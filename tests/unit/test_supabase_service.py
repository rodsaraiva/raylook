import httpx

from app.services import supabase_service
from app.services.supabase_service import SupabaseRestClient


def test_request_uses_configurable_rest_path(monkeypatch):
    captured = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured["method"] = method
        captured["url"] = url
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)

    client = SupabaseRestClient(
        url="http://postgrest.internal:3000",
        service_role_key="token-123",
        schema="public",
        rest_path="",
    )

    rows = client.select("produtos")

    assert rows == []
    assert captured["method"] == "GET"
    assert captured["url"] == "http://postgrest.internal:3000/produtos"


def test_request_keeps_default_supabase_rest_prefix(monkeypatch):
    captured = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured["url"] = url
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)

    client = SupabaseRestClient(
        url="https://example.supabase.co",
        service_role_key="token-123",
        schema="public",
    )

    rows = client.select("produtos")

    assert rows == []
    assert captured["url"] == "https://example.supabase.co/rest/v1/produtos"


def test_fetch_project_status_uses_postgrest_probe_when_not_on_supabase(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://alana-postgrest-staging:3000")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")

    class FakeClient:
        def select(self, table, **kwargs):
            assert table == "app_runtime_state"
            return [{"key": "dashboard_metrics"}]

    monkeypatch.setattr(supabase_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    payload = supabase_service.fetch_project_status()

    assert payload["backend"] == "postgrest"
    assert payload["status"] == "ok"
    assert payload["sample_rows"] == 1
