from metrics.processors import VoteProcessor

def test_process_add_and_remove():
    vp = VoteProcessor()
    status, v = vp.process_vote({"pollId": "p1", "voterPhone": "t1", "qty": "2"})
    assert status == "added"
    status2, removed = vp.process_vote({"pollId": "p1", "voterPhone": "t1", "qty": "0"})
    assert status2 in ("removed", "ignored")

def test_find_subset_sum_simple():
    vp = VoteProcessor()
    votes = [{"parsed_qty": 10}, {"parsed_qty": 14}, {"parsed_qty": 5}]
    subset, remaining = vp._find_subset_sum(votes, 15)
    # subset should sum to 15 (10+5) or single 15 if present
    assert sum(v["parsed_qty"] for v in subset) == 15

