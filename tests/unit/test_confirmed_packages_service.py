from datetime import datetime, timedelta

from app.services import confirmed_packages_service as svc
from metrics import processors


def _base_metrics():
    return {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [{"id": "legacy-closed"}, {"id": "db-approved", "source_package_id": "pkg-db"}],
                "confirmed_today": [{"id": "db-approved", "source_package_id": "pkg-db", "confirmed_at": "2026-04-06T13:42:29+00:00"}],
                "rejected_today": [],
            },
            "packages_summary_confirmed": {
                "today": 1,
                "yesterday": 0,
                "last_7_days": [1, 0, 0, 0, 0, 0, 0],
                "avg_7_days": 0.14,
                "same_weekday_last_week": 0,
            },
        }
    }


def test_merge_confirmed_into_metrics_preserves_existing_postgres_packages(monkeypatch):
    """F-051: merge_confirmed_into_metrics no longer injects JSON packages.
    It only removes duplicates from closed_today and updates the summary count.
    save_confirmed_packages is a no-op now (data lives in Postgres)."""
    now = datetime(2026, 4, 6, 12, 0, 0)
    fake_dates = {
        "now": now,
        "today_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "yesterday_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1),
        "yesterday_end": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7),
        "day24h_start": now - timedelta(hours=24),
    }
    monkeypatch.setattr(processors, "get_date_range", lambda: fake_dates)

    # save_confirmed_packages is now a no-op
    svc.save_confirmed_packages([{"id": "legacy-approved", "source_package_id": "pkg-legacy"}])

    merged = svc.merge_confirmed_into_metrics(_base_metrics())
    confirmed = merged["votos"]["packages"]["confirmed_today"]

    # Only the DB package (pkg-db) remains — JSON packages no longer injected
    assert [row["source_package_id"] for row in confirmed] == ["pkg-db"]
    # db-approved should be removed from closed_today because it's in confirmed_today
    assert all(row.get("id") != "db-approved" for row in merged["votos"]["packages"]["closed_today"])
    # Summary updated to count confirmed_today
    assert merged["votos"]["packages_summary_confirmed"]["today"] == 1
