from fastapi.testclient import TestClient
from fastapi import HTTPException

import main as main_module


def test_api_metrics_returns_error_when_file_missing(monkeypatch):
    def mock_load():
        raise FileNotFoundError("Metrics file not found")

    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(main_module, "_load_metrics", mock_load)

    client = TestClient(main_module.app)
    response = client.get("/api/metrics")

    assert response.status_code == 404
    assert "not found" in response.json().get("detail", "").lower()


def test_api_refresh_success_returns_generated_payload(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(main_module, "_refresh_lock", main_module.asyncio.Lock())

    expected = {"generated_at": "2026-01-01T00:00:00", "enquetes": {"today": 1}, "votos": {"today": 2}}

    async def mock_gen():
        return expected

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", mock_gen)

    async def mock_sync():
        return 0

    monkeypatch.setattr("app.services.payment_sync_service.sync_mercadopago_payments", mock_sync)

    client = TestClient(main_module.app)
    response = client.post("/api/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"] == expected


def test_api_refresh_returns_busy_payload_when_locked(monkeypatch):
    class LockedOnly:
        def locked(self):
            return True

    monkeypatch.setattr(main_module, "_refresh_lock", LockedOnly())
    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(main_module, "_load_metrics", lambda: {"generated_at": "now", "enquetes": {}, "votos": {}})

    client = TestClient(main_module.app)
    response = client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json()["status"] == "busy"


def test_api_refresh_returns_error_payload_on_exception(monkeypatch):
    monkeypatch.setattr(main_module, "_refresh_lock", main_module.asyncio.Lock())

    async def raise_error():
        raise ValueError("boom")

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", raise_error)

    client = TestClient(main_module.app)
    response = client.post("/api/refresh")

    assert response.status_code == 500
    assert "Error generating metrics" in response.json().get("detail", "")


def test_startup_does_not_start_payment_queue_worker(monkeypatch):
    """F-035: o worker de fila de cobrança foi desativado. A cobrança roda
    via portal do cliente. Garante que startup_backfill_routing_once NÃO
    chama start_payment_queue_worker — se alguém reativar sem motivo, o
    teste quebra e força revisão."""
    called = {"started": False}

    async def _fake_start_payment_queue_worker():
        called["started"] = True

    def _raise_not_found():
        raise HTTPException(status_code=404, detail="Metrics file not found")

    monkeypatch.setattr(
        "app.services.payment_queue_service.start_payment_queue_worker",
        _fake_start_payment_queue_worker,
    )
    monkeypatch.setattr(main_module, "_load_metrics", _raise_not_found)
    monkeypatch.setattr(main_module, "_is_supabase_metrics_mode", lambda: False)

    main_module.asyncio.run(main_module.startup_backfill_routing_once())

    assert called["started"] is False
