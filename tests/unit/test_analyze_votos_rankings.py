"""Tests for rankings, hourly distribution and boundary conditions in analyze_votos."""
from datetime import datetime, timedelta

import pytest

from metrics.processors import get_date_range, analyze_votos


@pytest.fixture(autouse=True)
def _isolate_customer_store(monkeypatch):
    """Prevent customer_service from reading/writing disk to avoid cross-test pollution."""
    monkeypatch.setattr(
        "app.services.customer_service.load_customers", lambda: {}
    )
    monkeypatch.setattr(
        "app.services.customer_service.save_customers", lambda _data: None
    )


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


class TestHourlyDistribution:
    def test_votes_counted_in_correct_hour_buckets(self):
        now = datetime(2026, 2, 15, 23, 59, 0)
        dates = get_date_range(now=now)

        votos = [
            {"id": i, "pollId": "p", "voterPhone": f"551190000000{i}", "voterName": f"V{i}",
             "qty": "3", "timestamp": _ts(dates["today_start"] + timedelta(hours=h))}
            for i, h in enumerate([0, 0, 8, 14, 14, 23])
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["by_hour"][0] == 2
        assert result["by_hour"][8] == 1
        assert result["by_hour"][14] == 2
        assert result["by_hour"][23] == 1
        assert result["by_hour"].get(12, 0) == 0


class TestCustomerRankings:
    def test_same_customer_accumulates_qty_across_polls(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p1", "voterPhone": "5511999990001", "voterName": "Customer X",
             "qty": "6", "timestamp": _ts(today + timedelta(hours=1))},
            {"id": 2, "pollId": "p2", "voterPhone": "5511999990001", "voterName": "Customer X",
             "qty": "3", "timestamp": _ts(today + timedelta(hours=2))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["by_customer_today"]["5511999990001"]["qty"] == 9
        assert result["by_customer_today"]["5511999990001"]["name"] == "Customer X"

    def test_top_customer_by_week_includes_yesterday(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        votos = [
            {"id": 1, "pollId": "p1", "voterPhone": "5511999990002", "voterName": "Weekly User",
             "qty": "12", "timestamp": _ts(yesterday + timedelta(hours=5))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["by_customer_week"]["5511999990002"]["qty"] == 12
        # Not in today rankings
        assert "5511999990002" not in result["by_customer_today"]


class TestPollRankings:
    def test_multiple_voters_same_poll_accumulate(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": i, "pollId": "hot-poll", "voterPhone": f"p{i}", "voterName": f"V{i}",
             "qty": "6", "timestamp": _ts(today + timedelta(minutes=i))}
            for i in range(4)
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["by_poll_today"]["hot-poll"]["qty"] == 24


class TestBoundaryTimestamps:
    def test_vote_exactly_at_midnight_counts_as_today(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph", "voterName": "V",
             "qty": "3", "timestamp": _ts(dates["today_start"])},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})
        assert result["today"] == 1

    def test_vote_one_second_before_midnight_counts_as_yesterday(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph", "voterName": "V",
             "qty": "3", "timestamp": _ts(dates["today_start"] - timedelta(seconds=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})
        assert result["yesterday"] == 1
        assert result["today"] == 0


class TestEmptyInputs:
    def test_empty_votos_returns_zeroed_metrics(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)

        result = analyze_votos([], dates, enquetes_map={})

        assert result["today"] == 0
        assert result["yesterday"] == 0
        assert result["removed_today"] == 0
        assert result["packages"]["open"] == []
        assert result["packages"]["closed_today"] == []
        assert result["by_hour"] == {}
        assert result["by_poll_today"] == {}
        assert result["by_customer_today"] == {}
