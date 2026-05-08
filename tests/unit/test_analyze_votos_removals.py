"""Tests for removal counting and percentage calculations in analyze_votos."""
from datetime import datetime, timedelta

from metrics.processors import get_date_range, analyze_votos


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


class TestRemovalCounting:
    def test_removal_counted_today(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "6", "timestamp": _ts(today + timedelta(hours=1))},
            {"id": 2, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "0", "timestamp": _ts(today + timedelta(hours=2))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["removed_today"] == 1

    def test_removal_counted_yesterday(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "6", "timestamp": _ts(yesterday + timedelta(hours=1))},
            {"id": 2, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "0", "timestamp": _ts(yesterday + timedelta(hours=2))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["removed_yesterday"] == 1

    def test_ignored_removal_also_counted(self):
        """Removing a vote that was never added → 'ignored', but still counts."""
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "0", "timestamp": _ts(today + timedelta(hours=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["removed_today"] == 1
        assert result["today"] == 0


class TestDiffAndPercentages:
    def test_positive_diff_yesterday(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        yesterday = dates["yesterday_start"]

        votos = [
            # 3 today
            *[{"id": i, "pollId": "p", "voterPhone": f"ph-{i}", "voterName": f"V{i}",
               "qty": "3", "timestamp": _ts(today + timedelta(hours=1, minutes=i))}
              for i in range(3)],
            # 1 yesterday
            {"id": 100, "pollId": "p", "voterPhone": "ph-100", "voterName": "V100",
             "qty": "3", "timestamp": _ts(yesterday + timedelta(hours=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["today"] == 3
        assert result["yesterday"] == 1
        assert result["diff_yesterday"] == 2
        assert result["pct_yesterday"] == 200.0

    def test_negative_diff(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        yesterday = dates["yesterday_start"]

        votos = [
            # 1 today
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "3", "timestamp": _ts(today + timedelta(hours=1))},
            # 3 yesterday
            *[{"id": 10 + i, "pollId": "p", "voterPhone": f"ph-{10+i}", "voterName": f"V{10+i}",
               "qty": "3", "timestamp": _ts(yesterday + timedelta(hours=1, minutes=i))}
              for i in range(3)],
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["diff_yesterday"] == -2

    def test_pct_removed_zero_division(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "6", "timestamp": _ts(today + timedelta(hours=1))},
            {"id": 2, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "0", "timestamp": _ts(today + timedelta(hours=2))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        # removed_yesterday = 0 → pct_removed should be 0 (not ZeroDivisionError)
        assert result["pct_removed"] == 0


class TestFieldFallbacks:
    def test_field_158_used_for_poll_id(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "field_158": "poll-old", "voterPhone": "5511999990005",
             "voterName": "V", "qty": "6",
             "timestamp": _ts(today + timedelta(hours=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={"poll-old": "Legacy Poll"})

        assert "poll-old" in result["by_poll_today"]

    def test_field_160_for_voter_phone(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "field_160": "5511999990003",
             "field_161": "Legacy Name", "qty": "6",
             "timestamp": _ts(today + timedelta(hours=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert "5511999990003" in result["by_customer_today"]
        assert result["by_customer_today"]["5511999990003"]["name"] == "Legacy Name"

    def test_field_166_for_timestamp(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph", "voterName": "V",
             "qty": "6", "field_166": _ts(today + timedelta(hours=1))},
        ]

        result = analyze_votos(votos, dates, enquetes_map={})

        assert result["today"] == 1
