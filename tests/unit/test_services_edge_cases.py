"""Edge-case tests for metrics.services.generate_metrics."""
import os
from unittest.mock import patch, MagicMock

import pytest

from metrics.services import generate_metrics
from metrics import services


@pytest.fixture(autouse=True)
def _force_baserow_mode(monkeypatch):
    """Force baserow mode for all tests in this file."""
    monkeypatch.setattr(services.settings, "TEST_MODE", False)
    monkeypatch.setattr(services.settings, "METRICS_SOURCE", "baserow")
    monkeypatch.setattr(services.settings, "METRICS_MIN_DATE", None)


class TestGenerateMetricsEnquetesMapBuilding:
    """Verify enquetes_map is built correctly from various field combinations."""

    def test_enquetes_map_uses_field_169_fallback(self, monkeypatch):
        """When 'pollId' is absent, field_169 should be used."""
        mock_dates = MagicMock(return_value={"now": MagicMock(isoformat=lambda: "2026")})
        mock_av = MagicMock(return_value={})
        mock_ae = MagicMock(return_value={})
        monkeypatch.setattr(services.processors, "get_date_range", mock_dates)
        monkeypatch.setattr(services.processors, "analyze_votos", mock_av)
        monkeypatch.setattr(services.processors, "analyze_enquetes", mock_ae)
        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=[
                [{"field_169": "poll-fallback", "field_173": "Titulo Fallback"}],
                [],
            ]),
        )

        generate_metrics()

        call_args = mock_av.call_args
        enquetes_map = call_args[0][2]
        assert "poll-fallback" in enquetes_map
        assert enquetes_map["poll-fallback"]["title"] == "Titulo Fallback"

    def test_enquetes_without_poll_id_are_skipped(self, monkeypatch):
        """Rows with neither pollId nor field_169 should not be added to map."""
        mock_av = MagicMock(return_value={})
        monkeypatch.setattr(services.processors, "get_date_range", lambda: {"now": MagicMock(isoformat=lambda: "2026")})
        monkeypatch.setattr(services.processors, "analyze_votos", mock_av)
        monkeypatch.setattr(services.processors, "analyze_enquetes", MagicMock(return_value={}))
        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=[
                [{"title": "Orphan Title"}],
                [],
            ]),
        )

        generate_metrics()

        call_args = mock_av.call_args
        enquetes_map = call_args[0][2]
        assert len(enquetes_map) == 0

    def test_enquetes_map_prefers_pollId_over_field_169(self, monkeypatch):
        mock_av = MagicMock(return_value={})
        monkeypatch.setattr(services.processors, "get_date_range", lambda: {"now": MagicMock(isoformat=lambda: "2026")})
        monkeypatch.setattr(services.processors, "analyze_votos", mock_av)
        monkeypatch.setattr(services.processors, "analyze_enquetes", MagicMock(return_value={}))
        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=[
                [{"pollId": "correct-id", "field_169": "wrong-id", "title": "Titulo"}],
                [],
            ]),
        )

        generate_metrics()

        call_args = mock_av.call_args
        enquetes_map = call_args[0][2]
        assert "correct-id" in enquetes_map
        assert "wrong-id" not in enquetes_map

    def test_client_error_propagates(self, monkeypatch):
        """If the Baserow client raises, generate_metrics should not swallow it."""
        monkeypatch.setattr(
            services.clients,
            "fetch_all_rows",
            MagicMock(side_effect=RuntimeError("Connection error")),
        )

        try:
            generate_metrics()
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Connection error" in str(e)

    def test_env_vars_for_table_ids(self, monkeypatch):
        mock_fetch = MagicMock(return_value=[])
        monkeypatch.setattr(services.processors, "get_date_range", lambda: {"now": MagicMock(isoformat=lambda: "2026")})
        monkeypatch.setattr(services.processors, "analyze_votos", MagicMock(return_value={}))
        monkeypatch.setattr(services.processors, "analyze_enquetes", MagicMock(return_value={}))
        monkeypatch.setattr(services.clients, "fetch_all_rows", mock_fetch)

        monkeypatch.setenv("BASEROW_TABLE_ENQUETES", "99")
        monkeypatch.setenv("BASEROW_TABLE_VOTOS", "88")

        generate_metrics()

        assert mock_fetch.call_args_list[0][0][0] == "99"
        assert mock_fetch.call_args_list[1][0][0] == "88"

    def test_return_payload_structure(self, monkeypatch):
        monkeypatch.setattr(services.processors, "get_date_range", lambda: {"now": MagicMock(isoformat=lambda: "2026")})
        monkeypatch.setattr(services.processors, "analyze_votos", MagicMock(return_value={"votos_data": 1}))
        monkeypatch.setattr(services.processors, "analyze_enquetes", MagicMock(return_value={"enquetes_data": 2}))
        monkeypatch.setattr(services.clients, "fetch_all_rows", MagicMock(return_value=[]))

        result = generate_metrics()

        assert "generated_at" in result
        assert "enquetes" in result
        assert "votos" in result
        assert result["enquetes"] == {"enquetes_data": 2}
        assert result["votos"] == {"votos_data": 1}
