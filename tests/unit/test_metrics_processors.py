"""Testes extras para metrics/processors.py.

Cobre gaps de cobertura não tratados nos arquivos existentes:
  - _app_timezone (linhas 38-43): TZ inválido → fallback UTC
  - _to_local_naive (linhas 46-49): aware → naive
  - get_packages_closed_cutoff (linhas 105-109): env presente/ausente
  - preserve_package_metadata (linhas 111-179): thumbnails, PDF, votos Asaas
  - analyze_enquetes: ativo_now com status != 'open', field_171 fallback
  - _extract_title_from_raw: todos os paths de rawJson
  - VoteProcessor.calculate_packages: múltiplos pacotes + waitlist
  - analyze_votos: closed_cutoff, closed_last7, F-044 métricas, customer save falha
"""

import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from metrics import processors
from metrics.processors import (
    _app_timezone,
    _to_local_naive,
    build_drive_image_url,
    resolve_enquete_drive_file_id,
    get_packages_closed_cutoff,
    preserve_package_metadata,
    analyze_enquetes,
    analyze_votos,
    get_date_range,
    VoteProcessor,
    parse_timestamp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


@pytest.fixture(autouse=True)
def _isolate_customer_store(monkeypatch):
    """Impede leitura/escrita real de clientes em todos os testes deste arquivo."""
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: {})
    monkeypatch.setattr("app.services.customer_service.save_customers", lambda _data: None)


# ---------------------------------------------------------------------------
# _app_timezone
# ---------------------------------------------------------------------------

class TestAppTimezone:
    def test_returns_valid_timezone_for_sao_paulo(self, monkeypatch):
        monkeypatch.setenv("TZ", "America/Sao_Paulo")
        tz = _app_timezone()
        assert tz.key == "America/Sao_Paulo"

    def test_fallback_to_utc_for_invalid_tz(self, monkeypatch):
        """TZ inválido → ZoneInfo lança → fallback é timezone.utc."""
        monkeypatch.setenv("TZ", "Nao/Existe_Timezone_Invalido")
        tz = _app_timezone()
        assert tz is timezone.utc

    def test_default_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        tz = _app_timezone()
        # deve ser um ZoneInfo válido (America/Sao_Paulo)
        assert hasattr(tz, "key")


# ---------------------------------------------------------------------------
# _to_local_naive
# ---------------------------------------------------------------------------

class TestToLocalNaive:
    def test_naive_datetime_returned_unchanged(self):
        dt = datetime(2026, 3, 10, 12, 0, 0)
        assert _to_local_naive(dt) == dt

    def test_aware_datetime_converted_to_naive(self):
        dt_utc = datetime(2026, 3, 10, 15, 0, 0, tzinfo=timezone.utc)
        result = _to_local_naive(dt_utc)
        assert result.tzinfo is None
        # BRT = UTC-3, então 15:00 UTC → 12:00 BRT (naive)
        assert result.hour == 12


# ---------------------------------------------------------------------------
# build_drive_image_url
# ---------------------------------------------------------------------------

class TestBuildDriveImageUrl:
    def test_returns_none_for_falsy(self):
        assert build_drive_image_url(None) is None
        assert build_drive_image_url("") is None

    def test_returns_url_with_id(self):
        url = build_drive_image_url("abc123")
        assert url == "/files/abc123"


# ---------------------------------------------------------------------------
# resolve_enquete_drive_file_id
# ---------------------------------------------------------------------------

class TestResolveEnqueteDriveFileId:
    def test_prefers_enquete_over_produto(self):
        result = resolve_enquete_drive_file_id(
            {"drive_file_id": "enq-id"},
            {"drive_file_id": "prod-id"},
        )
        assert result == "enq-id"

    def test_falls_back_to_produto(self):
        result = resolve_enquete_drive_file_id(
            {"drive_file_id": ""},
            {"drive_file_id": "prod-id"},
        )
        assert result == "prod-id"

    def test_returns_none_when_both_empty(self):
        result = resolve_enquete_drive_file_id(
            {"drive_file_id": ""},
            {"drive_file_id": ""},
        )
        assert result is None

    def test_handles_non_dict_inputs(self):
        assert resolve_enquete_drive_file_id(None, None) is None
        assert resolve_enquete_drive_file_id("string", None) is None


