import json
from datetime import datetime, timedelta

from metrics.processors import get_date_range, analyze_votos


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def test_analyze_votos_activity_rankings_and_packages():
    now = datetime(2026, 2, 15, 12, 0, 0)
    dates = get_date_range(now=now)
    today = dates["today_start"]
    yesterday = dates["yesterday_start"]

    votos = [
        # yesterday add (active)
        {
            "id": 1,
            "pollId": "poll-A",
            "voterPhone": "5511977770001",
            "voterName": "User Yesterday",
            "qty": "14",
            "timestamp": _ts(yesterday + timedelta(hours=3)),
            "rawJson": json.dumps({"poll": {"title": "Titulo da Enquete A"}}),
        },
        # today add (active)
        {
            "id": 2,
            "pollId": "poll-A",
            "voterPhone": "5511977770002",
            "voterName": "User Today 1",
            "qty": "10",
            "timestamp": _ts(today + timedelta(hours=2)),
        },
        # today add then remove (not active)
        {
            "id": 3,
            "pollId": "poll-A",
            "voterPhone": "5511977770003",
            "voterName": "User Today 2",
            "qty": "5",
            "timestamp": _ts(today + timedelta(hours=3)),
        },
        {
            "id": 4,
            "pollId": "poll-A",
            "voterPhone": "5511977770003",
            "voterName": "User Today 2",
            "qty": "0",
            "timestamp": _ts(today + timedelta(hours=4)),
        },
        # ignored remove today
        {
            "id": 5,
            "pollId": "poll-A",
            "voterPhone": "5511977770004",
            "voterName": "No Stack",
            "qty": "0",
            "timestamp": _ts(today + timedelta(hours=5)),
        },
    ]

    enquetes_map = {}
    enquetes_created = {"poll-A": yesterday}
    result = analyze_votos(votos, dates, enquetes_map, enquetes_created=enquetes_created)

    # Activity
    assert result["today"] == 2
    assert result["yesterday"] == 1
    assert result["removed_today"] == 2
    assert result["removed_yesterday"] == 0

    # rawJson fallback fills title (string simples, pois vem do _first_pass)
    assert enquetes_map["poll-A"] == "Titulo da Enquete A"

    # Rankings from ACTIVE votes only: today active is qty 10
    assert result["by_poll_today"]["poll-A"]["qty"] == 10
    # Week includes yesterday active 14 + today active 10
    assert result["by_poll_week"]["poll-A"]["qty"] == 24

    # Packages: one closed package (14 + 10), none open
    # closed_today usa corte fixo desde 20/03/2026 (este cenário é anterior).
    assert len(result["packages"]["closed_week"]) == 1
    assert result["packages"]["closed_week"][0]["qty"] == 24
    assert result["packages"]["open"] == []


def test_analyze_votos_handles_invalid_timestamps_gracefully():
    now = datetime(2026, 2, 15, 12, 0, 0)
    dates = get_date_range(now=now)

    votos = [
        {"id": 1, "pollId": "p", "voterPhone": "a", "voterName": "A", "qty": "3", "timestamp": "not-a-ts"},
        {"id": 2, "pollId": "p", "voterPhone": "b", "voterName": "B", "qty": "4", "timestamp": "also-invalid"},
    ]

    result = analyze_votos(votos, dates, enquetes_map={})

    assert result["today"] == 0
    assert result["yesterday"] == 0
    assert result["by_hour"] == {}
    assert isinstance(result["packages"], dict)

