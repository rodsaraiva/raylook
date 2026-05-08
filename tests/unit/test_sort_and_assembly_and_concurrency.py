"""Tests for sorting, package assembly invariants and concurrency on package endpoints."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from metrics import processors
from metrics.processors import VoteProcessor, parse_timestamp
import main as main_module


def test_sort_key_prioritizes_qty_then_timestamp():
    now = datetime.now()
    votes = [
        {"id": 1, "parsed_qty": 3, "timestamp": (now - timedelta(minutes=5)).isoformat()},
        {"id": 2, "parsed_qty": 5, "timestamp": (now - timedelta(minutes=10)).isoformat()},
        {"id": 3, "parsed_qty": 5, "timestamp": (now - timedelta(minutes=1)).isoformat()},
        {"id": 4, "parsed_qty": 2, "timestamp": (now - timedelta(minutes=2)).isoformat()},
    ]

    def sort_key(v):
        qty = v.get("parsed_qty", 0)
        ts = parse_timestamp(v.get("timestamp")) or datetime.min
        return (-int(qty), ts)

    sorted_votes = sorted(votes, key=sort_key)
    # Expect qty 5 votes first, earlier timestamp first, then qty 3, then qty 2
    assert [v["id"] for v in sorted_votes] == [2, 3, 1, 4]


def test_package_assembly_preserves_total_qty_and_limits():
    vp = VoteProcessor()
    poll_id = "poll_test"
    # create votes with various quantities and timestamps
    now = datetime.now().isoformat()
    votes = [
        {"pollId": poll_id, "voterPhone": "111", "qty": "10", "timestamp": now},
        {"pollId": poll_id, "voterPhone": "222", "qty": "8", "timestamp": now},
        {"pollId": poll_id, "voterPhone": "333", "qty": "6", "timestamp": now},
        {"pollId": poll_id, "voterPhone": "444", "qty": "4", "timestamp": now},
        {"pollId": poll_id, "voterPhone": "555", "qty": "2", "timestamp": now},
    ]
    total_qty = 0
    for v in votes:
        vp.process_vote(v)
        total_qty += int(v["qty"])

    # use a small limit to force multiple packages
    vp.calculate_packages(limit=10)

    # collect all produced packages and waitlist
    closed = vp.closed_packages.get(poll_id, [])
    wait = vp.waitlist.get(poll_id, [])
    sum_closed = sum(sum(int(x.get("parsed_qty", x.get("qty", 0))) for x in pkg) for pkg in closed)
    sum_wait = sum(int(v.get("parsed_qty", v.get("qty", 0))) for v in wait)

    # total conserved
    assert sum_closed + sum_wait == total_qty
    # each closed package must not exceed limit
    for pkg in closed:
        assert sum(int(x.get("parsed_qty", x.get("qty", 0))) for x in pkg) <= 10


def test_concurrent_confirm_requests_do_not_duplicate(tmp_path, monkeypatch):
    """F-051: confirmed_packages_service no longer uses CONFIRMED_FILE (migrated to Postgres).
    This test verifies sequential confirm calls: first succeeds, second returns 404."""
    metrics_file = tmp_path / "dashboard_metrics.json"
    monkeypatch.setattr(main_module, "METRICS_FILE", str(metrics_file))

    from app.services import metrics_service
    monkeypatch.setattr(metrics_service, "METRICS_FILE", metrics_file)
    from app.storage import JsonFileStorage
    monkeypatch.setattr(metrics_service, "_storage", JsonFileStorage(metrics_file))

    # initial metrics with one closed package
    data = {
        "generated_at": "now",
        "enquetes": {},
        "votos": {
            "today": 1,
            "packages": {
                "open": [],
                "closed_today": [{"id": "pkg_x", "poll_title": "X", "qty": 5, "closed_at": None, "votes": []}],
                "closed_week": [],
                "confirmed_today": [],
            },
        },
    }
    metrics_file.write_text(json.dumps(data), encoding="utf-8")

    client = TestClient(main_module.app)
    # First call succeeds
    r1 = client.post("/api/packages/pkg_x/confirm")
    assert r1.status_code == 200

    # Second call for the same package should fail with 404 (not found in closed_today anymore)
    r2 = client.post("/api/packages/pkg_x/confirm")
    assert r2.status_code == 404