# ---------------------------------------------------------------------------
# get_packages_closed_cutoff
# ---------------------------------------------------------------------------

class TestGetPackagesClosedCutoff:
    def test_returns_none_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        assert get_packages_closed_cutoff() is None

    def test_returns_none_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("METRICS_MIN_DATE", "")
        assert get_packages_closed_cutoff() is None

    def test_parses_iso_date(self, monkeypatch):
        monkeypatch.setenv("METRICS_MIN_DATE", "2026-03-01T00:00:00+00:00")
        result = get_packages_closed_cutoff()
        assert result is not None
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# preserve_package_metadata
# ---------------------------------------------------------------------------

class TestPreservePackageMetadata:
    def test_noop_when_old_data_empty(self):
        new_data = {"votos": {"packages": {"open": [{"id": "p1", "image": "/img"}]}}}
        preserve_package_metadata(new_data, {})
        # deve não lançar e não alterar
        assert new_data["votos"]["packages"]["open"][0].get("image_thumb") is None

    def test_noop_when_new_data_empty(self):
        old_data = {"votos": {"packages": {"open": [{"id": "p1", "image": "/img", "image_thumb": "thumb"}]}}}
        preserve_package_metadata({}, old_data)  # não deve lançar

    def test_restores_thumbnail_by_image_url(self):
        old_data = {
            "votos": {
                "packages": {
                    "open": [{"id": "p1", "image": "/img/abc", "image_thumb": "thumb-abc"}]
                }
            }
        }
        new_data = {
            "votos": {
                "packages": {
                    "open": [{"id": "p1", "image": "/img/abc"}]
                }
            }
        }
        preserve_package_metadata(new_data, old_data)
        assert new_data["votos"]["packages"]["open"][0]["image_thumb"] == "thumb-abc"

    def test_does_not_overwrite_existing_thumb(self):
        old_data = {
            "votos": {
                "packages": {
                    "open": [{"id": "p1", "image": "/img/abc", "image_thumb": "old-thumb"}]
                }
            }
        }
        new_data = {
            "votos": {
                "packages": {
                    "open": [{"id": "p1", "image": "/img/abc", "image_thumb": "new-thumb"}]
                }
            }
        }
        preserve_package_metadata(new_data, old_data)
        assert new_data["votos"]["packages"]["open"][0]["image_thumb"] == "new-thumb"

    def test_restores_pdf_fields_by_package_id(self):
        old_data = {
            "votos": {
                "packages": {
                    "closed_today": [
                        {
                            "id": "pkg-1",
                            "pdf_status": "sent",
                            "pdf_file_name": "file.pdf",
                            "votes": [],
                        }
                    ]
                }
            }
        }
        new_data = {
            "votos": {
                "packages": {
                    "closed_today": [
                        {"id": "pkg-1", "votes": []}
                    ]
                }
            }
        }
        preserve_package_metadata(new_data, old_data)
        pkg = new_data["votos"]["packages"]["closed_today"][0]
        assert pkg["pdf_status"] == "sent"
        assert pkg["pdf_file_name"] == "file.pdf"

    def test_restores_asaas_payment_fields_by_phone(self):
        old_data = {
            "votos": {
                "packages": {
                    "closed_today": [
                        {
                            "id": "pkg-2",
                            "votes": [
                                {
                                    "phone": "5511999990001",
                                    "asaas_payment_id": "pay-abc",
                                    "asaas_payment_status": "CONFIRMED",
                                }
                            ],
                        }
                    ]
                }
            }
        }
        new_data = {
            "votos": {
                "packages": {
                    "closed_today": [
                        {
                            "id": "pkg-2",
                            "votes": [{"phone": "5511999990001", "name": "A"}],
                        }
                    ]
                }
            }
        }
        preserve_package_metadata(new_data, old_data)
        vote = new_data["votos"]["packages"]["closed_today"][0]["votes"][0]
        assert vote["asaas_payment_id"] == "pay-abc"
        assert vote["asaas_payment_status"] == "CONFIRMED"

    def test_non_dict_votos_new_returns_early(self):
        old_data = {"votos": {}}
        new_data = {"votos": "not-a-dict"}
        preserve_package_metadata(new_data, old_data)  # não deve lançar

    def test_non_dict_packages_new_returns_early(self):
        old_data = {"votos": {"packages": {}}}
        new_data = {"votos": {"packages": "not-a-dict"}}
        preserve_package_metadata(new_data, old_data)  # não deve lançar


