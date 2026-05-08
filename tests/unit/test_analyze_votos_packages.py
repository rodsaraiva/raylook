"""Tests for package assembly, closed_today, closed_week and open in analyze_votos."""
import os
from datetime import datetime, timedelta

import pytest

from metrics.processors import get_date_range, analyze_votos


@pytest.fixture(autouse=True)
def _clear_metrics_min_date(monkeypatch):
    """Ensure METRICS_MIN_DATE does not interfere with test date ranges."""
    monkeypatch.delenv("METRICS_MIN_DATE", raising=False)


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def _make_votes(poll_id, qtys, base_ts):
    """Build a list of vote dicts for a single poll."""
    return [
        {
            "id": i,
            "pollId": poll_id,
            "voterPhone": f"551198888{i:04d}",
            "voterName": f"Voter {i}",
            "qty": str(q),
            "timestamp": _ts(base_ts + timedelta(minutes=i)),
        }
        for i, q in enumerate(qtys)
    ]


class TestClosedTodayPackages:
    def test_single_package_closed_today(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = _make_votes("poll-A", [6, 6, 6, 6], today + timedelta(hours=1))
        enquetes_map = {"poll-A": "Test Poll"}
        # poll criado hoje → aparece em closed_today
        enquetes_created = {"poll-A": today + timedelta(hours=0, minutes=30)}

        result = analyze_votos(votos, dates, enquetes_map=enquetes_map, enquetes_created=enquetes_created)

        assert len(result["packages"]["closed_today"]) == 1
        pkg = result["packages"]["closed_today"][0]
        assert pkg["qty"] == 24
        assert pkg["poll_title"] == "Test Poll"
        assert len(pkg["votes"]) == 4

    def test_two_packages_closed_today(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = _make_votes("poll-B", [6] * 8, today + timedelta(hours=1))
        enquetes_created = {"poll-B": today}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        assert len(result["packages"]["closed_today"]) == 2
        for pkg in result["packages"]["closed_today"]:
            assert pkg["qty"] == 24


class TestClosedWeekPackages:
    def test_package_closed_yesterday_appears_in_week(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        votos = _make_votes("poll-C", [12, 12], yesterday + timedelta(hours=5))
        # poll criado ontem → fechado ontem
        enquetes_created = {"poll-C": yesterday}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        # Package closed yesterday does NOT appear in closed_today (last_ts < today_start)
        # but DOES appear in closed_week
        assert len(result["packages"]["closed_today"]) == 0
        assert len(result["packages"]["closed_week"]) == 1

    def test_package_closed_today_appears_in_both(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = _make_votes("poll-D", [24], today + timedelta(hours=1))
        enquetes_created = {"poll-D": today}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        assert len(result["packages"]["closed_today"]) == 1
        assert len(result["packages"]["closed_week"]) == 1


class TestOpenPackages:
    def test_leftover_goes_to_open(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        # 15 total → não fecha pacote de 24
        votos = _make_votes("poll-E", [10, 5], today + timedelta(hours=1))
        # poll criado hoje → aparece em open
        enquetes_created = {"poll-E": today}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        assert len(result["packages"]["open"]) == 1
        assert result["packages"]["open"][0]["qty"] == 15

    def test_no_votes_means_no_packages(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)

        result = analyze_votos([], dates, enquetes_map={})

        assert result["packages"]["open"] == []
        assert result["packages"]["closed_today"] == []
        assert result["packages"]["closed_week"] == []


class TestPackageVoteDetails:
    def test_package_vote_entries_contain_name_phone_qty(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = _make_votes("poll-F", [12, 12], today + timedelta(hours=1))
        enquetes_created = {"poll-F": today}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        pkg = result["packages"]["closed_today"][0]
        for i, vote in enumerate(pkg["votes"]):
            assert "name" in vote
            assert vote["name"] == f"Voter {i}"
            assert "phone" in vote
            assert vote["phone"] == f"551198888{i:04d}"
            assert "qty" in vote
            assert isinstance(vote["qty"], int)


class TestRemovedVotesInPackages:
    def test_removed_vote_not_in_open_package(self):
        now = datetime(2026, 2, 15, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph-1", "voterName": "V1",
             "qty": "12", "timestamp": _ts(today + timedelta(hours=1))},
            {"id": 2, "pollId": "p", "voterPhone": "ph-2", "voterName": "V2",
             "qty": "6", "timestamp": _ts(today + timedelta(hours=2))},
            {"id": 3, "pollId": "p", "voterPhone": "ph-2", "voterName": "V2",
             "qty": "0", "timestamp": _ts(today + timedelta(hours=3))},
        ]
        enquetes_created = {"p": today}

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        if result["packages"]["open"]:
            total_open = result["packages"]["open"][0]["qty"]
            assert total_open == 12

class TestPackageFiltering:
    def test_packages_without_opened_at_are_filtered(self):
        now = datetime(2026, 3, 22, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        # 24 total votes → would close a package
        votos = _make_votes("poll-NoDate", [24], today + timedelta(hours=1))
        
        # enquetes_created does NOT contain poll-NoDate
        enquetes_created = {} 

        result = analyze_votos(votos, dates, enquetes_map={}, enquetes_created=enquetes_created)

        # Should be empty because it has no opened_at
        assert result["packages"]["closed_today"] == []
        assert result["packages"]["closed_week"] == []
        assert result["packages"]["open"] == []
