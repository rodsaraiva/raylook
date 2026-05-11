"""Testes do router app/routers/metrics.py.

Endpoints cobertos:
  GET  /           → HTML (index.html)
  GET  /api/metrics
  POST /api/refresh
  GET  /health
  GET  /ready
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from app.routers import metrics as metrics_router_module
from app.services import metrics_service


def _make_app(with_lock: bool = True) -> FastAPI:
    """Cria app de teste com o router de metrics incluído."""
    app = FastAPI()
    app.include_router(metrics_router_module.router)
    if with_lock:
        app.state.refresh_lock = asyncio.Lock()
    return app


SAMPLE_METRICS: Dict[str, Any] = {
    "generated_at": "2026-05-11T15:30:00+00:00",
    "enquetes": {"total": 3},
    "votos": {
        "packages": {
            "open": [],
            "closed": [],
        }
    },
}


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok():
    client = TestClient(_make_app())
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /ready
# ---------------------------------------------------------------------------

def test_ready_returns_true_when_file_exists():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", return_value=SAMPLE_METRICS):
        client = TestClient(app)
        res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["ready"] is True


def test_ready_returns_false_when_file_not_found():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", side_effect=FileNotFoundError):
        client = TestClient(app)
        res = client.get("/ready")
    assert res.status_code == 503
    assert res.json()["ready"] is False


def test_ready_returns_false_on_unexpected_exception():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", side_effect=RuntimeError("disco cheio")):
        client = TestClient(app)
        res = client.get("/ready")
    assert res.status_code == 503
    assert res.json()["ready"] is False


# ---------------------------------------------------------------------------
# GET /api/metrics
# ---------------------------------------------------------------------------

def test_get_metrics_returns_200_with_data():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", return_value=dict(SAMPLE_METRICS)):
        client = TestClient(app)
        res = client.get("/api/metrics")
    assert res.status_code == 200
    body = res.json()
    assert body["generated_at"] == SAMPLE_METRICS["generated_at"]


def test_get_metrics_injects_customers_map():
    app = _make_app()
    customers = {"5511999990001": "Ana"}
    with patch.object(metrics_service, "load_metrics", return_value=dict(SAMPLE_METRICS)):
        with patch("app.routers.metrics.load_customers", return_value=customers):
            client = TestClient(app)
            res = client.get("/api/metrics")
    assert res.status_code == 200
    assert res.json()["customers_map"] == customers


def test_get_metrics_ensures_confirmed_today_key():
    """Garante que confirmed_today é injetado mesmo se ausente nos dados."""
    data = {
        "generated_at": "now",
        "votos": {"packages": {}},  # sem confirmed_today
    }
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", return_value=data):
        with patch("app.routers.metrics.load_customers", return_value={}):
            client = TestClient(app)
            res = client.get("/api/metrics")
    assert res.status_code == 200
    assert res.json()["votos"]["packages"]["confirmed_today"] == []


def test_get_metrics_votos_not_dict_skips_injection():
    """Se votos não é dict, não deve quebrar."""
    data = {"generated_at": "now", "votos": [1, 2, 3]}
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", return_value=data):
        with patch("app.routers.metrics.load_customers", return_value={}):
            client = TestClient(app)
            res = client.get("/api/metrics")
    assert res.status_code == 200


def test_get_metrics_returns_404_when_file_not_found():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", side_effect=FileNotFoundError("sem arquivo")):
        client = TestClient(app)
        res = client.get("/api/metrics")
    assert res.status_code == 404
    assert "Metrics file not found" in res.json()["detail"]


def test_get_metrics_returns_500_on_generic_exception():
    app = _make_app()
    with patch.object(metrics_service, "load_metrics", side_effect=RuntimeError("crash")):
        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/api/metrics")
    assert res.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/refresh
# ---------------------------------------------------------------------------

def test_refresh_returns_success():
    async def _gen():
        return {"generated_at": "now", "votos": {}}

    app = _make_app()
    with patch.object(metrics_service, "generate_and_persist_metrics", side_effect=_gen):
        client = TestClient(app)
        res = client.post("/api/refresh")
    assert res.status_code == 200
    assert res.json()["status"] == "success"


def test_refresh_returns_data_in_body():
    payload = {"generated_at": "2026-05-11", "votos": {"total": 10}}

    async def _gen():
        return payload

    app = _make_app()
    with patch.object(metrics_service, "generate_and_persist_metrics", side_effect=_gen):
        client = TestClient(app)
        res = client.post("/api/refresh")
    assert res.json()["data"]["generated_at"] == "2026-05-11"


def test_refresh_returns_409_when_lock_held():
    """Se o lock já está adquirido, deve retornar 409."""
    app = _make_app()
    lock = asyncio.Lock()

    async def _gen():
        return {}

    app.state.refresh_lock = lock

    with patch.object(metrics_service, "generate_and_persist_metrics", side_effect=_gen):
        client = TestClient(app)
        # Adquirimos o lock manualmente para simular "em andamento"
        import threading

        result: dict = {}

        async def _hold_and_call():
            async with lock:
                # Com o lock adquirido, faz a chamada ao endpoint
                import httpx
                transport = client._transport  # type: ignore[attr-defined]
                req = client.post("/api/refresh")
                result["status"] = req.status_code

        # Rodamos num novo event loop para evitar conflito com o loop do TestClient
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_hold_and_call())
        loop.close()

    assert result["status"] == 409


def test_refresh_returns_500_on_exception():
    async def _fail():
        raise RuntimeError("geração falhou")

    app = _make_app()
    with patch.object(metrics_service, "generate_and_persist_metrics", side_effect=_fail):
        client = TestClient(app, raise_server_exceptions=False)
        res = client.post("/api/refresh")
    assert res.status_code == 500


# ---------------------------------------------------------------------------
# GET / (HTML root)
# ---------------------------------------------------------------------------

def test_root_returns_html():
    """O endpoint raiz deve retornar HTML via template."""
    app = _make_app()
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
