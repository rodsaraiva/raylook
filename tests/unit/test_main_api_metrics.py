"""Tests for GET /api/metrics and general API contract."""
import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import main as main_module
from app.services import metrics_service

def test_api_metrics_returns_saved_data(monkeypatch):
    expected = {"generated_at": "2026-02-15T12:00:00", "enquetes": {}, "votos": {}}

    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(main_module, "_load_metrics", lambda: expected)

    client = TestClient(main_module.app)
    response = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.json() == expected


def test_api_metrics_handles_corrupt_json(monkeypatch):
    def mock_load():
        raise json.JSONDecodeError("msg", "doc", 0)

    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(main_module, "_load_metrics", mock_load)

    client = TestClient(main_module.app, raise_server_exceptions=False)
    response = client.get("/api/metrics")

    # The API should catch the exception and return 500 (based on current main.py implementation)
    assert response.status_code == 500


def test_api_refresh_writes_file_that_api_metrics_can_read(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(main_module, "refresh_lock", main_module.asyncio.Lock())

    # Mock metrics_service
    monkeypatch.setattr(metrics_service, "METRICS_FILE", metrics_file)
    from app.storage import JsonFileStorage
    monkeypatch.setattr(metrics_service, "_storage", JsonFileStorage(metrics_file))

    payload = {"generated_at": "now", "enquetes": {"today": 5}, "votos": {"today": 10}}
    # Mock generate_metrics which is called by metrics_service.generate_and_persist_metrics
    # But generate_and_persist_metrics is async and runs generate_metrics in a thread.
    # It's easier to mock generate_and_persist_metrics directly.
    
    async def mock_gen():
        metrics_file.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", mock_gen)
    monkeypatch.setattr(metrics_service, "load_metrics", lambda: payload)

    # Mock payment_sync_service to avoid network calls during tests
    from app.services import payment_sync_service
    async def mock_sync(): return 0
    monkeypatch.setattr(payment_sync_service, "sync_mercadopago_payments", mock_sync)

    client = TestClient(main_module.app)

    # Refresh writes the file
    r1 = client.post("/api/refresh")
    assert r1.status_code == 200
    assert r1.json()["status"] == "success"

    # Metrics reads it back
    r2 = client.get("/api/metrics")
    assert r2.json() == payload


def test_api_root_returns_html(monkeypatch):
    client = TestClient(main_module.app)
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_stream_versions_uses_runtime_state_metadata(tmp_path, monkeypatch):
    metrics_file = tmp_path / "dashboard_metrics.json"
    metrics_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))
    monkeypatch.setattr(
        main_module,
        "load_runtime_state_metadata",
        lambda keys: {
            "dashboard_metrics": {"updated_at": "2026-04-01T00:00:01+00:00"},
            "finance_charges": {"updated_at": "2026-04-01T00:00:02+00:00"},
            "finance_stats": {"updated_at": "2026-04-01T00:00:03+00:00"},
            "customer_rows": {"updated_at": "2026-04-01T00:00:04+00:00"},
        },
    )

    versions = main_module._dashboard_stream_versions()

    assert versions["dashboard"] == "2026-04-01T00:00:01+00:00"
    assert versions["finance"] == "2026-04-01T00:00:03+00:00"
    assert versions["customers"] == "2026-04-01T00:00:04+00:00"
