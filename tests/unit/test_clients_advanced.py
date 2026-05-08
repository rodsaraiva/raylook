"""Advanced tests for metrics.clients.fetch_all_rows."""
from unittest.mock import MagicMock, patch

import pytest
import httpx

from metrics import clients
from metrics.clients import fetch_all_rows


def _mock_httpx_client(pages):
    """Build a mock httpx.Client that returns pages sequentially."""
    responses = []
    for page in pages:
        r = MagicMock(spec=httpx.Response)
        r.status_code = page.get("status_code", 200)
        r.json.return_value = page.get("body", {})
        r.text = str(page.get("body", {}))
        responses.append(r)
    return responses


class TestFetchAllRowsPagination:
    """Detailed pagination and deduplication tests."""

    def test_three_pages(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "test-token")
        pages = [
            {"body": {"results": [{"id": 1, "name": "A"}], "next": "http://api/page2"}},
            {"body": {"results": [{"id": 2, "name": "B"}], "next": "http://api/page3"}},
            {"body": {"results": [{"id": 3, "name": "C"}], "next": None}},
        ]
        responses = _mock_httpx_client(pages)

        mock_client_instance = MagicMock()
        mock_client_instance.get.side_effect = responses
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        rows = fetch_all_rows("42")
        assert len(rows) == 3
        names = {r["name"] for r in rows}
        assert names == {"A", "B", "C"}

    def test_duplicate_ids_are_deduplicated(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "test-token")
        pages = [
            {"body": {"results": [{"id": 1, "name": "First"}, {"id": 2, "name": "X"}], "next": "http://p2"}},
            {"body": {"results": [{"id": 1, "name": "Overwritten"}, {"id": 3, "name": "Y"}], "next": None}},
        ]
        responses = _mock_httpx_client(pages)

        mock_client_instance = MagicMock()
        mock_client_instance.get.side_effect = responses
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        rows = fetch_all_rows("42")
        assert len(rows) == 3
        id1 = next(r for r in rows if r["id"] == 1)
        assert id1["name"] == "Overwritten"


class TestFetchAllRowsErrors:
    def test_404_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "test-token")
        r = MagicMock(spec=httpx.Response)
        r.status_code = 404
        r.text = "Not found"

        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = r
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        with pytest.raises(RuntimeError, match="404"):
            fetch_all_rows("999")

    def test_500_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "test-token")
        r = MagicMock(spec=httpx.Response)
        r.status_code = 500
        r.text = "Server error"

        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = r
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        with pytest.raises(RuntimeError, match="500"):
            fetch_all_rows("999")

    def test_network_error_propagates(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "test-token")
        mock_client_instance = MagicMock()
        mock_client_instance.get.side_effect = httpx.RequestError("DNS resolution failed", request=MagicMock())
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        with pytest.raises(RuntimeError, match="Network error"):
            fetch_all_rows("42")


class TestFetchAllRowsHeaders:
    def test_token_present_sends_auth_header(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "my-secret-token")

        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json.return_value = {"results": [], "next": None}

        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = r
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        fetch_all_rows("10")

        _, kwargs = mock_client_instance.get.call_args
        assert kwargs["headers"]["Authorization"] == "Token my-secret-token"

    def test_no_token_raises(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", None)

        with pytest.raises(RuntimeError, match="BASEROW_API_TOKEN"):
            fetch_all_rows("10")

    def test_empty_string_token_raises(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "")

        with pytest.raises(RuntimeError, match="BASEROW_API_TOKEN"):
            fetch_all_rows("10")


class TestFetchAllRowsUrl:
    def test_url_includes_table_id(self, monkeypatch):
        monkeypatch.setattr(clients, "API_URL", "https://custom.api.io")
        monkeypatch.setattr(clients, "TOKEN", "t")

        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json.return_value = {"results": [], "next": None}

        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = r
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        fetch_all_rows("777")

        url_called = mock_client_instance.get.call_args_list[0][0][0]
        assert "777" in url_called
        assert "custom.api.io" in url_called

    def test_empty_results_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(clients, "TOKEN", "t")

        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json.return_value = {"results": [], "next": None}

        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = r
        mock_client_instance.__enter__ = lambda s: s
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client_instance)

        rows = fetch_all_rows("10")
        assert rows == []
