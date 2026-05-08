"""Tests for rejected packages external persistence/merge service."""
from datetime import datetime, timedelta

from app.services import rejected_packages_service as svc
from metrics import processors


def _base_metrics():
    return {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [{"id": "pkg-1"}, {"id": "pkg-2"}],
                "confirmed_today": [{"id": "pkg-3"}],
                "rejected_today": [],
            }
        }
    }


def test_add_rejected_package_is_idempotent(tmp_path, monkeypatch):
    rejected_file = tmp_path / "rejected_packages.json"
    monkeypatch.setattr(svc, "REJECTED_FILE", rejected_file)

    pkg = {"id": "pkg-1", "status": "rejected", "rejected": True}
    svc.add_rejected_package(pkg)
    svc.add_rejected_package(pkg)

    items = svc.load_rejected_packages()
    assert len(items) == 1
    assert items[0]["id"] == "pkg-1"


def test_merge_rejected_into_metrics_sets_list_and_removes_duplicates(monkeypatch, tmp_path):
    rejected_file = tmp_path / "rejected_packages.json"
    monkeypatch.setattr(svc, "REJECTED_FILE", rejected_file)

    now = datetime(2026, 3, 23, 12, 0, 0)
    fake_dates = {
        "now": now,
        "today_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "yesterday_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1),
        "yesterday_end": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7),
        "day24h_start": now - timedelta(hours=24),
    }
    monkeypatch.setattr(processors, "get_date_range", lambda: fake_dates)

    pkg_rejected = {"id": "pkg-1", "status": "rejected", "rejected": True, "rejected_at": now.isoformat()}
    svc.save_rejected_packages([pkg_rejected])

    data = _base_metrics()
    merged = svc.merge_rejected_into_metrics(data)

    rejected = merged["votos"]["packages"]["rejected_today"]
    assert len(rejected) == 1
    assert rejected[0]["id"] == "pkg-1"
    assert all(p["id"] != "pkg-1" for p in merged["votos"]["packages"]["closed_today"])
    assert all(p["id"] != "pkg-1" for p in merged["votos"]["packages"]["confirmed_today"])


def test_merge_rejected_into_metrics_preserves_existing_postgres_rejections(monkeypatch, tmp_path):
    rejected_file = tmp_path / "rejected_packages.json"
    monkeypatch.setattr(svc, "REJECTED_FILE", rejected_file)

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

    svc.save_rejected_packages(
        [
            {
                "id": "pkg-legacy",
                "source_package_id": "legacy-rejected",
                "rejected_at": now.isoformat(),
            }
        ]
    )

    data = {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [
                    {"id": "pkg-closed"},
                    {"id": "pkg-db", "source_package_id": "db-rejected"},
                ],
                "confirmed_today": [],
                "rejected_today": [
                    {"id": "pkg-db", "source_package_id": "db-rejected", "rejected_at": now.isoformat()}
                ],
            }
        }
    }

    merged = svc.merge_rejected_into_metrics(data)
    rejected = merged["votos"]["packages"]["rejected_today"]

    assert [row["source_package_id"] for row in rejected] == ["db-rejected", "legacy-rejected"]
    assert all((row.get("source_package_id") or row.get("id")) != "db-rejected" for row in merged["votos"]["packages"]["closed_today"])

