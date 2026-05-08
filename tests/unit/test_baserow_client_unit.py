from unittest.mock import MagicMock
import httpx
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


def test_fetch_all_rows_pagination(monkeypatch):
    monkeypatch.setattr(clients, "TOKEN", "token-abc")
    _mock_client([
        {"results": [{"id": 1, "a": 1}], "next": "http://next"},
        {"results": [{"id": 2, "a": 2}], "next": None},
    ], monkeypatch)

    rows = clients.fetch_all_rows("17")
    assert isinstance(rows, list)
    assert {r["id"] for r in rows} == {1, 2}