# ---------------------------------------------------------------------------
# analyze_enquetes: active_now + status != 'open'
# ---------------------------------------------------------------------------

class TestAnalyzeEnquetesActiveNow:
    def test_closed_enquete_not_counted_as_active(self):
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            {
                "createdAtTs": _ts(now - timedelta(hours=10)),
                "status": "closed",
            },
            {
                "createdAtTs": _ts(now - timedelta(hours=10)),
                "status": "open",
            },
        ]
        result = analyze_enquetes(enquetes, dates)
        # Somente a enquete open criada nas últimas 72h é ativa
        assert result["active_now"] == 1

    def test_open_enquete_older_than_72h_not_active(self):
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            {
                "createdAtTs": _ts(now - timedelta(hours=73)),
                "status": "open",
            },
        ]
        result = analyze_enquetes(enquetes, dates)
        assert result["active_now"] == 0

    def test_field_171_used_when_createdAtTs_absent(self):
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)

        enquetes = [
            {"field_171": _ts(now - timedelta(hours=5)), "status": "open"},
        ]
        result = analyze_enquetes(enquetes, dates)
        assert result["active_now"] == 1

    def test_active_now_with_all_open_recent(self):
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)
        enquetes = [
            {"createdAtTs": _ts(now - timedelta(hours=i)), "status": "open"}
            for i in range(1, 5)
        ]
        result = analyze_enquetes(enquetes, dates)
        assert result["active_now"] == 4

    def test_status_defaults_to_open_when_absent(self):
        """Enquete sem campo status deve ser tratada como open (default)."""
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)
        enquetes = [
            {"createdAtTs": _ts(now - timedelta(hours=1))},  # sem status
        ]
        result = analyze_enquetes(enquetes, dates)
        assert result["active_now"] == 1

    def test_pct_avg_nonzero_when_history_exists(self):
        """pct_avg > 0 quando hoje > média dos últimos 7 dias."""
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        yesterday = dates["yesterday_start"]

        # 3 hoje, 1 ontem
        enquetes = [
            {"createdAtTs": _ts(today + timedelta(hours=h))} for h in [1, 2, 3]
        ] + [
            {"createdAtTs": _ts(yesterday + timedelta(hours=1))}
        ]
        result = analyze_enquetes(enquetes, dates)
        # avg_7_days = 1/7 ≈ 0.14, today=3 → pct_avg > 0
        assert result["pct_avg"] > 0
        assert result["today"] == 3


# ---------------------------------------------------------------------------
# _extract_title_from_raw: paths do rawJson
# ---------------------------------------------------------------------------

