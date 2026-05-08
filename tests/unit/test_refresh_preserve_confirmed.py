"""Test that /api/refresh preserves confirmed_today from metrics generation."""
from fastapi.testclient import TestClient

import main as main_module


def test_refresh_preserves_confirmed_today(monkeypatch):
    """F-051: confirmed packages come from Postgres via generate_and_persist_metrics.
    The refresh endpoint should return them in the response."""
    monkeypatch.setattr(main_module, "refresh_lock", main_module.asyncio.Lock())

    confirmed_pkg = {
        "id": "pkg_confirmed_1",
        "poll_title": "X",
        "qty": 5,
        "confirmed_at": "2026-02-19T10:00:00",
        "votes": [],
    }

    async def mock_gen():
        return {
            "generated_at": "now2",
            "enquetes": {},
            "votos": {
                "today": 2,
                "packages": {
                    "open": [],
                    "closed_today": [],
                    "closed_week": [],
                    "confirmed_today": [confirmed_pkg],
                },
            },
        }

    monkeypatch.setattr(main_module, "generate_and_persist_metrics", mock_gen)

    # Mock the supabase mode so it goes through the supabase branch
    monkeypatch.setattr(main_module, "_is_supabase_metrics_mode", lambda: True)
    monkeypatch.setattr(main_module, "refresh_finance_dashboard_stats", lambda: None)
    monkeypatch.setattr(main_module, "refresh_customer_rows_snapshot", lambda: None)
    monkeypatch.setattr(main_module, "load_customers", lambda: {})

    client = TestClient(main_module.app)
    r = client.post("/api/refresh")
    assert r.status_code == 200
    data = r.json()["data"]

    confirmed = data.get("votos", {}).get("packages", {}).get("confirmed_today", [])
    assert any(p["id"] == "pkg_confirmed_1" for p in confirmed)
