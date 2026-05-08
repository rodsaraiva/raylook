from datetime import datetime

from metrics import services


def test_generate_metrics_uses_env_table_ids_and_builds_payload(monkeypatch):
    monkeypatch.setenv("BASEROW_TABLE_ENQUETES", "18")
    monkeypatch.setenv("BASEROW_TABLE_VOTOS", "17")
    monkeypatch.setattr(services.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(services.settings, "TEST_MODE", False)
    monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)

    calls = []

    enquetes_rows = [{"pollId": "poll-1", "title": "Titulo 1"}]
    votos_rows = [{"id": 1, "pollId": "poll-1", "qty": "1"}]

    def fake_fetch_all_rows(table_id):
        calls.append(table_id)
        if table_id == "18":
            return enquetes_rows
        if table_id == "17":
            return votos_rows
        return []

    fixed_now = datetime(2026, 2, 15, 12, 0, 0)
    fake_dates = {"now": fixed_now}
    fake_dates.update(
        {
            "today_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
            "yesterday_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
            "yesterday_end": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
            "week_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
            "day24h_start": fixed_now,
        }
    )

    monkeypatch.setattr(services.clients, "fetch_all_rows", fake_fetch_all_rows)
    monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
    monkeypatch.setattr(services.processors, "analyze_enquetes", lambda enquetes, dates: {"ok_enquetes": True})

    captured_map = {}

    def fake_analyze_votos(votos, dates, enquetes_map, enquetes_created=None):
        captured_map.update(enquetes_map)
        return {"ok_votos": True}

    monkeypatch.setattr(services.processors, "analyze_votos", fake_analyze_votos)

    result = services.generate_metrics()

    assert calls == ["18", "17"]
    assert result["generated_at"] == fixed_now.isoformat()
    assert result["enquetes"] == {"ok_enquetes": True}
    assert result["votos"] == {"ok_votos": True}
    assert captured_map == {
        "poll-1": {"title": "Titulo 1", "drive_file_id": None, "chat_id": None}
    }


def test_generate_metrics_supabase_uses_paginated_metric_clients(monkeypatch):
    fixed_now = datetime(2026, 3, 23, 15, 0, 0)
    fake_dates = {
        "now": fixed_now,
        "today_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "yesterday_start": fixed_now.replace(day=22, hour=0, minute=0, second=0, microsecond=0),
        "yesterday_end": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week_start": fixed_now.replace(day=16, hour=0, minute=0, second=0, microsecond=0),
        "day24h_start": fixed_now.replace(day=22, hour=15, minute=0, second=0, microsecond=0),
    }

    monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
    monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_enquetes_for_metrics",
        lambda: [
            {
                "pollId": "poll-1",
                "title": "Titulo Supabase",
                "chatId": "120@g.us",
                "createdAtTs": "2026-03-23T12:00:00+00:00",
                "driveFileId": "drive-1",
                "status": "open",
            }
        ],
    )
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_votos_for_metrics",
        lambda: [
            {
                "id": "vote-1",
                "pollId": "poll-1",
                "voterPhone": "5511999999999",
                "voterName": "Cliente 1",
                "qty": 3,
                "timestamp": "2026-03-23T13:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_package_lists_for_metrics",
        lambda: {"open": [], "closed_today": [], "closed_week": [], "confirmed_today": []},
    )
    monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
    monkeypatch.setattr(services.processors, "analyze_enquetes", lambda enquetes, dates: {"ok_enquetes": enquetes})

    captured = {}

    def fake_analyze_votos(votos, dates, enquetes_map, enquetes_created=None):
        captured["votos"] = votos
        captured["enquetes_map"] = enquetes_map
        captured["enquetes_created"] = enquetes_created
        return {"ok_votos": True}

    monkeypatch.setattr(services.processors, "analyze_votos", fake_analyze_votos)

    # Mock sales_temperature_service to avoid DB calls
    monkeypatch.setattr(
        "app.services.sales_temperature_service.get_temperature",
        lambda force_refresh=False: {"label": "mock", "value": 0},
    )
    monkeypatch.setattr(
        "app.services.sales_temperature_service.compute_confirmed_extras",
        lambda: {},
    )
    # Mock _enrich_enquetes_from_snapshots to avoid DB calls
    monkeypatch.setattr(services, "_enrich_enquetes_from_snapshots", lambda *args, **kwargs: None)

    result = services.generate_metrics()

    assert result["generated_at"] == fixed_now.isoformat()
    # enquetes now includes extra fields from _enrich (mocked to no-op)
    ok_enquetes = result["enquetes"]["ok_enquetes"]
    assert len(ok_enquetes) == 1
    assert ok_enquetes[0]["pollId"] == "poll-1"
    # votos now includes packages_summary_confirmed with sales_temperature from F-050
    assert result["votos"]["ok_votos"] is True
    assert captured["votos"] == [
        {
            "pollId": "poll-1",
            "voterPhone": "5511999999999",
            "voterName": "Cliente 1",
            "qty": "3",
            "timestamp": "2026-03-23T13:00:00+00:00",
            "rawJson": None,
        }
    ]
    assert captured["enquetes_map"] == {
        "poll-1": {
            "title": "Titulo Supabase",
            "drive_file_id": "drive-1",
            "chat_id": "120@g.us",
        }
    }
    assert "poll-1" in captured["enquetes_created"]


def test_generate_metrics_applies_metrics_min_date_filter(monkeypatch):
    fixed_now = datetime(2026, 3, 23, 15, 0, 0)
    fake_dates = {
        "now": fixed_now,
        "today_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "yesterday_start": fixed_now.replace(day=22, hour=0, minute=0, second=0, microsecond=0),
        "yesterday_end": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week_start": fixed_now.replace(day=16, hour=0, minute=0, second=0, microsecond=0),
        "day24h_start": fixed_now.replace(day=22, hour=15, minute=0, second=0, microsecond=0),
    }
    monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
    monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-20T00:00:00-03:00")
    monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_enquetes_for_metrics",
        lambda: [
            {
                "pollId": "poll-old",
                "title": "Antiga",
                "chatId": "120@g.us",
                "createdAtTs": "2026-03-19T23:59:00-03:00",
                "driveFileId": None,
                "status": "open",
            },
            {
                "pollId": "poll-new",
                "title": "Nova",
                "chatId": "120@g.us",
                "createdAtTs": "2026-03-20T00:01:00-03:00",
                "driveFileId": None,
                "status": "open",
            },
        ],
    )
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_votos_for_metrics",
        lambda: [
            {
                "id": "vote-old",
                "pollId": "poll-old",
                "voterPhone": "5511",
                "voterName": "Antigo",
                "qty": 3,
                "timestamp": "2026-03-19T23:59:30-03:00",
            },
            {
                "id": "vote-new",
                "pollId": "poll-new",
                "voterPhone": "5522",
                "voterName": "Novo",
                "qty": 6,
                "timestamp": "2026-03-20T00:02:00-03:00",
            },
        ],
    )
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_package_lists_for_metrics",
        lambda: {"open": [], "closed_today": [], "closed_week": [], "confirmed_today": []},
    )
    monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
    monkeypatch.setattr(services.processors, "analyze_enquetes", lambda enquetes, dates: {"poll_ids": [e["pollId"] for e in enquetes]})
    monkeypatch.setattr(services.processors, "analyze_votos", lambda votos, dates, enquetes_map, enquetes_created=None: {"poll_ids": [v["pollId"] for v in votos]})
    # Mock extras
    monkeypatch.setattr("app.services.sales_temperature_service.get_temperature", lambda force_refresh=False: {})
    monkeypatch.setattr("app.services.sales_temperature_service.compute_confirmed_extras", lambda: {})
    monkeypatch.setattr(services, "_enrich_enquetes_from_snapshots", lambda *args, **kwargs: None)

    result = services.generate_metrics()

    # enquetes now includes extra keys from the supabase path (closed_packages_on_active etc.)
    assert result["enquetes"]["poll_ids"] == ["poll-new"]
    assert result["votos"]["poll_ids"] == ["poll-new"]


