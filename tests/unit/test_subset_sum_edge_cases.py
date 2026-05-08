"""Edge-case and stress tests for VoteProcessor._find_subset_sum."""
import pytest

from metrics.processors import VoteProcessor


def _votes(qtys):
    """Helper: build vote dicts from a list of quantities."""
    return [{"parsed_qty": q} for q in qtys]


class TestSubsetSumEdgeCases:
    """Boundary and combinatorial scenarios."""

    def test_empty_list_returns_none(self):
        vp = VoteProcessor()
        subset, remaining = vp._find_subset_sum([], 24)
        assert subset is None
        assert remaining == []

    def test_single_element_exact_match(self):
        vp = VoteProcessor()
        votes = _votes([24])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is not None
        assert sum(v["parsed_qty"] for v in subset) == 24
        assert remaining == []

    def test_single_element_no_match(self):
        vp = VoteProcessor()
        votes = _votes([23])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is None

    def test_all_elements_needed(self):
        vp = VoteProcessor()
        votes = _votes([6, 6, 6, 6])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is not None
        assert sum(v["parsed_qty"] for v in subset) == 24
        assert remaining == []

    def test_target_zero_returns_none_due_to_empty_list_falsy(self):
        """Target 0: backtrack returns [] which is falsy → treated as no solution."""
        vp = VoteProcessor()
        votes = _votes([3, 6, 9])
        subset, remaining = vp._find_subset_sum(votes, 0)
        # [] is falsy in Python, so _find_subset_sum returns None
        assert subset is None
        assert remaining == votes

    def test_all_elements_exceed_target(self):
        vp = VoteProcessor()
        votes = _votes([25, 30, 50])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is None

    def test_duplicate_quantities(self):
        vp = VoteProcessor()
        votes = _votes([12, 12, 12])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is not None
        assert sum(v["parsed_qty"] for v in subset) == 24
        assert len(remaining) == 1

    def test_greedy_trap_requires_skipping(self):
        """Greedy would pick 20 first and fail; backtrack finds 15+9."""
        vp = VoteProcessor()
        votes = _votes([20, 15, 9])
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is not None
        assert sum(v["parsed_qty"] for v in subset) == 24

    def test_many_small_items(self):
        """12 items of qty 2 each → exactly 24."""
        vp = VoteProcessor()
        votes = _votes([2] * 12)
        subset, remaining = vp._find_subset_sum(votes, 24)
        assert subset is not None
        assert sum(v["parsed_qty"] for v in subset) == 24

    def test_remaining_preserves_order(self):
        vp = VoteProcessor()
        votes = [{"parsed_qty": 10, "tag": "A"}, {"parsed_qty": 14, "tag": "B"}, {"parsed_qty": 5, "tag": "C"}]
        subset, remaining = vp._find_subset_sum(votes, 24)
        # 10 + 14 = 24 → remaining is [C]
        assert len(remaining) == 1
        assert remaining[0]["tag"] == "C"


class TestCalculatePackagesMultiple:
    """Test that calculate_packages can form multiple packages."""

    def test_two_full_packages(self):
        vp = VoteProcessor()
        # 6 voters × 8 each = 48 = 2 packages of 24
        for i in range(6):
            vp.process_vote({
                "pollId": "poll-X",
                "voterPhone": f"phone-{i}",
                "qty": "8",
                "timestamp": str(1700000000 + i),
            })

        vp.calculate_packages(limit=24)

        assert len(vp.closed_packages["poll-X"]) == 2
        for pkg in vp.closed_packages["poll-X"]:
            assert sum(v["parsed_qty"] for v in pkg) == 24
        assert vp.waitlist["poll-X"] == []

    def test_package_with_leftover(self):
        vp = VoteProcessor()
        # 12 + 12 + 7 = 31 → one package of 24 + 7 leftover
        # Use unique phone numbers to avoid overwrite
        for i, qty in enumerate([12, 12, 7]):
            vp.process_vote({
                "pollId": "poll-Y",
                "voterPhone": f"phone-{i}",
                "qty": str(qty),
                "timestamp": str(1700000000 + i),
            })

        vp.calculate_packages(limit=24)

        assert len(vp.closed_packages["poll-Y"]) == 1
        assert sum(v["parsed_qty"] for v in vp.waitlist["poll-Y"]) == 7

    def test_custom_limit(self):
        vp = VoteProcessor()
        vp.process_vote({"pollId": "p", "voterPhone": "a", "qty": "5", "timestamp": "1700000000"})
        vp.process_vote({"pollId": "p", "voterPhone": "b", "qty": "5", "timestamp": "1700000001"})

        vp.calculate_packages(limit=10)

        assert len(vp.closed_packages["p"]) == 1
        assert sum(v["parsed_qty"] for v in vp.closed_packages["p"][0]) == 10

    def test_impossible_package_all_goes_to_waitlist(self):
        vp = VoteProcessor()
        vp.process_vote({"pollId": "p", "voterPhone": "a", "qty": "7", "timestamp": "1700000000"})
        vp.process_vote({"pollId": "p", "voterPhone": "b", "qty": "9", "timestamp": "1700000001"})

        vp.calculate_packages(limit=24)

        assert len(vp.closed_packages["p"]) == 0
        assert sum(v["parsed_qty"] for v in vp.waitlist["p"]) == 16

    def test_multiple_polls_independent(self):
        vp = VoteProcessor()
        # Poll A: 12 + 12 = 24
        vp.process_vote({"pollId": "A", "voterPhone": "x", "qty": "12", "timestamp": "1700000000"})
        vp.process_vote({"pollId": "A", "voterPhone": "y", "qty": "12", "timestamp": "1700000001"})
        # Poll B: 10 only → waitlist
        vp.process_vote({"pollId": "B", "voterPhone": "z", "qty": "10", "timestamp": "1700000002"})

        vp.calculate_packages(limit=24)

        assert len(vp.closed_packages["A"]) == 1
        assert len(vp.closed_packages["B"]) == 0
        assert sum(v["parsed_qty"] for v in vp.waitlist["B"]) == 10
