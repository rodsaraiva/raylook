from unittest.mock import MagicMock
import httpx
import pytest

import metrics.clients as clients


def _mock_client(pages, monkeypatch):
    responses = []
    for page in pages:
        r = MagicMock(spec=httpx.Response)
        r.status_code = page.get("status_code", 200)
        r.json.return_value = {"results": page.get("results", []), "next": page.get("next")}
        r.text = str(page)
        responses.append(r)

    mock_instance = MagicMock()
    mock_instance.get.side_effect = responses
    mock_instance.__enter__ = lambda s: s
    mock_instance.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(httpx, "Client", lambda **kw: mock_instance)
    return mock_instance


def test_fetch_all_rows_deduplicates_by_id(monkeypatch):
    monkeypatch.setattr(clients, "TOKEN", "token-abc")
    mock = _mock_client([
        {"results": [{"id": 1, "v": "old"}, {"id": 2, "v": "x"}], "next": "http://next"},
        {"results": [{"id": 1, "v": "new"}], "next": None},
    ], monkeypatch)

    rows = clients.fetch_all_rows("17")

    by_id = {r["id"]: r for r in rows}
    assert len(rows) == 2
    assert by_id[1]["v"] == "new"


def test_fetch_all_rows_raises_runtime_error_on_non_200(monkeypatch):
    monkeypatch.setattr(clients, "TOKEN", "token-abc")
    _mock_client([{"status_code": 500, "results": []}], monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        clients.fetch_all_rows("17")

    assert "500" in str(exc.value)


def test_fetch_all_rows_raises_when_no_token(monkeypatch):
    monkeypatch.setattr(clients, "TOKEN", None)

    with pytest.raises(RuntimeError, match="BASEROW_API_TOKEN"):
        clients.fetch_all_rows("17")
