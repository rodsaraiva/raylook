"""Testes extras para metrics/services.py.

Cobre gaps de cobertura não tratados em test_services_generate_metrics.py e
test_services_edge_cases.py:
  - _rankings_from_approved_packages (linhas 69-136)
  - _metrics_min_datetime (linhas 139-147)
  - _filter_rows_since (linhas 150-169)
  - generate_metrics: TEST_MODE path, prometheus paths, supabase disabled errors
  - _enrich_enquetes_from_snapshots (linhas 383-471)
  - geração de métricas supabase com rankings, temperature e snapshots
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from metrics import services


# ---------------------------------------------------------------------------
# Helpers comuns
# ---------------------------------------------------------------------------

def _make_dates(now: datetime) -> dict:
    from metrics.processors import get_date_range
    return get_date_range(now=now)


def _noop_analyze_enquetes(enquetes, dates):
    return {"active_now": 2, "today": 0}


def _noop_analyze_votos(votos, dates, enquetes_map, enquetes_created=None):
    return {"ok": True}


# ---------------------------------------------------------------------------
# _metrics_min_datetime
# ---------------------------------------------------------------------------

class TestMetricsMinDatetime:
    def test_returns_none_when_setting_absent(self, monkeypatch):
        """Sem METRICS_MIN_DATE → retorna None."""
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
        assert services._metrics_min_datetime() is None

    def test_returns_none_for_empty_string(self, monkeypatch):
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "")
        assert services._metrics_min_datetime() is None

    def test_returns_datetime_for_valid_iso(self, monkeypatch):
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-01T00:00:00-03:00")
        result = services._metrics_min_datetime()
        assert result is not None
        assert isinstance(result, datetime)

    def test_returns_none_for_garbage_string(self, monkeypatch):
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "nao-é-data")
        assert services._metrics_min_datetime() is None


# ---------------------------------------------------------------------------
# _filter_rows_since
# ---------------------------------------------------------------------------

class TestFilterRowsSince:
    def test_no_floor_returns_all_rows(self, monkeypatch):
        """Sem METRICS_MIN_DATE todos as linhas passam."""
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
        rows = [{"timestamp": "2026-01-01T00:00:00+00:00"}, {"timestamp": "2025-01-01T00:00:00+00:00"}]
        result = services._filter_rows_since(rows, "timestamp")
        assert result == rows

    def test_filters_rows_before_floor(self, monkeypatch):
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-10T00:00:00+00:00")
        rows = [
            {"timestamp": "2026-03-09T23:59:00+00:00"},  # antes → excluída
            {"timestamp": "2026-03-10T00:01:00+00:00"},  # depois → incluída
        ]
        result = services._filter_rows_since(rows, "timestamp")
        assert len(result) == 1
        assert result[0]["timestamp"] == "2026-03-10T00:01:00+00:00"

    def test_rows_without_valid_timestamp_are_dropped(self, monkeypatch):
        """Linhas sem timestamp válido são sempre descartadas quando floor está definido."""
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-01T00:00:00+00:00")
        rows = [{"timestamp": None}, {"timestamp": "nao-é-data"}]
        result = services._filter_rows_since(rows, "timestamp")
        assert result == []

    def test_tries_multiple_keys_in_order(self, monkeypatch):
        """Deve tentar a segunda chave quando a primeira é None."""
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-01T00:00:00+00:00")
        rows = [{"created_at": "2026-03-15T12:00:00+00:00"}]
        # "timestamp" inexistente, "created_at" válida
        result = services._filter_rows_since(rows, "timestamp", "created_at")
        assert len(result) == 1

    def test_row_exactly_at_floor_is_included(self, monkeypatch):
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", "2026-03-10T00:00:00+00:00")
        rows = [{"timestamp": "2026-03-10T00:00:00+00:00"}]
        result = services._filter_rows_since(rows, "timestamp")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# generate_metrics: TEST_MODE path
# ---------------------------------------------------------------------------

class TestGenerateMetricsTestMode:
    def test_test_mode_requires_supabase_enabled(self, monkeypatch):
        """TEST_MODE=True sem Supabase habilitado deve lançar RuntimeError."""
        monkeypatch.setattr(services.settings, "TEST_MODE", True)
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: False)

        with pytest.raises(RuntimeError, match="Staging test mode requires Supabase"):
            services.generate_metrics()

    def test_test_mode_with_supabase_enabled_uses_supabase_path(self, monkeypatch):
        """TEST_MODE=True com Supabase habilitado → usa _generate_metrics_from_supabase."""
        monkeypatch.setattr(services.settings, "TEST_MODE", True)
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        called = {}

        def fake_generate():
            called["supabase"] = True
            return {"generated_at": "2026-01-01", "enquetes": {}, "votos": {}}

        monkeypatch.setattr(services, "_generate_metrics_from_supabase", fake_generate)

        result = services.generate_metrics()
        assert called.get("supabase") is True
        assert result["generated_at"] == "2026-01-01"


# ---------------------------------------------------------------------------
# generate_metrics: METRICS_SOURCE=supabase path errors
# ---------------------------------------------------------------------------

class TestGenerateMetricsSupabaseDisabledError:
    def test_supabase_source_disabled_raises(self, monkeypatch):
        """METRICS_SOURCE=supabase mas SUPABASE_DOMAIN_ENABLED=false → RuntimeError."""
        monkeypatch.setattr(services.settings, "TEST_MODE", False)
        monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: False)

        with pytest.raises(RuntimeError, match="METRICS_SOURCE=supabase but SUPABASE_DOMAIN_ENABLED is false"):
            services.generate_metrics()


# ---------------------------------------------------------------------------
# _rankings_from_approved_packages
# ---------------------------------------------------------------------------

class TestRankingsFromApprovedPackages:
    def test_returns_empty_when_supabase_disabled(self, monkeypatch):
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: False)
        customers, polls = services._rankings_from_approved_packages()
        assert customers == {}
        assert polls == {}

    def test_returns_empty_when_no_packages(self, monkeypatch):
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.return_value = []

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages()

        assert customers == {}
        assert polls == {}

    def test_aggregates_customers_and_polls(self, monkeypatch):
        """Com pacotes e clientes válidos, deve agregar corretamente."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        # Primeiro select_all: pacotes
        fake_sb.select_all.side_effect = [
            [
                {
                    "id": "pkg-1",
                    "enquete_id": "enq-1",
                    "enquete": {
                        "id": "enq-uuid-1",
                        "external_poll_id": "ext-poll-1",
                        "titulo": "Produto A",
                        "drive_file_id": "drive-abc",
                        "produto": {"drive_file_id": None},
                    },
                }
            ],
            # Segundo: pacote_clientes
            [
                {
                    "pacote_id": "pkg-1",
                    "cliente_id": "cli-1",
                    "qty": 6,
                    "cliente": {"nome": "Cliente Teste", "celular": "5511999990001"},
                }
            ],
        ]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages(hours=24)

        assert "5511999990001" in customers
        assert customers["5511999990001"]["qty"] == 6
        assert "ext-poll-1" in polls
        assert polls["ext-poll-1"]["qty"] == 6
        assert polls["ext-poll-1"]["package_count"] == 1

    def test_since_today_computes_start_of_day(self, monkeypatch):
        """since_today=True deve calcular corte em 00:00 BRT sem erro."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.return_value = []  # sem pacotes

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages(since_today=True)

        assert customers == {}
        assert polls == {}

    def test_image_url_from_product_fallback(self, monkeypatch):
        """Quando a enquete não tem drive_file_id, usa o do produto."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.side_effect = [
            [
                {
                    "id": "pkg-2",
                    "enquete_id": "enq-2",
                    "enquete": {
                        "id": "enq-uuid-2",
                        "external_poll_id": "ext-poll-2",
                        "titulo": "Produto B",
                        "drive_file_id": None,  # sem drive na enquete
                        "produto": {"drive_file_id": "produto-drive-id"},
                    },
                }
            ],
            [
                {
                    "pacote_id": "pkg-2",
                    "cliente_id": "cli-2",
                    "qty": 3,
                    "cliente": {"nome": "Cliente B", "celular": "5511888880001"},
                }
            ],
        ]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            _, polls = services._rankings_from_approved_packages(hours=48)

        assert "ext-poll-2" in polls
        assert "produto-drive-id" in polls["ext-poll-2"]["image"]

    def test_zero_qty_row_is_skipped(self, monkeypatch):
        """Linhas com qty=0 não devem contribuir para os rankings."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.side_effect = [
            [
                {
                    "id": "pkg-3",
                    "enquete_id": "enq-3",
                    "enquete": {
                        "id": "enq-uuid-3",
                        "external_poll_id": "ext-poll-3",
                        "titulo": "Produto C",
                        "drive_file_id": None,
                        "produto": {},
                    },
                }
            ],
            [
                {
                    "pacote_id": "pkg-3",
                    "cliente_id": "cli-3",
                    "qty": 0,  # deve ser ignorado
                    "cliente": {"nome": "Cliente C", "celular": "5511777770001"},
                }
            ],
        ]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages(hours=24)

        assert customers == {}
        assert polls == {}

    def test_packages_without_poll_key_are_skipped(self, monkeypatch):
        """Enquete sem external_poll_id e sem id → poll_key vazio → não registra."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.side_effect = [
            [
                {
                    "id": "pkg-4",
                    "enquete_id": "enq-4",
                    "enquete": {
                        "id": "",             # id vazio
                        "external_poll_id": "",  # external_poll_id vazio
                        "titulo": "Produto D",
                        "drive_file_id": None,
                        "produto": {},
                    },
                }
            ],
            [
                {
                    "pacote_id": "pkg-4",
                    "cliente_id": "cli-4",
                    "qty": 5,
                    "cliente": {"nome": "Cliente D", "celular": "5511666660001"},
                }
            ],
        ]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages(hours=24)

        # cliente pode ser registrado mas poll sem chave não
        assert polls == {}

    def test_multiple_customers_accumulate_qty(self, monkeypatch):
        """Dois clientes no mesmo pacote são registrados independentemente."""
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)

        fake_sb = MagicMock()
        fake_sb.select_all.side_effect = [
            [
                {
                    "id": "pkg-5",
                    "enquete_id": "enq-5",
                    "enquete": {
                        "id": "uuid-5",
                        "external_poll_id": "ep-5",
                        "titulo": "Poll 5",
                        "drive_file_id": None,
                        "produto": {},
                    },
                }
            ],
            [
                {"pacote_id": "pkg-5", "qty": 6, "cliente": {"nome": "A", "celular": "5511111"}},
                {"pacote_id": "pkg-5", "qty": 6, "cliente": {"nome": "A", "celular": "5511111"}},
            ],
        ]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            customers, polls = services._rankings_from_approved_packages(hours=24)

        assert customers["5511111"]["qty"] == 12  # acumulou


