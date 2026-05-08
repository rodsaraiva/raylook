"""Tests for package confirm/reject API endpoints."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

import main as main_module


def _make_metrics_with_package(pkg_id: str):
    return {
        "generated_at": "2026-01-01T00:00:00",
        "enquetes": {"today": 1},
        "votos": {
            "today": 2,
            "packages": {
                "open": [],
                "closed_today": [
                    {"id": pkg_id, "poll_title": "Test Poll", "qty": 24, "closed_at": "2026-01-01T12:00:00", "votes": []},
                ],
                "closed_week": [],
                "confirmed_today": [],
            },
        },
    }

def _make_metrics_after_confirm(pkg_id: str):
    return {
        "generated_at": "2026-01-01T00:00:00",
        "enquetes": {"today": 1},
        "votos": {
            "today": 2,
            "packages": {
                "open": [],
                "closed_today": [],
                "closed_week": [],
                "confirmed_today": [
                    {"id": pkg_id, "poll_title": "Test Poll", "qty": 24, "closed_at": "2026-01-01T12:00:00", "votes": [], "confirmed_at": "2026-01-01T12:00:00"},
                ],
            },
        },
    }

def test_confirm_package_success(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())

    data = _make_metrics_with_package("poll_0")
    metrics_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    client = TestClient(main_module.app)
    # mock load metrics so that it actually returns the file we just wrote instead of running real ones
    monkeypatch.setattr(main_module, "_load_metrics", lambda: _make_metrics_with_package("poll_0"))
    
    # We must mock the service layer completely so it doesn't read other files on disk when tests run
    from app.services import metrics_service
    monkeypatch.setattr(metrics_service, "load_metrics", lambda: _make_metrics_after_confirm("poll_0"))
    
    response = client.post("/api/packages/poll_0/confirm")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["moved"]["from"] == "closed_today"
    assert body["moved"]["to"] == "confirmed_packages"
    assert len(body["data"]["votos"]["packages"]["closed_today"]) == 0
    assert len(body["data"]["votos"]["packages"]["confirmed_today"]) == 1
    assert body["data"]["votos"]["packages"]["confirmed_today"][0]["id"] == "poll_0"


def test_confirm_package_not_found(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())

    data = _make_metrics_with_package("poll_0")
    metrics_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    client = TestClient(main_module.app)
    response = client.post("/api/packages/unknown_id/confirm")

    assert response.status_code == 404
    assert "não encontrado" in response.json().get("detail", "").lower()


def test_reject_package_success(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())
    # Force legacy path (no supabase resolution)
    monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _pkg_id: None)

    # Mock _load_metrics and _save_metrics to use in-memory data
    metrics_data = _make_metrics_with_package("poll_0")
    monkeypatch.setattr(main_module, "_load_metrics", lambda: _make_metrics_with_package("poll_0"))
    monkeypatch.setattr(main_module, "_save_metrics", lambda _data: None)

    from app.services import metrics_service
    def _fake_load_metrics():
        d = _make_metrics_with_package("poll_0")
        d["votos"]["packages"]["closed_today"] = []
        d["votos"]["packages"]["rejected_today"] = [
            {"id": "poll_0", "poll_title": "Test Poll", "qty": 24, "closed_at": "2026-01-01T12:00:00", "votes": [], "rejected": True}
        ]
        return d

    monkeypatch.setattr(metrics_service, "load_metrics", _fake_load_metrics)

    client = TestClient(main_module.app)
    response = client.post("/api/packages/poll_0/reject")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["moved"]["rejected"] is True
    rejected = body["data"]["votos"]["packages"].get("rejected_today", [])
    assert len(rejected) == 1
    assert rejected[0]["id"] == "poll_0"
    assert rejected[0]["rejected"] is True

    closed = body["data"]["votos"]["packages"].get("closed_today", [])
    assert len(closed) == 0



def test_reject_package_persists_in_rejected_store(monkeypatch):
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())
    # Force legacy path (no supabase resolution)
    monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _pkg_id: None)

    # Mock _load_metrics and _save_metrics to use in-memory data
    monkeypatch.setattr(main_module, "_load_metrics", lambda: _make_metrics_with_package("poll_0"))
    monkeypatch.setattr(main_module, "_save_metrics", lambda _data: None)

    captured = {}

    def _fake_add_rejected_package(pkg):
        captured["pkg"] = pkg

    import app.services.rejected_packages_service as rejected_service
    monkeypatch.setattr(rejected_service, "add_rejected_package", _fake_add_rejected_package)

    client = TestClient(main_module.app)
    response = client.post("/api/packages/poll_0/reject")

    assert response.status_code == 200
    body = response.json()
    assert body["moved"]["package"] is not None
    assert body["moved"]["package"]["id"] == "poll_0"
    assert captured["pkg"]["id"] == "poll_0"


def test_revert_package_success(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())

    # prepare metrics where package is already confirmed
    data = _make_metrics_with_package("poll_0")
    # move to confirmed
    pkg = data["votos"]["packages"]["closed_today"].pop(0)
    pkg["confirmed_at"] = "2026-02-19T10:00:00"
    data["votos"]["packages"]["confirmed_today"] = [pkg]
    metrics_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    client = TestClient(main_module.app)
    response = client.post("/api/packages/poll_0/revert")

    # Revert is no longer allowed, returns 403
    assert response.status_code == 403


def test_confirm_package_supabase_runs_post_confirmation_pipeline(monkeypatch):
    """F-051: supabase confirm path now directly calls SalesService.approve_package,
    generate_and_persist_metrics, and kicks off background pdf_worker + payments_worker.
    run_post_confirmation_effects is only used by the legacy path."""
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())
    monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _pkg_id: "sb-pkg-1")

    class _FakeSalesService:
        def __init__(self, _client):
            pass

        def approve_package(self, pacote_id):
            assert pacote_id == "sb-pkg-1"
            return {"pacote_id": pacote_id, "status": "approved"}

    monkeypatch.setattr(main_module, "SalesService", _FakeSalesService)
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: object()))

    async def _fake_generate_and_persist_metrics():
        return {
            "votos": {
                "packages": {
                    "open": [],
                    "closed_today": [],
                    "closed_week": [],
                    "confirmed_today": [
                        {
                            "id": "poll_0",
                            "source_package_id": "sb-pkg-1",
                            "poll_title": "Test Poll",
                            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 12}],
                        }
                    ],
                }
            }
        }

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", _fake_generate_and_persist_metrics)
    monkeypatch.setattr(main_module, "load_customers", lambda: {})

    # Mock background workers to avoid real I/O (imported inside the endpoint)
    async def _noop_pdf(_pkg): pass
    async def _noop_payments(_pkg, concurrency=5): pass
    monkeypatch.setattr("app.workers.background.pdf_worker", _noop_pdf)
    monkeypatch.setattr("app.workers.background.payments_worker", _noop_payments)

    client = TestClient(main_module.app)
    response = client.post("/api/packages/poll_0/confirm")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["mode"] == "supabase"
    assert body["moved"]["source_package_id"] == "sb-pkg-1"
    assert body["data"]["votos"]["packages"]["confirmed_today"][0]["source_package_id"] == "sb-pkg-1"


def test_confirm_package_supabase_uses_snapshot_fallback_when_metrics_lag(monkeypatch):
    """F-051: _load_supabase_package_snapshot was removed. The supabase confirm path
    now always calls generate_and_persist_metrics after approval. If the package
    doesn't appear in confirmed_today (metrics lag), the endpoint still succeeds
    because SalesService.approve_package already persisted the approval in Postgres."""
    monkeypatch.setattr(main_module, "packages_lock", main_module.asyncio.Lock())
    monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _pkg_id: "sb-pkg-1")

    class _FakeSalesService:
        def __init__(self, _client):
            pass

        def approve_package(self, pacote_id):
            assert pacote_id == "sb-pkg-1"
            return {"pacote_id": pacote_id, "status": "approved"}

    monkeypatch.setattr(main_module, "SalesService", _FakeSalesService)
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: object()))

    async def _fake_generate_and_persist_metrics():
        # Simulate metrics lag: confirmed_today is empty even after approval
        return {
            "votos": {
                "packages": {
                    "open": [],
                    "closed_today": [],
                    "closed_week": [],
                    "confirmed_today": [],
                }
            }
        }

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", _fake_generate_and_persist_metrics)
    monkeypatch.setattr(main_module, "load_customers", lambda: {})

    # Mock background workers (imported inside the endpoint)
    async def _noop_pdf(_pkg): pass
    async def _noop_payments(_pkg, concurrency=5): pass
    monkeypatch.setattr("app.workers.background.pdf_worker", _noop_pdf)
    monkeypatch.setattr("app.workers.background.payments_worker", _noop_payments)

    client = TestClient(main_module.app)
    response = client.post("/api/packages/poll_0/confirm", json={"tag": "Teste"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["mode"] == "supabase"
    assert body["moved"]["source_package_id"] == "sb-pkg-1"


def test_get_confirmed_package_edit_data_success(monkeypatch):
    pkg = {
        "id": "poll_0",
        "poll_id": "poll-A",
        "poll_title": "Test Poll",
        "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 12}],
    }
    confirmed_packages = [
        pkg,
        {
            "id": "poll_1",
            "poll_id": "poll-A",
            "votes": [{"phone": "5511999990002", "name": "Bia", "qty": 12}],
        },
    ]
    votos_rows = [
        {"phone": "5511999990001", "name": "Ana", "qty": 12},
        {"phone": "5511999990002", "name": "Bia", "qty": 12},
        {"phone": "5511999990003", "name": "Cris", "qty": 12},
    ]

    import app.services.confirmed_packages_service as confirmed_service
    monkeypatch.setattr(confirmed_service, "get_confirmed_package", lambda pkg_id: pkg if pkg_id == "poll_0" else None)
    monkeypatch.setattr(confirmed_service, "load_confirmed_packages", lambda: confirmed_packages)

    # Mock the active votes fetcher (supabase path)
    monkeypatch.setattr(main_module, "_fetch_active_votes_for_poll", lambda _poll_id: votos_rows)

    client = TestClient(main_module.app)
    response = client.get("/api/packages/poll_0/edit-data")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert [v["phone"] for v in body["data"]["selected_votes"]] == ["5511999990001"]
    assert [v["phone"] for v in body["data"]["available_votes"]] == ["5511999990003"]


def test_update_confirmed_package_blocks_total_different_from_24(monkeypatch):
    pkg = {
        "id": "poll_0",
        "poll_id": "poll-A",
        "poll_title": "Test Poll",
        "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 12}],
    }
    import app.services.confirmed_packages_service as confirmed_service
    monkeypatch.setattr(confirmed_service, "get_confirmed_package", lambda _pkg_id: pkg)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/packages/poll_0/update-confirmed",
        json={"votes": [{"phone": "5511999990001", "name": "Ana", "qty": 23}]},
    )

    assert response.status_code == 400
    assert "24" in response.json().get("detail", "")


def test_update_confirmed_package_reconciles_charges(monkeypatch):
    pkg = {
        "id": "poll_0",
        "poll_id": "poll-A",
        "poll_title": "Test Poll",
        "valor_col": 10,
        "votes": [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
            {"phone": "5511999990002", "name": "Bia", "qty": 12},
        ],
    }

    saved_package = {}
    removed_from_queue = {}

    import app.services.confirmed_packages_service as confirmed_service
    monkeypatch.setattr(confirmed_service, "get_confirmed_package", lambda _pkg_id: dict(pkg))
    monkeypatch.setattr(confirmed_service, "add_confirmed_package", lambda p: saved_package.update({"pkg": p}))

    import app.services.metrics_service as metrics_service
    monkeypatch.setattr(metrics_service, "load_metrics", lambda: {"votos": {"packages": {"confirmed_today": []}}})

    import app.services.payment_queue_service as queue_service
    monkeypatch.setattr(queue_service, "remove_open_jobs_for_charge_ids", lambda ids: removed_from_queue.update({"ids": ids}))
    monkeypatch.setattr(queue_service, "enqueue_whatsapp_job", lambda _payload: "job-1")

    monkeypatch.setattr("app.services.baserow_lookup.normalize_phone", lambda p: p)

    class _FakeFinanceManager:
        def list_charges(self):
            return [
                {"id": "c-ana", "package_id": "poll_0", "customer_phone": "5511999990001"},
            ]

        def delete_charge(self, charge_id):
            return charge_id == "c-ana"

        def register_package_confirmation(self, package):
            return [
                {
                    "id": "c-cris",
                    "package_id": package.get("id"),
                    "customer_phone": "5511999990003",
                    "customer_name": "Cris",
                    "quantity": 12,
                    "subtotal": 120,
                    "commission_percent": 13.0,
                    "poll_title": package.get("poll_title"),
                }
            ]

    monkeypatch.setattr(main_module, "FinanceManager", _FakeFinanceManager)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/packages/poll_0/update-confirmed",
        json={
            "votes": [
                {"phone": "5511999990002", "name": "Bia", "qty": 12},
                {"phone": "5511999990003", "name": "Cris", "qty": 12},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["summary"]["added_votes"] == 1
    assert body["summary"]["removed_votes"] == 1
    assert body["summary"]["created_charges"] == 1
    assert body["summary"]["deleted_charges"] == 1
    assert removed_from_queue["ids"] == ["c-ana"]
    assert [v["phone"] for v in saved_package["pkg"]["votes"]] == ["5511999990002", "5511999990003"]
