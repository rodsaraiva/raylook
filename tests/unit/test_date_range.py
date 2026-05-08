"""Tests for get_date_range: correctness of all returned keys."""
from datetime import datetime, timedelta

from metrics.processors import get_date_range


class TestDateRangeValues:
    def test_today_start_is_midnight(self):
        now = datetime(2026, 3, 10, 15, 30, 45)
        d = get_date_range(now=now)
        assert d["today_start"] == datetime(2026, 3, 10, 0, 0, 0)

    def test_yesterday_start_is_one_day_before_today(self):
        now = datetime(2026, 3, 10, 15, 30, 45)
        d = get_date_range(now=now)
        assert d["yesterday_start"] == datetime(2026, 3, 9, 0, 0, 0)

    def test_yesterday_end_equals_today_start(self):
        now = datetime(2026, 3, 10, 15, 30, 45)
        d = get_date_range(now=now)
        assert d["yesterday_end"] == d["today_start"]

    def test_week_start_is_7_days_before_today(self):
        now = datetime(2026, 3, 10, 15, 30, 45)
        d = get_date_range(now=now)
        assert d["week_start"] == datetime(2026, 3, 3, 0, 0, 0)

    def test_day24h_start_is_24h_before_now(self):
        now = datetime(2026, 3, 10, 15, 30, 45)
        d = get_date_range(now=now)
        assert d["day24h_start"] == now - timedelta(hours=24)

    def test_now_is_stored(self):
        now = datetime(2026, 6, 1, 0, 0, 0)
        d = get_date_range(now=now)
        assert d["now"] == now


class TestDateRangeEdgeCases:
    def test_midnight_exact(self):
        now = datetime(2026, 1, 15, 0, 0, 0)
        d = get_date_range(now=now)
        assert d["today_start"] == now
        assert d["yesterday_start"] == datetime(2026, 1, 14, 0, 0, 0)

    def test_one_second_after_midnight(self):
        now = datetime(2026, 1, 15, 0, 0, 1)
        d = get_date_range(now=now)
        assert d["today_start"] == datetime(2026, 1, 15, 0, 0, 0)

    def test_last_second_of_day(self):
        now = datetime(2026, 1, 15, 23, 59, 59)
        d = get_date_range(now=now)
        assert d["today_start"] == datetime(2026, 1, 15, 0, 0, 0)

    def test_new_year_boundary(self):
        now = datetime(2026, 1, 1, 0, 0, 0)
        d = get_date_range(now=now)
        assert d["yesterday_start"] == datetime(2025, 12, 31, 0, 0, 0)

    def test_leap_year_boundary(self):
        now = datetime(2024, 3, 1, 0, 0, 0)  # 2024 is leap
        d = get_date_range(now=now)
        assert d["yesterday_start"] == datetime(2024, 2, 29, 0, 0, 0)

    def test_defaults_to_real_now(self):
        d = get_date_range()
        assert "now" in d
        assert (datetime.now() - d["now"]).total_seconds() < 2
