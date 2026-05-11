"""Testes do router app/routers/finance.py (GET /api/finance/charges e /api/finance/stats)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import finance as finance_router_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(finance_router_module.router)
    return app


def _today_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


SAMPLE_CHARGES: List[Dict[str, Any]] = [
    {
        "id": "c1",
        "status": "paid",
        "total_amount": 200.0,
        "created_at": _today_iso(),
    },
    {
        "id": "c2",
        "status": "pending",
        "total_amount": 150.0,
        "created_at": _today_iso(),
    },
    {
        "id": "c3",
        "status": "enviando",
        "total_amount": 100.0,
        "created_at": _days_ago_iso(8),  # fora da janela de 7 dias
    },
]


@pytest.fixture
def client_with_charges():
    """Client com lista de cobranças pré-carregada."""
    app = _make_app()
    with patch.object(finance_router_module.finance_manager, "list_charges", return_value=SAMPLE_CHARGES):
        yield TestClient(app)


@pytest.fixture
def client_empty():
    """Client sem cobranças."""
    app = _make_app()
    with patch.object(finance_router_module.finance_manager, "list_charges", return_value=[]):
        yield TestClient(app)


@pytest.fixture
def client_raises():
    """Client cujo manager lança exceção."""
    app = _make_app()

    def _boom():
        raise RuntimeError("banco fora")

    with patch.object(finance_router_module.finance_manager, "list_charges", side_effect=_boom):
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/finance/charges
# ---------------------------------------------------------------------------

def test_charges_returns_200_with_list(client_with_charges):
    res = client_with_charges.get("/api/finance/charges")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 3


def test_charges_empty_list(client_empty):
    res = client_empty.get("/api/finance/charges")
    assert res.status_code == 200
    assert res.json() == []


def test_charges_returns_500_on_exception(client_raises):
    res = client_raises.get("/api/finance/charges")
    assert res.status_code == 500
    assert "error" in res.json()


def test_charges_error_body_contains_message(client_raises):
    res = client_raises.get("/api/finance/charges")
    assert "banco fora" in res.json()["error"]


# ---------------------------------------------------------------------------
# GET /api/finance/stats
# ---------------------------------------------------------------------------

def test_stats_returns_expected_keys(client_with_charges):
    res = client_with_charges.get("/api/finance/stats")
    assert res.status_code == 200
    body = res.json()
    for key in ("timeline", "total_pending", "total_paid", "total_charges"):
        assert key in body


def test_stats_totals_correct(client_with_charges):
    res = client_with_charges.get("/api/finance/stats")
    body = res.json()
    # c1 paid=200, c2 pending=150
    assert body["total_paid"] == 200.0
    assert body["total_pending"] == 150.0
    assert body["total_charges"] == 3


def test_stats_timeline_has_7_days(client_with_charges):
    res = client_with_charges.get("/api/finance/stats")
    body = res.json()
    assert len(body["timeline"]) == 7


def test_stats_timeline_keys_format(client_with_charges):
    """Chaves devem estar no formato dd/mm."""
    res = client_with_charges.get("/api/finance/stats")
    body = res.json()
    for key in body["timeline"]:
        parts = key.split("/")
        assert len(parts) == 2
        assert parts[0].isdigit() and parts[1].isdigit()


def test_stats_empty_charges(client_empty):
    res = client_empty.get("/api/finance/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_paid"] == 0
    assert body["total_pending"] == 0
    assert body["total_charges"] == 0


def test_stats_today_paid_accumulated_in_timeline(client_with_charges):
    """Cobranças de hoje devem aparecer no último slot do timeline."""
    res = client_with_charges.get("/api/finance/stats")
    body = res.json()
    today_key = datetime.now().strftime("%d/%m")
    slot = body["timeline"].get(today_key)
    assert slot is not None
    assert slot["paid"] == 200.0
    assert slot["created"] >= 350.0  # 200 + 150


def test_stats_charge_outside_7_days_excluded(client_with_charges):
    """Cobrança com 8 dias atrás não deve aparecer no timeline."""
    res = client_with_charges.get("/api/finance/stats")
    body = res.json()
    total_in_timeline = sum(v["created"] for v in body["timeline"].values())
    # c3 (100.0) tem 8 dias atrás, fora da janela
    assert total_in_timeline == pytest.approx(200.0 + 150.0)


def test_stats_invalid_created_at_skipped():
    """Cobranças com created_at inválido não devem quebrar o endpoint."""
    bad_charges = [{"id": "x", "status": "paid", "total_amount": 50.0, "created_at": "nao-e-data"}]
    app = _make_app()
    with patch.object(finance_router_module.finance_manager, "list_charges", return_value=bad_charges):
        client = TestClient(app)
        res = client.get("/api/finance/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_paid"] == 50.0
    assert body["total_charges"] == 1


def test_stats_charge_missing_created_at_skipped():
    """Cobranças sem created_at não devem aparecer no timeline."""
    charges = [{"id": "y", "status": "pending", "total_amount": 75.0}]
    app = _make_app()
    with patch.object(finance_router_module.finance_manager, "list_charges", return_value=charges):
        client = TestClient(app)
        res = client.get("/api/finance/stats")
    assert res.status_code == 200
    body = res.json()
    total_in_timeline = sum(v["created"] for v in body["timeline"].values())
    assert total_in_timeline == 0.0
    assert body["total_pending"] == 75.0


def test_stats_returns_500_on_exception(client_raises):
    res = client_raises.get("/api/finance/stats")
    assert res.status_code == 500
    assert "error" in res.json()
