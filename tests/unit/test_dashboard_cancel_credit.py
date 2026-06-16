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