# ---------------------------------------------------------------------------
# _enrich_enquetes_from_snapshots
# ---------------------------------------------------------------------------

class TestEnrichEnquetesFromSnapshots:
    def _make_sb_mock(self, id_rows, count_header, snapshot_rows):
        """Cria mock de SupabaseRestClient com comportamento configurável."""
        fake_sb = MagicMock()

        def fake_request(method, path, **kwargs):
            resp = MagicMock()
            if "pacotes" in path and "count=exact" in str(kwargs.get("extra_headers", {})):
                resp.text = "[]"
                resp.headers = {"content-range": f"0/{count_header}"}
                resp.json.return_value = []
            elif "pacotes" in path:
                resp.text = "[]"
                resp.headers = {}
                resp.json.return_value = []
            elif "metrics_hourly_snapshots" in path:
                resp.text = str(snapshot_rows)
                resp.json.return_value = snapshot_rows
                resp.headers = {}
            else:
                # enquetes query
                resp.text = str(id_rows)
                resp.json.return_value = id_rows
                resp.headers = {}
            return resp

        fake_sb._request.side_effect = fake_request
        return fake_sb

    def test_sets_closed_packages_on_active(self, monkeypatch):
        """Deve popular closed_packages_on_active com contagem do content-range."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()
        id_resp = MagicMock()
        id_resp.text = '[{"id": "uuid-1"}]'
        id_resp.json.return_value = [{"id": "uuid-1"}]

        count_resp = MagicMock()
        count_resp.text = "[]"
        count_resp.headers = {"content-range": "0/7"}
        count_resp.json.return_value = []

        snap_resp = MagicMock()
        snap_resp.text = "[]"
        snap_resp.json.return_value = []
        snap_resp.headers = {}

        fake_sb._request.side_effect = [id_resp, count_resp, snap_resp, snap_resp, snap_resp,
                                         snap_resp, snap_resp, snap_resp, snap_resp, snap_resp]

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 3}
            services._enrich_enquetes_from_snapshots(metrics, now, ["ext-poll-1"])

        assert metrics["closed_packages_on_active"] == 7

    def test_sets_zero_when_no_open_enquetes(self, monkeypatch):
        """Sem enquetes abertas → closed_packages_on_active = 0."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()
        empty_resp = MagicMock()
        empty_resp.text = "[]"
        empty_resp.json.return_value = []
        empty_resp.headers = {}

        fake_sb._request.return_value = empty_resp

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 5}
            services._enrich_enquetes_from_snapshots(metrics, now, ["poll-x"])

        assert metrics["closed_packages_on_active"] == 0

    def test_handles_exception_in_closed_count(self, monkeypatch):
        """Exceção na consulta de pacotes → closed_packages_on_active = None."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()
        fake_sb._request.side_effect = Exception("DB indisponível")

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 5}
            services._enrich_enquetes_from_snapshots(metrics, now, ["poll-x"])

        assert metrics["closed_packages_on_active"] is None

    def test_pct_vs_yesterday_with_snapshot(self, monkeypatch):
        """Com snapshot de ontem presente, pct_vs_yesterday deve ser calculado."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()

        # Sequência: 1) open enquetes, 2) count pacotes, depois 8 snapshots
        open_resp = MagicMock()
        open_resp.text = "[]"
        open_resp.json.return_value = []
        open_resp.headers = {}

        def snap_resp_for(value):
            r = MagicMock()
            r.text = f'[{{"enquetes_active_72h": {value}}}]'
            r.json.return_value = [{"enquetes_active_72h": value}]
            r.headers = {}
            return r

        # sem open_uuids → pula pacotes, vai direto pra snapshots
        # Snapshots: yesterday=10, depois 7 do loop
        responses = [open_resp] + [snap_resp_for(10)] + [snap_resp_for(8)] * 7

        fake_sb._request.side_effect = responses

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 12}
            services._enrich_enquetes_from_snapshots(metrics, now, None)

        # active_now=12, yesterday=10 → pct = (12-10)/10*100 = 20.0
        assert metrics["pct_vs_yesterday"] == 20.0

    def test_pct_vs_yesterday_none_when_no_snapshot(self, monkeypatch):
        """Sem snapshot disponível → pct_vs_yesterday = None."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()
        empty_resp = MagicMock()
        empty_resp.text = "[]"
        empty_resp.json.return_value = []
        empty_resp.headers = {}

        fake_sb._request.return_value = empty_resp

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 5}
            services._enrich_enquetes_from_snapshots(metrics, now, None)

        assert metrics["pct_vs_yesterday"] is None

    def test_pct_vs_7d_avg_computed_with_multiple_snapshots(self, monkeypatch):
        """Com 7 snapshots disponíveis, pct_vs_7d_avg deve ser calculado."""
        now = datetime(2026, 4, 10, 12, 0, 0)

        fake_sb = MagicMock()
        open_resp = MagicMock()
        open_resp.text = "[]"
        open_resp.json.return_value = []
        open_resp.headers = {}

        def snap_resp_for(value):
            r = MagicMock()
            r.text = f'[{{"enquetes_active_72h": {value}}}]'
            r.json.return_value = [{"enquetes_active_72h": value}]
            r.headers = {}
            return r

        # yesterday=5, loop de 7 dias todos com valor=5
        responses = [open_resp, snap_resp_for(5)] + [snap_resp_for(5)] * 7

        fake_sb._request.side_effect = responses

        with patch("app.services.supabase_service.SupabaseRestClient.from_settings", return_value=fake_sb):
            metrics = {"active_now": 10}
            services._enrich_enquetes_from_snapshots(metrics, now, None)

        # avg_7d=5, active_now=10 → pct = (10-5)/5*100 = 100.0
        assert metrics["pct_vs_7d_avg"] == 100.0


# ---------------------------------------------------------------------------
# generate_metrics: supabase path - rankings exception handling
# ---------------------------------------------------------------------------

class TestGenerateMetricsSupabaseRankingsExceptions:
    """Verifica que erros em rankings/temperatura não propagam (são logados)."""

    def _base_monkeypatch(self, monkeypatch, now):
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        fake_dates = {
            "now": now,
            "today_start": today_start,
            "yesterday_start": today_start - timedelta(days=1),
            "yesterday_end": today_start,
            "week_start": today_start - timedelta(days=7),
            "day24h_start": now - timedelta(hours=24),
        }
        monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
        monkeypatch.setattr(services.settings, "TEST_MODE", False)
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(services.supabase_clients, "fetch_enquetes_for_metrics", lambda: [])
        monkeypatch.setattr(services.supabase_clients, "fetch_votos_for_metrics", lambda: [])
        monkeypatch.setattr(
            services.supabase_clients,
            "fetch_package_lists_for_metrics",
            lambda: {},
        )
        monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
        monkeypatch.setattr(services.processors, "analyze_enquetes", _noop_analyze_enquetes)
        monkeypatch.setattr(services.processors, "analyze_votos", _noop_analyze_votos)
        monkeypatch.setattr(services, "_enrich_enquetes_from_snapshots", lambda *a, **kw: None)
        return fake_dates

    def test_rankings_exception_does_not_propagate(self, monkeypatch):
        """Se _rankings_from_approved_packages lançar, generate_metrics deve continuar."""
        now = datetime(2026, 5, 1, 14, 0, 0)
        self._base_monkeypatch(monkeypatch, now)

        monkeypatch.setattr(
            "app.services.sales_temperature_service.get_temperature",
            lambda force_refresh=False: {},
        )
        monkeypatch.setattr(
            "app.services.sales_temperature_service.compute_confirmed_extras",
            lambda: {},
        )
        monkeypatch.setattr(
            services,
            "_rankings_from_approved_packages",
            MagicMock(side_effect=Exception("DB error")),
        )

        result = services.generate_metrics()
        assert "generated_at" in result

    def test_sales_temperature_exception_does_not_propagate(self, monkeypatch):
        """Se get_temperature lançar, generate_metrics deve continuar sem temperature."""
        now = datetime(2026, 5, 1, 14, 0, 0)
        self._base_monkeypatch(monkeypatch, now)

        monkeypatch.setattr(
            "app.services.sales_temperature_service.get_temperature",
            lambda force_refresh=False: (_ for _ in ()).throw(Exception("temp error")),
        )
        monkeypatch.setattr(
            "app.services.sales_temperature_service.compute_confirmed_extras",
            lambda: {},
        )
        monkeypatch.setattr(
            services,
            "_rankings_from_approved_packages",
            lambda **kw: ({}, {}),
        )

        result = services.generate_metrics()
        assert "generated_at" in result

    def test_enrich_snapshots_exception_does_not_propagate(self, monkeypatch):
        """Se _enrich_enquetes_from_snapshots lançar, generate_metrics deve continuar."""
        now = datetime(2026, 5, 1, 14, 0, 0)
        self._base_monkeypatch(monkeypatch, now)

        monkeypatch.setattr(
            "app.services.sales_temperature_service.get_temperature",
            lambda force_refresh=False: {},
        )
        monkeypatch.setattr(
            "app.services.sales_temperature_service.compute_confirmed_extras",
            lambda: {},
        )
        monkeypatch.setattr(
            services,
            "_rankings_from_approved_packages",
            lambda **kw: ({}, {}),
        )
        # Substitui novamente com erro (a fixture base colocou no-op)
        monkeypatch.setattr(
            services,
            "_enrich_enquetes_from_snapshots",
            MagicMock(side_effect=Exception("snapshot error")),
        )

        result = services.generate_metrics()
        assert "generated_at" in result


# ---------------------------------------------------------------------------
# generate_metrics baserow: campos enquetes_created capturado
# ---------------------------------------------------------------------------

class TestGenerateMetricsBaserowEnquetesCreated:
    def test_enquetes_created_populated_from_createdAtTs(self, monkeypatch):
        """enquetes_created deve conter as datas parsadas de createdAtTs."""
        now = datetime(2026, 4, 10, 10, 0, 0)
        fake_dates = {
            "now": now,
            "today_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "yesterday_start": now.replace(day=9, hour=0),
            "yesterday_end": now.replace(hour=0),
            "week_start": now.replace(day=3, hour=0),
            "day24h_start": now - timedelta(hours=24),
        }
        monkeypatch.setattr(services.settings, "METRICS_SOURCE", "baserow")
        monkeypatch.setattr(services.settings, "TEST_MODE", False)
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)

        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=[
                [{"pollId": "p1", "title": "T1", "createdAtTs": "2026-04-10T08:00:00+00:00"}],
                [],
            ]),
        )
        monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
        monkeypatch.setattr(services.processors, "analyze_enquetes", lambda e, d: {})
        captured = {}

        def fake_av(votos, dates, enquetes_map, enquetes_created=None):
            captured["enquetes_created"] = enquetes_created
            return {}

        monkeypatch.setattr(services.processors, "analyze_votos", fake_av)

        services.generate_metrics()
        assert "p1" in captured["enquetes_created"]

    def test_enquetes_created_with_field_171_fallback(self, monkeypatch):
        """Deve capturar enquetes_created usando field_171 quando createdAtTs ausente."""
        now = datetime(2026, 4, 10, 10, 0, 0)
        fake_dates = {
            "now": now,
            "today_start": now.replace(hour=0),
            "yesterday_start": now.replace(day=9, hour=0),
            "yesterday_end": now.replace(hour=0),
            "week_start": now.replace(day=3, hour=0),
            "day24h_start": now - timedelta(hours=24),
        }
        monkeypatch.setattr(services.settings, "METRICS_SOURCE", "baserow")
        monkeypatch.setattr(services.settings, "TEST_MODE", False)
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)

        ts_val = str(int(datetime(2026, 4, 10, 7, 0, 0).timestamp()))
        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=[
                [{"pollId": "p2", "field_173": "T2", "field_171": ts_val}],
                [],
            ]),
        )
        monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
        monkeypatch.setattr(services.processors, "analyze_enquetes", lambda e, d: {})
        captured = {}

        def fake_av(votos, dates, enquetes_map, enquetes_created=None):
            captured["enquetes_created"] = enquetes_created
            return {}

        monkeypatch.setattr(services.processors, "analyze_votos", fake_av)

        services.generate_metrics()
        assert "p2" in captured["enquetes_created"]


# ---------------------------------------------------------------------------
# _generate_metrics_from_supabase: voto sem poll_id descartado
# ---------------------------------------------------------------------------

class TestGenerateMetricsSupabaseVotoSemPollId:
    def test_votos_without_pollid_are_skipped(self, monkeypatch):
        """Votos sem pollId não devem ser incluídos em normalized_votos."""
        now = datetime(2026, 4, 10, 10, 0, 0)
        fake_dates = {
            "now": now,
            "today_start": now.replace(hour=0),
            "yesterday_start": now.replace(day=9, hour=0),
            "yesterday_end": now.replace(hour=0),
            "week_start": now.replace(day=3, hour=0),
            "day24h_start": now - timedelta(hours=24),
        }
        monkeypatch.setattr(services.settings, "METRICS_SOURCE", "supabase")
        monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)
        monkeypatch.setattr(services.settings, "TEST_MODE", False)
        monkeypatch.setattr(services, "supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            services.supabase_clients,
            "fetch_enquetes_for_metrics",
            lambda: [],
        )
        monkeypatch.setattr(
            services.supabase_clients,
            "fetch_votos_for_metrics",
            lambda: [
                {"pollId": None, "voterPhone": "5511", "qty": 3, "timestamp": "2026-04-10T08:00:00+00:00"},
                {"pollId": "", "voterPhone": "5522", "qty": 5, "timestamp": "2026-04-10T08:00:00+00:00"},
            ],
        )
        monkeypatch.setattr(services.supabase_clients, "fetch_package_lists_for_metrics", lambda: {})
        monkeypatch.setattr(services.processors, "get_date_range", lambda: fake_dates)
        monkeypatch.setattr(services.processors, "analyze_enquetes", _noop_analyze_enquetes)
        captured = {}

        def fake_av(votos, dates, enquetes_map, enquetes_created=None):
            captured["votos"] = votos
            return {}

        monkeypatch.setattr(services.processors, "analyze_votos", fake_av)
        monkeypatch.setattr(services, "_enrich_enquetes_from_snapshots", lambda *a, **kw: None)
        monkeypatch.setattr(
            "app.services.sales_temperature_service.get_temperature",
            lambda force_refresh=False: {},
        )
        monkeypatch.setattr(
            "app.services.sales_temperature_service.compute_confirmed_extras",
            lambda: {},
        )
        monkeypatch.setattr(services, "_rankings_from_approved_packages", lambda **kw: ({}, {}))

        services.generate_metrics()
        assert captured["votos"] == []
