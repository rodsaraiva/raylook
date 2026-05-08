"""Concrete assembly tests: validate exact package compositions produced by VoteProcessor."""
from metrics.processors import VoteProcessor
from metrics.processors import parse_timestamp
from datetime import datetime


def make_vote(poll_id, phone, qty, ts=None):
    return {
        "pollId": poll_id,
        "voterPhone": phone,
        "qty": str(qty),
        "timestamp": ts or datetime.now().isoformat(),
    }


def extract_pkg_qtys(pkg):
    return [int(v.get("parsed_qty", v.get("qty", 0))) for v in pkg]


def test_exact_fill_and_pairing():
    vp = VoteProcessor()
    poll = "p_exact"
    # votes: 10,5,5 -> expect [10], [5,5]
    votes = [make_vote(poll, "a", 10), make_vote(poll, "b", 5), make_vote(poll, "c", 5)]
    for v in votes:
        vp.process_vote(v)

    vp.calculate_packages(limit=10)
    closed = vp.closed_packages.get(poll, [])
    # Expect 2 packages
    assert len(closed) == 2
    sums = [sum(extract_pkg_qtys(pkg)) for pkg in closed]
    assert sorted(sums) == [10, 10]


def test_combination_prefers_larger_then_pair():
    vp = VoteProcessor()
    poll = "p_combo"
    # votes: 8,6,4,2 -> expect first package 8+2, second 6+4
    votes = [
        make_vote(poll, "v1", 8),
        make_vote(poll, "v2", 6),
        make_vote(poll, "v3", 4),
        make_vote(poll, "v4", 2),
    ]
    for v in votes:
        vp.process_vote(v)

    vp.calculate_packages(limit=10)
    closed = vp.closed_packages.get(poll, [])
    # We expect two packages summing to 10 each
    assert len(closed) == 2
    sums = [sum(extract_pkg_qtys(pkg)) for pkg in closed]
    assert sums == [10, 10]
    # Check composition of first package contains 8
    assert 8 in extract_pkg_qtys(closed[0])


def test_prioritize_qty_leads_to_partial_pack_when_no_further_combination():
    vp = VoteProcessor()
    poll = "p_partial"
    # votes: 6,4,4,2,2 and limit 10
    # given current algorithm and ordering, first package is 6+4 => remaining [4,2,2] -> no exact pack
    votes = [
        make_vote(poll, "a", 6),
        make_vote(poll, "b", 4),
        make_vote(poll, "c", 4),
        make_vote(poll, "d", 2),
        make_vote(poll, "e", 2),
    ]
    for v in votes:
        vp.process_vote(v)

    vp.calculate_packages(limit=10)
    closed = vp.closed_packages.get(poll, [])
    wait = vp.waitlist.get(poll, [])
    # Expect exactly one closed package (6+4)
    assert len(closed) == 1
    assert sum(extract_pkg_qtys(closed[0])) == 10
    # Remaining votes sum should equal total - 10
    total = sum(int(v["qty"]) for v in votes)
    remaining = sum(int(v.get("parsed_qty", v.get("qty", 0))) for v in wait)
    assert remaining == total - 10

