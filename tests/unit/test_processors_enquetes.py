from datetime import datetime, timedelta

from metrics.processors import get_date_range, analyze_enquetes


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def test_get_date_range_boundaries():
    now = datetime(2026, 2, 15, 12, 30, 45)
    dates = get_date_range(now=now)

    assert dates["now"] == now
    assert dates["today_start"] == datetime(2026, 2, 15, 0, 0, 0)
    assert dates["yesterday_start"] == datetime(2026, 2, 14, 0, 0, 0)
    assert dates["yesterday_end"] == datetime(2026, 2, 15, 0, 0, 0)
    assert dates["week_start"] == datetime(2026, 2, 8, 0, 0, 0)
    assert dates["day24h_start"] == datetime(2026, 2, 14, 12, 30, 45)


def test_analyze_enquetes_counts_today_yesterday_and_last_7_days():
    now = datetime(2026, 2, 15, 12, 0, 0)
    dates = get_date_range(now=now)

    enquetes = [
        {"createdAtTs": _ts(dates["today_start"] + timedelta(hours=2))},      # today
        {"createdAtTs": _ts(dates["yesterday_start"] + timedelta(hours=3))},  # yesterday
        {"createdAtTs": _ts(dates["today_start"] - timedelta(days=2))},       # last 7 days
        {"createdAtTs": _ts(dates["today_start"] - timedelta(days=10))},      # outside week
        {"createdAtTs": "invalid"},                                            # ignored
    ]

    result = analyze_enquetes(enquetes, dates)

    assert result["today"] == 1
    assert result["yesterday"] == 1
    # last_7_days has 2 valid entries (yesterday + 2 days ago)
    assert result["avg_7_days"] == 2 / 7
    assert result["diff_yesterday"] == 0
    assert result["pct_yesterday"] == 0


def test_analyze_enquetes_zero_division_guards():
    now = datetime(2026, 2, 15, 12, 0, 0)
    dates = get_date_range(now=now)
    # only today; no yesterday and no week history
    enquetes = [{"createdAtTs": _ts(dates["today_start"] + timedelta(minutes=1))}]

    result = analyze_enquetes(enquetes, dates)

    assert result["today"] == 1
    assert result["yesterday"] == 0
    assert result["pct_yesterday"] == 0
    assert result["pct_avg"] == 0

