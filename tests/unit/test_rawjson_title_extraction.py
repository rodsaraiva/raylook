"""Tests for rawJson title extraction paths inside analyze_votos."""
import json
from datetime import datetime, timedelta

from metrics.processors import get_date_range, analyze_votos


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def _make_vote(poll_id, raw_json_obj, now):
    return {
        "id": 1,
        "pollId": poll_id,
        "voterPhone": "phone-1",
        "voterName": "Voter",
        "qty": "3",
        "timestamp": _ts(now),
        "rawJson": json.dumps(raw_json_obj) if raw_json_obj is not None else None,
    }


class TestRawJsonTitleExtraction:
    """Cover all 3 extraction paths + fallback scenarios."""

    def setup_method(self):
        self.now = datetime(2026, 2, 15, 12, 0, 0)
        self.dates = get_date_range(now=self.now)

    def test_path1_poll_at_root(self):
        raw = {"poll": {"title": "Root Title"}}
        vote = _make_vote("poll-root", raw, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {}

        analyze_votos([vote], self.dates, enquetes_map)

        assert enquetes_map["poll-root"] == "Root Title"

    def test_path2_body_poll(self):
        raw = {"body": {"poll": {"title": "Body Title"}}}
        vote = _make_vote("poll-body", raw, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {}

        analyze_votos([vote], self.dates, enquetes_map)

        assert enquetes_map["poll-body"] == "Body Title"

    def test_path3_body_messages_updates(self):
        raw = {"body": {"messages_updates": [{"poll": {"title": "Updates Title"}}]}}
        vote = _make_vote("poll-updates", raw, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {}

        analyze_votos([vote], self.dates, enquetes_map)

        assert enquetes_map["poll-updates"] == "Updates Title"

    def test_no_rawjson_field_leaves_map_unchanged(self):
        vote = _make_vote("poll-no-raw", None, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {}

        analyze_votos([vote], self.dates, enquetes_map)

        assert "poll-no-raw" not in enquetes_map

    def test_malformed_rawjson_does_not_crash(self):
        vote = {
            "id": 1,
            "pollId": "poll-bad",
            "voterPhone": "p1",
            "voterName": "V",
            "qty": "3",
            "timestamp": _ts(self.dates["today_start"] + timedelta(hours=1)),
            "rawJson": "NOT VALID JSON {{{",
        }
        enquetes_map = {}

        # Must not raise
        result = analyze_votos([vote], self.dates, enquetes_map)
        assert "poll-bad" not in enquetes_map
        assert isinstance(result, dict)

    def test_rawjson_with_empty_body_dict(self):
        raw = {"body": {}}
        vote = _make_vote("poll-empty-body", raw, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {}

        analyze_votos([vote], self.dates, enquetes_map)

        assert "poll-empty-body" not in enquetes_map

    def test_existing_title_in_map_is_not_overwritten(self):
        raw = {"poll": {"title": "New Title"}}
        vote = _make_vote("poll-exists", raw, self.dates["today_start"] + timedelta(hours=1))
        enquetes_map = {"poll-exists": "Original Title"}

        analyze_votos([vote], self.dates, enquetes_map)

        assert enquetes_map["poll-exists"] == "Original Title"

    def test_rawjson_is_a_list_not_dict(self):
        vote = {
            "id": 1,
            "pollId": "poll-list",
            "voterPhone": "p1",
            "voterName": "V",
            "qty": "3",
            "timestamp": _ts(self.dates["today_start"] + timedelta(hours=1)),
            "rawJson": json.dumps([1, 2, 3]),
        }
        enquetes_map = {}

        result = analyze_votos([vote], self.dates, enquetes_map)
        assert "poll-list" not in enquetes_map
        assert isinstance(result, dict)
