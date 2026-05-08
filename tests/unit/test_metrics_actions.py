"""Unit tests for metrics.actions: ConfirmAction, RejectAction, RevertAction."""
import json
from pathlib import Path
from metrics.actions import ConfirmAction, RejectAction, RevertAction
from datetime import datetime, timezone


def make_sample_metrics():
    return {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [
                    {"id": "p_1_0", "poll_title": "Poll A", "qty": 10, "closed_at": "2026-02-19T10:00:00", "votes": []}
                ],
                "closed_week": [],
                "confirmed_today": [],
            }
        }
    }


def test_confirm_action_moves_package_and_sets_confirmed_at_and_by():
    metrics = make_sample_metrics()
    act = ConfirmAction("p_1_0", user="tester")
    out = act.execute(metrics)
    pkgs = out["votos"]["packages"]
    assert len(pkgs["closed_today"]) == 0
    # Package is returned as self.confirmed_pkg to be placed into confirmed_packages.json
    assert act.confirmed_pkg is not None
    assert act.confirmed_pkg["id"] == "p_1_0"
    assert "confirmed_at" in act.confirmed_pkg
    assert act.confirmed_pkg.get("confirmed_by") == "tester"
    # confirmed_at should be an ISO string parseable to datetime
    datetime.fromisoformat(act.confirmed_pkg["confirmed_at"].replace("Z", "+00:00"))


def test_reject_action_moves_to_rejected_list():
    metrics = make_sample_metrics()
    # add a second closed package to ensure it stays in closed_today
    metrics["votos"]["packages"]["closed_today"].append(
        {"id": "p_1_1", "poll_title": "Poll B", "qty": 5, "closed_at": "2026-02-19T11:00:00", "votes": []}
    )
    act = RejectAction("p_1_0", user="tester2")
    out = act.execute(metrics)
    
    closed = out["votos"]["packages"]["closed_today"]
    
    assert len(closed) == 1
    assert closed[0]["id"] == "p_1_1"
    
    assert act.rejected_pkg is not None
    assert act.rejected_pkg["id"] == "p_1_0"
    assert act.rejected_pkg["rejected"] is True
    assert act.rejected_pkg.get("rejected_by") == "tester2"
    assert "rejected_at" in act.rejected_pkg



import pytest

def test_revert_action_removes_confirmation_metadata_and_inserts_at_beginning():
    # prepare metrics where package is confirmed
    metrics = make_sample_metrics()
    pkg = metrics["votos"]["packages"]["closed_today"].pop(0)
    pkg["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    pkg["confirmed_by"] = "tester"
    metrics["votos"]["packages"]["confirmed_today"] = [pkg]

    act = RevertAction("p_1_0", user="reverter")
    
    with pytest.raises(RuntimeError, match="revert_not_allowed"):
        out = act.execute(metrics)

