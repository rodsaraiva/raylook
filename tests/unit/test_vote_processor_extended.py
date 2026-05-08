from datetime import datetime, timedelta

from metrics.processors import VoteProcessor


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def test_process_vote_uses_fallback_fields_and_invalid_qty_behaviour():
    vp = VoteProcessor()

    status_add, vote_add = vp.process_vote(
        {
            "field_158": "poll-1",
            "field_160": "phone-1",
            "field_164": "3",
        }
    )
    assert status_add == "added"
    assert vote_add["parsed_qty"] == 3

    # invalid qty becomes 0 and removes existing entry
    status_remove, _ = vp.process_vote(
        {
            "field_158": "poll-1",
            "field_160": "phone-1",
            "field_164": "abc",
        }
    )
    assert status_remove == "removed"

    # removing again with empty stack should be ignored
    status_ignored, _ = vp.process_vote(
        {
            "field_158": "poll-1",
            "field_160": "phone-1",
            "field_164": "0",
        }
    )
    assert status_ignored == "ignored"


def test_calculate_packages_creates_closed_and_waitlist():
    vp = VoteProcessor()
    base = datetime(2026, 2, 15, 10, 0, 0)

    # 10 + 14 closes one package (24), leaving 5 in waitlist
    vp.process_vote({"pollId": "poll-A", "voterPhone": "p1", "qty": "10", "timestamp": _ts(base)})
    vp.process_vote({"pollId": "poll-A", "voterPhone": "p2", "qty": "14", "timestamp": _ts(base + timedelta(minutes=1))})
    vp.process_vote({"pollId": "poll-A", "voterPhone": "p3", "qty": "5", "timestamp": _ts(base + timedelta(minutes=2))})

    vp.calculate_packages(limit=24)

    assert "poll-A" in vp.closed_packages
    assert len(vp.closed_packages["poll-A"]) == 1
    assert sum(v["parsed_qty"] for v in vp.closed_packages["poll-A"][0]) == 24
    assert sum(v["parsed_qty"] for v in vp.waitlist["poll-A"]) == 5


def test_subset_sum_no_solution_returns_none_and_original_list():
    vp = VoteProcessor()
    votes = [{"parsed_qty": 7}, {"parsed_qty": 11}]

    subset, remaining = vp._find_subset_sum(votes, 24)

    assert subset is None
    assert remaining == votes

