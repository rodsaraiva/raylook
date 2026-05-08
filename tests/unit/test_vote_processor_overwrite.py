"""Tests for vote overwrite semantics and multi-poll isolation."""
from datetime import datetime, timedelta

from metrics.processors import VoteProcessor


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


class TestVoteOverwrite:
    """Verify that a second add from the same voter OVERWRITES the first."""

    def test_overwrite_replaces_previous_vote(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "p1", "voterPhone": "voter-A", "qty": "3"})
        vp.process_vote({"pollId": "p1", "voterPhone": "voter-A", "qty": "6"})

        # Only one entry for voter-A
        assert len(vp.poll_votes["p1"]["voter-A"]) == 1
        assert vp.poll_votes["p1"]["voter-A"][0]["parsed_qty"] == 6

    def test_overwrite_then_remove(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "p1", "voterPhone": "voter-A", "qty": "3"})
        vp.process_vote({"pollId": "p1", "voterPhone": "voter-A", "qty": "6"})

        status, removed = vp.process_vote({"pollId": "p1", "voterPhone": "voter-A", "qty": "0"})

        assert status == "removed"
        assert removed["parsed_qty"] == 6
        assert vp.poll_votes["p1"]["voter-A"] == []

    def test_different_polls_are_isolated(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "poll-A", "voterPhone": "voter-1", "qty": "3"})
        vp.process_vote({"pollId": "poll-B", "voterPhone": "voter-1", "qty": "6"})

        assert vp.poll_votes["poll-A"]["voter-1"][0]["parsed_qty"] == 3
        assert vp.poll_votes["poll-B"]["voter-1"][0]["parsed_qty"] == 6

    def test_remove_from_wrong_poll_is_ignored(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "poll-A", "voterPhone": "voter-1", "qty": "3"})

        status, _ = vp.process_vote({"pollId": "poll-B", "voterPhone": "voter-1", "qty": "0"})

        assert status == "ignored"
        # Original vote still intact
        assert len(vp.poll_votes["poll-A"]["voter-1"]) == 1

    def test_multiple_voters_same_poll(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "p", "voterPhone": "A", "qty": "3"})
        vp.process_vote({"pollId": "p", "voterPhone": "B", "qty": "6"})
        vp.process_vote({"pollId": "p", "voterPhone": "C", "qty": "9"})

        total = sum(
            v["parsed_qty"]
            for phone_votes in vp.poll_votes["p"].values()
            for v in phone_votes
        )
        assert total == 18

    def test_double_remove_yields_removed_then_ignored(self):
        vp = VoteProcessor()

        vp.process_vote({"pollId": "p1", "voterPhone": "v1", "qty": "5"})

        s1, _ = vp.process_vote({"pollId": "p1", "voterPhone": "v1", "qty": "0"})
        s2, _ = vp.process_vote({"pollId": "p1", "voterPhone": "v1", "qty": "0"})

        assert s1 == "removed"
        assert s2 == "ignored"
