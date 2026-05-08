from app.services import recent_image_cache as svc


def test_recent_image_cache_uses_runtime_state_in_supabase_mode(monkeypatch):
    cache = {}

    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: True)
    monkeypatch.setattr(svc, "load_runtime_state", lambda key: dict(cache))
    monkeypatch.setattr(svc, "save_runtime_state", lambda key, value: cache.update(value))
    monkeypatch.setattr(
        svc,
        "_utc_now",
        lambda: svc.datetime(2026, 3, 30, 16, 30, 0, tzinfo=svc.timezone.utc),
    )

    svc.remember_recent_image(
        chat_id="group-1",
        message_id="msg-1",
        media_id="media-1",
        occurred_at="2026-03-30T16:00:00+00:00",
    )

    item = svc.find_recent_image(chat_id="group-1", poll_ts=svc._parse_timestamp("2026-03-30T16:10:00+00:00"))

    assert item is not None
    assert item["media_id"] == "media-1"
    assert "group-1" in cache
