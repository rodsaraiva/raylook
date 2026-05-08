import pytest

from app.services import metrics_service


@pytest.mark.asyncio
async def test_generate_and_persist_metrics_updates_runtime_state_marker(monkeypatch):
    expected = {"generated_at": "2026-04-01T12:00:00+00:00", "enquetes": {}, "votos": {}}
    captured = {}

    monkeypatch.setattr("metrics.services.generate_metrics", lambda: expected)
    monkeypatch.setattr(metrics_service._storage, "load", lambda: None)
    monkeypatch.setattr(metrics_service._storage, "save", lambda data: None)
    monkeypatch.setattr("app.services.runtime_state_service.runtime_state_enabled", lambda: True)

    def fake_save_runtime_state(key, payload):
        captured["key"] = key
        captured["payload"] = payload
        return payload

    monkeypatch.setattr("app.services.runtime_state_service.save_runtime_state", fake_save_runtime_state)

    payload = await metrics_service.generate_and_persist_metrics()

    assert payload == expected
    assert captured["key"] == "dashboard_metrics"
    assert captured["payload"]["generated_at"] == expected["generated_at"]
