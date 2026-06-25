"""Testes do wiring de crédito no cancelamento/avanço do dashboard ativo.

Bug 1: POST /api/dashboard/packages/{id}/cancel deve delegar a
package_cancellation_service.cancel_package (que gera crédito), não fazer
um flip de status. Bug 2: confirm_debit deve rodar nos caminhos admin que
marcam pagamento como pago.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import dashboard as dashboard_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dashboard_module.router)
    return app


def _silence_snapshots(monkeypatch):
    """Os refresh de snapshot são lazy-imports dentro do endpoint — no-op."""
    import app.services.finance_service as fs
    import app.services.customer_service as cs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)


def test_cancel_delegates_to_service_and_returns_credit(monkeypatch):
    _silence_snapshots(monkeypatch)
    called = {}

    def fake_cancel(package_id, force=False, cancelled_by=None):
        called["args"] = (package_id, force, cancelled_by)
        return {"cancelled_sales": 2, "credited_clients": 1, "credited_total": 300.0}

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post("/api/dashboard/packages/PKG-1/cancel", json={})

    assert resp.status_code == 200
    assert called["args"] == ("PKG-1", False, "admin")
    body = resp.json()
    assert body["new_state"] == "cancelled"
    assert body["credited_clients"] == 1
    assert body["credited_total"] == 300.0


def test_cancel_blocked_when_paid_clients(monkeypatch):
    _silence_snapshots(monkeypatch)
    from app.services.package_cancellation_service import PackageCancelBlocked

    def fake_cancel(package_id, force=False, cancelled_by=None):
        raise PackageCancelBlocked([
            {"cliente_nome": "Ana", "total_amount": 150.0, "pagamento_id": "PG1"},
        ])

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post("/api/dashboard/packages/PKG-1/cancel", json={})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "blocked_paid"
    assert body["paid_count"] == 1
    assert body["paid_clients"][0]["cliente_nome"] == "Ana"


def test_cancel_returns_404_when_package_not_found(monkeypatch):
    _silence_snapshots(monkeypatch)
    from app.services.package_cancellation_service import PackageNotFound

    def fake_cancel(package_id, force=False, cancelled_by=None):
        raise PackageNotFound(package_id)

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post("/api/dashboard/packages/NOPE/cancel", json={})
    assert resp.status_code == 404


def test_advance_confirmado_confirms_credit_debit(monkeypatch):
    confirmed = []
    monkeypatch.setattr(
        dashboard_module.credit_service,
        "confirm_debit",
        lambda **kw: confirmed.append(kw.get("pagamento_id")),
    )

    fake = MagicMock()
    pkg = {"id": "PKG-1", "status": "approved"}
    vendas = [{"id": "V1"}, {"id": "V2"}]
    pags = [{"id": "PG1", "status": "sent"}, {"id": "PG2", "status": "sent"}]

    def fake_select(table, **kwargs):
        if table == "pacotes":
            return pkg
        if table == "vendas":
            return vendas
        if table == "pagamentos":
            return pags
        if table == "pacote_clientes":
            return []
        return None

    fake.select.side_effect = fake_select
    fake.now_iso.return_value = "2026-06-16T00:00:00Z"

    monkeypatch.setattr(
        dashboard_module.SupabaseRestClient, "from_settings", staticmethod(lambda: fake)
    )
    monkeypatch.setattr(dashboard_module, "_derive_state", lambda *a, **k: "confirmado")

    req = SimpleNamespace(state=SimpleNamespace(role="admin"))
    asyncio.run(dashboard_module.advance_package("PKG-1", req, to=None))

    assert confirmed == ["PG1", "PG2"]


def test_mark_client_paid_confirms_credit_debit(monkeypatch):
    confirmed = []
    monkeypatch.setattr(
        dashboard_module.credit_service,
        "confirm_debit",
        lambda **kw: confirmed.append(kw.get("pagamento_id")),
    )

    fake = MagicMock()

    def fake_select(table, **kwargs):
        if table == "pacote_clientes":
            return {"id": "PC1"}
        if table == "vendas":
            return {"id": "V1"}
        if table == "pagamentos":
            return {"id": "PG9", "status": "sent"}
        return None

    fake.select.side_effect = fake_select
    fake.now_iso.return_value = "2026-06-16T00:00:00Z"
    monkeypatch.setattr(
        dashboard_module.SupabaseRestClient, "from_settings", staticmethod(lambda: fake)
    )

    # mark_client_paid é SYNC e NÃO recebe request — chamar com (pacote_id, cliente_id)
    dashboard_module.mark_client_paid("PKG-1", "CLI-1")

    assert confirmed == ["PG9"]