def test_generate_metrics_supabase_uses_live_supabase_package_lists_and_confirmed_summary(monkeypatch):
    fixed_now = datetime(2026, 3, 25, 13, 0, 0)
    fake_dates = {
        "now": fixed_now,
        "today_start": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "yesterday_start": fixed_now.replace(day=24, hour=0, minute=0, second=0, microsecond=0),
        "yesterday_end": fixed_now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week_start": fixed_now.replace(day=18, hour=0, minute=0, second=0, microsecond=0),
        "day24h_start": fixed_now.replace(day=24, hour=13, minute=0, second=0, microsecond=0),
    }

    monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
    monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(services.supabase_clients, "fetch_enquetes_for_metrics", lambda: [])
    monkeypatch.setattr(services.supabase_clients, "fetch_votos_for_metrics", lambda: [])
    monkeypatch.setattr(
        services.supabase_clients,
        "fetch_package_lists_for_metrics",
        lambda: {
            "packages": {
                "open": [{"id": "sb-open"}],
                "closed_today": [{"id": "sb-closed"}],
                "closed_week": [{"id": "sb-week"}],
                "confirmed_today": [{"id": "processor-closed"}],
            },
            "packages_summary_confirmed": {"today": 1, "yesterday": 2},
        },
    )
    monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
    monkeypatch.setattr(services.processors, "analyze_enquetes", lambda enquetes, dates: {"today": 0})
    monkeypatch.setattr(
        services.processors,
        "analyze_votos",
        lambda votos, dates, enquetes_map, enquetes_created=None: {
            "today": 0,
            "packages": {
                "open": [{"id": "processor-open"}],
                "closed_today": [{"id": "processor-closed"}],
                "closed_week": [{"id": "processor-week"}],
                "confirmed_today": [],
            },
            "packages_summary": {"today": 10},
        },
    )
    # Mock extras injected by F-050
    monkeypatch.setattr("app.services.sales_temperature_service.get_temperature", lambda force_refresh=False: {})
    monkeypatch.setattr("app.services.sales_temperature_service.compute_confirmed_extras", lambda: {})
    monkeypatch.setattr(services, "_enrich_enquetes_from_snapshots", lambda *args, **kwargs: None)

    result = services.generate_metrics()
    packages = result["votos"]["packages"]

    assert packages["open"] == [{"id": "sb-open"}]
    assert packages["closed_today"] == [{"id": "sb-closed"}]
    assert packages["closed_week"] == [{"id": "sb-week"}]
    assert packages["confirmed_today"] == [{"id": "processor-closed"}]
    assert result["votos"]["packages_summary"]["today"] == 10
    # packages_summary_confirmed now includes sales_temperature + extras from F-050
    assert result["votos"]["packages_summary_confirmed"]["today"] == 1
    assert result["votos"]["packages_summary_confirmed"]["yesterday"] == 2
