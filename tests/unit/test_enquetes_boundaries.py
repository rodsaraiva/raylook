"""Boundary / edge-case tests for analyze_enquetes."""
from datetime import datetime, timedelta

from metrics.processors import get_date_range, analyze_enquetes


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


class TestEnquetesTimeBoundaries:
    """Test precise boundary behavior for today/yesterday/week buckets."""

    def setup_method(self):
        self.now = datetime(2026, 2, 15, 14, 0, 0)
        self.dates = get_date_range(now=self.now)

    def test_exactly_at_today_start(self):
        enquetes = [{"createdAtTs": _ts(self.dates["today_start"])}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["today"] == 1
        assert result["yesterday"] == 0

    def test_one_second_before_today_start(self):
        enquetes = [{"createdAtTs": _ts(self.dates["today_start"] - timedelta(seconds=1))}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["yesterday"] == 1
        assert result["today"] == 0

    def test_exactly_at_yesterday_start(self):
        enquetes = [{"createdAtTs": _ts(self.dates["yesterday_start"])}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["yesterday"] == 1

    def test_one_second_before_yesterday_start(self):
        """Falls into week range, not yesterday."""
        enquetes = [{"createdAtTs": _ts(self.dates["yesterday_start"] - timedelta(seconds=1))}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["yesterday"] == 0

    def test_one_enquete_per_day_for_7_days(self):
        enquetes = [
            {"createdAtTs": _ts(self.dates["today_start"] - timedelta(days=d, hours=-6))}
            for d in range(1, 8)
        ]
        result = analyze_enquetes(enquetes, self.dates)
        # avg_7_days is computed from internal last_7_days list
        # With 7 enquetes spread across 7 days (all in week range), avg should be > 0
        assert result["avg_7_days"] > 0
        # Yesterday should also be counted
        assert result["yesterday"] >= 1


class TestEnquetesFieldFallbacks:
    """Test field_171 fallback for timestamp."""

    def setup_method(self):
        self.now = datetime(2026, 2, 15, 14, 0, 0)
        self.dates = get_date_range(now=self.now)

    def test_field_171_used_when_createdAtTs_missing(self):
        enquetes = [{"field_171": _ts(self.dates["today_start"] + timedelta(hours=1))}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["today"] == 1

    def test_no_timestamp_at_all_is_skipped(self):
        enquetes = [{"title": "No timestamp here"}]
        result = analyze_enquetes(enquetes, self.dates)
        assert result["today"] == 0
        assert result["yesterday"] == 0


class TestEnquetesPercentages:
    def test_pct_yesterday_positive(self):
        now = datetime(2026, 2, 15, 14, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            # 3 today
            {"createdAtTs": _ts(dates["today_start"] + timedelta(hours=h))} for h in [1, 2, 3]
        ] + [
            # 2 yesterday
            {"createdAtTs": _ts(dates["yesterday_start"] + timedelta(hours=h))} for h in [1, 2]
        ]

        result = analyze_enquetes(enquetes, dates)
        assert result["today"] == 3
        assert result["yesterday"] == 2
        assert result["diff_yesterday"] == 1
        assert result["pct_yesterday"] == 50.0

    def test_pct_yesterday_zero_division(self):
        now = datetime(2026, 2, 15, 14, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            {"createdAtTs": _ts(dates["today_start"] + timedelta(hours=1))},
        ]

        result = analyze_enquetes(enquetes, dates)
        assert result["yesterday"] == 0
        assert result["pct_yesterday"] == 0  # no division by zero

    def test_pct_avg_when_all_zero(self):
        now = datetime(2026, 2, 15, 14, 0, 0)
        dates = get_date_range(now=now)

        result = analyze_enquetes([], dates)
        assert result["pct_avg"] == 0
        assert result["avg_7_days"] == 0


class TestEnquetesLargeVolume:
    """Stress test with many enquetes."""

    def test_thousand_enquetes_all_today(self):
        now = datetime(2026, 2, 15, 14, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            {"createdAtTs": _ts(dates["today_start"] + timedelta(seconds=i))}
            for i in range(1000)
        ]

        result = analyze_enquetes(enquetes, dates)
        assert result["today"] == 1000
        assert result["yesterday"] == 0
