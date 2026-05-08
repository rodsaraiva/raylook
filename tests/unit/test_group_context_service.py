from app.services import group_context_service as svc


def test_monitor_floor_is_disabled_when_metrics_min_date_is_configured(monkeypatch):
    monkeypatch.setattr(svc.settings, "TEST_MODE", True)
    monkeypatch.setattr(svc.settings, "TEST_GROUP_CHAT_ID", "120363403901156886@g.us")
    monkeypatch.setattr(svc.settings, "METRICS_MIN_DATE", "2026-03-27T00:00:00-03:00")
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: True)

    load_calls = []
    save_calls = []

    monkeypatch.setattr(svc, "load_runtime_state", lambda key: load_calls.append(key) or {})
    monkeypatch.setattr(svc, "save_runtime_state", lambda key, payload: save_calls.append((key, payload)))

    assert svc.get_test_group_monitor_started_at() is None
    assert load_calls == []
    assert save_calls == []