class TestExtractTitleFromRaw:
    """Testa _extract_title_from_raw via analyze_votos (função interna)."""

    def _run_with_rawjson(self, raw_json_str, now=None):
        now = now or datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        votos = [
            {
                "id": 1,
                "pollId": "p-test",
                "voterPhone": "5511999",
                "voterName": "V",
                "qty": "3",
                "timestamp": _ts(today + timedelta(hours=1)),
                "rawJson": raw_json_str,
            }
        ]
        # p-test não está no mapa; deve ser preenchido pelo rawJson
        enquetes_map = {}
        analyze_votos(votos, dates, enquetes_map)
        return enquetes_map.get("p-test")

    def test_direct_poll_path(self):
        raw = json.dumps({"poll": {"title": "Titulo Direto"}})
        title = self._run_with_rawjson(raw)
        assert title == "Titulo Direto"

    def test_body_poll_path(self):
        raw = json.dumps({"body": {"poll": {"title": "Titulo Body"}}})
        title = self._run_with_rawjson(raw)
        assert title == "Titulo Body"

    def test_messages_updates_path(self):
        raw = json.dumps({
            "body": {
                "messages_updates": [
                    {"poll": {"title": "Titulo MsgUpdate"}}
                ]
            }
        })
        title = self._run_with_rawjson(raw)
        assert title == "Titulo MsgUpdate"

    def test_after_update_path(self):
        raw = json.dumps({
            "body": {
                "messages_updates": [
                    {"after_update": {"poll": {"title": "Titulo AfterUpdate"}}}
                ]
            }
        })
        title = self._run_with_rawjson(raw)
        assert title == "Titulo AfterUpdate"

    def test_before_update_path(self):
        raw = json.dumps({
            "body": {
                "messages_updates": [
                    {
                        "before_update": {"poll": {"title": "Titulo BeforeUpdate"}},
                    }
                ]
            }
        })
        title = self._run_with_rawjson(raw)
        assert title == "Titulo BeforeUpdate"

    def test_invalid_json_returns_none(self):
        title = self._run_with_rawjson("nao é json {{{")
        assert title is None

    def test_non_dict_json_returns_none(self):
        title = self._run_with_rawjson(json.dumps([1, 2, 3]))
        assert title is None

    def test_empty_body_returns_none(self):
        raw = json.dumps({"body": {}})
        title = self._run_with_rawjson(raw)
        assert title is None

    def test_existing_map_entry_not_overwritten(self):
        """Se poll_id já está no mapa, rawJson não deve sobrescrever."""
        now = datetime(2026, 3, 20, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        votos = [
            {
                "id": 1,
                "pollId": "p-exists",
                "voterPhone": "5511",
                "voterName": "V",
                "qty": "3",
                "timestamp": _ts(today + timedelta(hours=1)),
                "rawJson": json.dumps({"poll": {"title": "Titulo do Raw"}}),
            }
        ]
        enquetes_map = {"p-exists": {"title": "Titulo Original", "drive_file_id": None, "chat_id": None}}
        analyze_votos(votos, dates, enquetes_map)
        # Mapa ainda deve ter o valor original (dict)
        assert enquetes_map["p-exists"]["title"] == "Titulo Original"


# ---------------------------------------------------------------------------
# VoteProcessor: múltiplos pacotes + waitlist
# ---------------------------------------------------------------------------

class TestVoteProcessorCalculatePackages:
    def test_no_votes_leaves_empty_structures(self):
        vp = VoteProcessor()
        vp.calculate_packages(limit=24)
        assert vp.closed_packages == {}
        assert vp.waitlist == {}

    def test_exact_two_packages_formed(self):
        vp = VoteProcessor()
        votes = [
            {"id": i, "pollId": "p", "voterPhone": f"ph{i}", "qty": str(q), "timestamp": str(i)}
            for i, q in enumerate([6, 6, 6, 6, 6, 6, 6, 6], 1)
        ]
        for v in votes:
            vp.process_vote(v)
        vp.calculate_packages(limit=24)
        assert len(vp.closed_packages["p"]) == 2
        assert vp.waitlist["p"] == []

    def test_leftover_goes_to_waitlist(self):
        vp = VoteProcessor()
        # 6+6+6+6 = 24 fecha 1 pacote; 5 sobra no waitlist
        votes = [
            {"id": i, "pollId": "p", "voterPhone": f"ph{i}", "qty": str(q), "timestamp": str(i)}
            for i, q in enumerate([6, 6, 6, 6, 5], 1)
        ]
        for v in votes:
            vp.process_vote(v)
        vp.calculate_packages(limit=24)
        assert len(vp.closed_packages["p"]) == 1
        assert len(vp.waitlist["p"]) == 1

    def test_process_vote_remove_from_stack(self):
        vp = VoteProcessor()
        v1 = {"id": 1, "pollId": "p", "voterPhone": "ph1", "qty": "6", "timestamp": "1"}
        v2 = {"id": 2, "pollId": "p", "voterPhone": "ph1", "qty": "0", "timestamp": "2"}  # remove
        vp.process_vote(v1)
        status, _ = vp.process_vote(v2)
        assert status == "removed"
        assert vp.poll_votes["p"]["ph1"] == []

    def test_sort_key_handles_invalid_qty(self):
        """_sort_key com qty não-numérico não deve lançar erro."""
        vp = VoteProcessor()
        v = {"id": 1, "pollId": "p", "voterPhone": "ph1", "qty": "nao-numero", "timestamp": "1"}
        vp.process_vote(v)  # não deve lançar
        vp.calculate_packages(limit=24)


# ---------------------------------------------------------------------------
# analyze_votos: closed_cutoff branch
# ---------------------------------------------------------------------------

class TestAnalyzeVotosClosedCutoff:
    def test_closed_cutoff_filters_old_packages(self, monkeypatch):
        """Com METRICS_MIN_DATE → pacotes fechados antes disso devem ser excluídos."""
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        # cutoff = hoje
        monkeypatch.setenv("METRICS_MIN_DATE", now.replace(hour=0).isoformat())

        votos = [
            {
                "id": i,
                "pollId": "p",
                "voterPhone": f"ph{i}",
                "voterName": f"V{i}",
                "qty": "6",
                "timestamp": _ts(yesterday + timedelta(hours=i)),
            }
            for i in range(1, 5)
        ]
        enquetes_created = {"p": yesterday}

        result = analyze_votos(votos, dates, {}, enquetes_created=enquetes_created)
        # Pacote fechado ontem, mas cutoff é hoje → não entra em closed_today
        assert result["packages"]["closed_today"] == []

    def test_closed_cutoff_includes_recent_packages(self, monkeypatch):
        """Com METRICS_MIN_DATE no passado → pacote de hoje deve entrar em closed_today."""
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        # cutoff = 5 dias atrás
        cutoff = (today - timedelta(days=5)).isoformat()
        monkeypatch.setenv("METRICS_MIN_DATE", cutoff)

        votos = [
            {
                "id": i,
                "pollId": "p",
                "voterPhone": f"ph{i}",
                "voterName": f"V{i}",
                "qty": "6",
                "timestamp": _ts(today + timedelta(hours=i)),
            }
            for i in range(1, 5)
        ]
        enquetes_created = {"p": today}

        result = analyze_votos(votos, dates, {}, enquetes_created=enquetes_created)
        assert len(result["packages"]["closed_today"]) == 1


# ---------------------------------------------------------------------------
# analyze_votos: closed_last7 e packages_summary
# ---------------------------------------------------------------------------

class TestAnalyzeVotosClosedLast7:
    def test_packages_summary_contains_yesterday_count(self, monkeypatch):
        """packages_summary['yesterday'] deve contar pacotes fechados ontem."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        # 4 votos que fecham 1 pacote de 24 ontem
        votos = [
            {
                "id": i,
                "pollId": "p",
                "voterPhone": f"ph{i}",
                "voterName": f"V{i}",
                "qty": "6",
                "timestamp": _ts(yesterday + timedelta(hours=i)),
            }
            for i in range(1, 5)
        ]
        enquetes_created = {"p": yesterday}

        result = analyze_votos(votos, dates, {}, enquetes_created=enquetes_created)
        assert result["packages_summary"]["yesterday"] == 1
        assert result["packages_summary"]["today"] == 0

    def test_packages_summary_keys_present(self, monkeypatch):
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)

        result = analyze_votos([], dates, {})

        expected_keys = {"today", "yesterday", "avg_7_days", "last_7_days", "same_weekday_last_week"}
        assert expected_keys.issubset(result["packages_summary"].keys())


# ---------------------------------------------------------------------------
# analyze_votos: F-044 métricas (pct_vs_yesterday_same_hour, etc.)
# ---------------------------------------------------------------------------

class TestAnalyzeVotosF044Metrics:
    def test_pct_vs_yesterday_same_hour_computed(self, monkeypatch):
        """Com votos hoje e ontem, pct_vs_yesterday_same_hour deve ser calculado."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 10, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        yesterday = dates["yesterday_start"]

        # 2 votos hoje às 8h e 9h
        # 1 voto ontem às 8h (dentro de "mesma hora")
        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph1", "voterName": "V1", "qty": "3",
             "timestamp": _ts(today + timedelta(hours=8))},
            {"id": 2, "pollId": "p", "voterPhone": "ph2", "voterName": "V2", "qty": "3",
             "timestamp": _ts(today + timedelta(hours=9))},
            {"id": 3, "pollId": "p", "voterPhone": "ph3", "voterName": "V3", "qty": "3",
             "timestamp": _ts(yesterday + timedelta(hours=8))},
        ]

        result = analyze_votos(votos, dates, {})

        # today_so_far = 2, yesterday_until_same_hour = 1 → pct = 100.0
        assert result["today_so_far"] == 2
        assert result["yesterday_until_same_hour"] == 1
        assert result["pct_vs_yesterday_same_hour"] == 100.0

    def test_pct_none_when_no_baseline(self, monkeypatch):
        """Sem votos ontem, pct_vs_yesterday_same_hour deve ser None."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 10, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        votos = [
            {"id": 1, "pollId": "p", "voterPhone": "ph1", "voterName": "V1", "qty": "3",
             "timestamp": _ts(today + timedelta(hours=8))},
        ]
        result = analyze_votos(votos, dates, {})
        assert result["pct_vs_yesterday_same_hour"] is None

    def test_week_to_date_and_last_week_same_point(self, monkeypatch):
        """week_to_date e last_week_same_point devem ser calculados corretamente."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 10, 0, 0)
        dates = get_date_range(now=now)
        this_week_start = dates["this_week_start"]
        last_week_start = this_week_start - timedelta(days=7)

        # 3 votos na semana atual, 2 na semana passada
        votos = [
            {"id": i, "pollId": "p", "voterPhone": f"ph{i}", "voterName": f"V{i}", "qty": "3",
             "timestamp": _ts(this_week_start + timedelta(hours=i))}
            for i in range(1, 4)
        ] + [
            {"id": 10 + i, "pollId": "p", "voterPhone": f"ph1{i}", "voterName": f"V1{i}", "qty": "3",
             "timestamp": _ts(last_week_start + timedelta(hours=i))}
            for i in range(1, 3)
        ]
        result = analyze_votos(votos, dates, {})
        assert result["week_to_date"] == 3
        assert result["last_week_same_point"] == 2

    def test_pct_vs_monthly_avg_with_data(self, monkeypatch):
        """pct_vs_monthly_avg deve ser calculado quando há dados mensais."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 10, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        # 4 votos hoje
        votos_hoje = [
            {"id": i, "pollId": "p", "voterPhone": f"ph{i}", "voterName": f"V{i}", "qty": "3",
             "timestamp": _ts(today + timedelta(hours=i))}
            for i in range(1, 5)
        ]
        # 2 votos em cada uma das 4 semanas anteriores (mesmo dia da semana)
        votos_historico = []
        for w in range(1, 5):
            ref_day = today - timedelta(days=7 * w)
            for h in range(1, 3):
                votos_historico.append({
                    "id": 100 + w * 10 + h,
                    "pollId": "p",
                    "voterPhone": f"hist-{w}-{h}",
                    "voterName": f"H{w}{h}",
                    "qty": "3",
                    "timestamp": _ts(ref_day + timedelta(hours=h)),
                })

        result = analyze_votos(votos_hoje + votos_historico, dates, {})
        # today_so_far=4, avg_monthly_same_weekday=2 → pct = (4-2)/2*100 = 100
        assert result["pct_vs_monthly_avg"] == 100.0

    def test_invalid_qty_in_last7_does_not_crash(self, monkeypatch):
        """qty inválido na listagem votes_last7 deve ser tratado sem lançar."""
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 10, 0, 0)
        dates = get_date_range(now=now)
        yesterday = dates["yesterday_start"]

        votos = [
            {
                "id": 1,
                "pollId": "p",
                "voterPhone": "ph1",
                "voterName": "V1",
                "qty": "nao-numero",
                "timestamp": _ts(yesterday + timedelta(hours=1)),
            }
        ]
        result = analyze_votos(votos, dates, {})
        # Deve executar sem erro; qty inválido → conta como 0 → não incrementa last7
        assert isinstance(result["last_7_days"], list)


# ---------------------------------------------------------------------------
# analyze_votos: customer save falha não propaga
# ---------------------------------------------------------------------------

class TestAnalyzeVotosCustomerSaveFails:
    def test_save_customers_exception_does_not_propagate(self, monkeypatch):
        """Erro ao salvar clientes não deve derrubar a análise."""
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]

        monkeypatch.setattr(
            "app.services.customer_service.load_customers", lambda: {}
        )
        monkeypatch.setattr(
            "app.services.customer_service.save_customers",
            MagicMock(side_effect=Exception("Disk full")),
        )
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)

        votos = [
            {
                "id": 1,
                "pollId": "p",
                "voterPhone": "5511999990001",
                "voterName": "Novo Cliente",
                "qty": "3",
                "timestamp": _ts(today + timedelta(hours=1)),
            }
        ]
        result = analyze_votos(votos, dates, {})
        assert result["today"] == 1  # análise completou normalmente


# ---------------------------------------------------------------------------
# analyze_votos: _is_useful_name
# ---------------------------------------------------------------------------

class TestIsUsefulName:
    """Testa _is_useful_name via comportamento de by_customer_today."""

    def _run(self, voter_name, monkeypatch):
        monkeypatch.delenv("METRICS_MIN_DATE", raising=False)
        now = datetime(2026, 3, 25, 12, 0, 0)
        dates = get_date_range(now=now)
        today = dates["today_start"]
        votos = [
            {
                "id": 1,
                "pollId": "p",
                "voterPhone": "5511999990001",
                "voterName": voter_name,
                "qty": "3",
                "timestamp": _ts(today + timedelta(hours=1)),
            }
        ]
        return analyze_votos(votos, dates, {})

    def test_useful_name_captured(self, monkeypatch):
        result = self._run("João Silva", monkeypatch)
        assert result["today"] == 1

    def test_desconhecido_not_useful(self, monkeypatch):
        """'Desconhecido' não deve ser salvo como nome útil."""
        monkeypatch.setattr(
            "app.services.customer_service.load_customers", lambda: {}
        )
        saved = {}
        monkeypatch.setattr(
            "app.services.customer_service.save_customers",
            lambda d: saved.update(d),
        )
        self._run("Desconhecido", monkeypatch)
        assert "5511999990001" not in saved

    def test_none_name_not_useful(self, monkeypatch):
        result = self._run(None, monkeypatch)
        # Análise não deve lançar
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# parse_timestamp: milissegundos (branch ts > 10_000_000_000)
# ---------------------------------------------------------------------------

class TestParseTimestampMillis:
    def test_milliseconds_timestamp_parsed(self):
        """Timestamps em ms devem ser divididos por 1000."""
        dt = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        ts_ms = int(dt.timestamp() * 1000)
        result = parse_timestamp(str(ts_ms))
        assert result is not None
        # Hora pode variar por timezone, mas não deve ser None
        assert isinstance(result, datetime)

    def test_already_datetime_returned_as_naive(self):
        dt = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        result = parse_timestamp(dt)
        assert result is not None
        assert result.tzinfo is None
