"""Testes unitários para SupabaseRestClient e funções auxiliares.

Usa monkeypatch em httpx.Client.request para nunca bater em API real.
"""
from __future__ import annotations

import httpx
import pytest
from datetime import datetime, timezone

from app.services import supabase_service
from app.services.supabase_service import (
    SupabaseRestClient,
    _required,
    _to_iso,
    supabase_domain_enabled,
    fetch_project_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> SupabaseRestClient:
    defaults = dict(url="http://postgrest:3000", service_role_key="tok")
    defaults.update(kwargs)
    return SupabaseRestClient(**defaults)


def _patch_request(monkeypatch, *, status=200, body=None, json_body=None):
    """Retorna captura de chamada e patcheia httpx.Client.request."""
    captured: dict = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured.update(method=method, url=url, headers=headers, params=params, json=json)
        if json_body is not None:
            return httpx.Response(status, json=json_body)
        if body is not None:
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    return captured


# ---------------------------------------------------------------------------
# _required
# ---------------------------------------------------------------------------

def test_required_retorna_valor_limpo():
    assert _required("  valor  ", "ENV") == "valor"


def test_required_levanta_quando_none():
    with pytest.raises(RuntimeError, match="ENV_X is not configured"):
        _required(None, "ENV_X")


def test_required_levanta_quando_string_vazia():
    with pytest.raises(RuntimeError):
        _required("   ", "ENV_Y")


# ---------------------------------------------------------------------------
# _to_iso
# ---------------------------------------------------------------------------

def test_to_iso_none_retorna_none():
    assert _to_iso(None) is None


def test_to_iso_sem_tzinfo_assume_utc():
    dt = datetime(2024, 6, 15, 12, 0, 0)
    result = _to_iso(dt)
    assert result is not None
    assert "12:00:00" in result
    assert "+00:00" in result or "Z" in result


def test_to_iso_com_tzinfo_mantem_utc():
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = _to_iso(dt)
    assert result is not None
    assert "2024-06-15" in result


# ---------------------------------------------------------------------------
# supabase_domain_enabled
# ---------------------------------------------------------------------------

def test_domain_enabled_com_sqlite_backend(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "DATA_BACKEND", "sqlite", raising=False)
    assert supabase_domain_enabled() is True


def test_domain_enabled_com_supabase_configurado(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "DATA_BACKEND", "supabase", raising=False)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_DOMAIN_ENABLED", True)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SERVICE_ROLE_KEY", "key")
    assert supabase_domain_enabled() is True


def test_domain_enabled_sem_configuracao(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "DATA_BACKEND", "supabase", raising=False)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_DOMAIN_ENABLED", False)
    assert supabase_domain_enabled() is False


# ---------------------------------------------------------------------------
# SupabaseRestClient.__init__ e from_settings
# ---------------------------------------------------------------------------

def test_init_normaliza_url():
    c = _make_client(url="http://postgrest:3000/")
    assert c.url == "http://postgrest:3000"


def test_from_settings_supabase_backend(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "DATA_BACKEND", "supabase", raising=False)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_REST_PATH", "/rest/v1", raising=False)

    client = SupabaseRestClient.from_settings()
    assert isinstance(client, SupabaseRestClient)
    assert client.service_role_key == "service-key"


def test_from_settings_sqlite_backend(monkeypatch):
    """Quando DATA_BACKEND=sqlite, from_settings delega para SQLiteRestClient."""
    monkeypatch.setattr(supabase_service.settings, "DATA_BACKEND", "sqlite", raising=False)

    class FakeSQLite:
        @classmethod
        def from_settings(cls):
            return cls()

    import app.services.sqlite_service as sqlite_mod
    monkeypatch.setattr(sqlite_mod, "SQLiteRestClient", FakeSQLite)

    result = SupabaseRestClient.from_settings()
    assert isinstance(result, FakeSQLite)


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------

def test_headers_padrao():
    c = _make_client(schema="custom")
    h = c._headers()
    assert h["apikey"] == "tok"
    assert "Bearer tok" in h["Authorization"]
    assert h["Accept-Profile"] == "custom"
    assert "Accept" not in h
    assert "Prefer" not in h


def test_headers_com_accept_object():
    c = _make_client()
    h = c._headers(accept_object=True)
    assert "application/vnd.pgrst.object+json" in h["Accept"]


def test_headers_com_prefer():
    c = _make_client()
    h = c._headers(prefer="return=representation")
    assert h["Prefer"] == "return=representation"


# ---------------------------------------------------------------------------
# _filter_value
# ---------------------------------------------------------------------------

def test_filter_value_eq():
    assert SupabaseRestClient._filter_value("eq", "abc") == "eq.abc"


def test_filter_value_in_lista():
    result = SupabaseRestClient._filter_value("in", [1, 2, 3])
    assert result == "in.(1,2,3)"


def test_filter_value_in_tuple():
    result = SupabaseRestClient._filter_value("in", (4, 5))
    assert result == "in.(4,5)"


def test_filter_value_in_set():
    result = SupabaseRestClient._filter_value("in", {7})
    assert result == "in.(7)"


def test_filter_value_in_escalar():
    assert SupabaseRestClient._filter_value("in", "x,y") == "in.(x,y)"


def test_filter_value_is_null():
    assert SupabaseRestClient._filter_value("is", None) == "is.none"


def test_filter_value_is_true():
    assert SupabaseRestClient._filter_value("is", True) == "is.true"


def test_filter_value_gte():
    assert SupabaseRestClient._filter_value("gte", "2024-01-01") == "gte.2024-01-01"


def test_filter_value_lte():
    assert SupabaseRestClient._filter_value("lte", "2024-12-31") == "lte.2024-12-31"


# ---------------------------------------------------------------------------
# _request — URL construction
# ---------------------------------------------------------------------------

def test_request_usa_rest_path_configuravel(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client(rest_path="")
    c.select("produtos")
    assert captured["url"] == "http://postgrest:3000/produtos"


def test_request_mantém_prefixo_rest_v1(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client(rest_path="/rest/v1")
    c.select("produtos")
    assert captured["url"] == "http://postgrest:3000/rest/v1/produtos"


def test_request_normaliza_path_sem_rest_v1(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client(rest_path="/api/v2")
    # chama _request diretamente com path sem /rest/v1
    c._request("GET", "outra_tabela")
    assert captured["url"] == "http://postgrest:3000/api/v2/outra_tabela"


def test_request_extra_headers(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client()
    c._request("GET", "/rest/v1/tabela", extra_headers={"Range": "0-99"})
    assert captured["headers"]["Range"] == "0-99"


# ---------------------------------------------------------------------------
# _request — tratamento de erros HTTP
# ---------------------------------------------------------------------------

def test_request_400_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(400, text="bad request"),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="Supabase REST error 400"):
        c._request("GET", "/rest/v1/tabela")


def test_request_401_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(401, text="unauthorized"),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="401"):
        c._request("GET", "/rest/v1/tabela")


def test_request_404_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(404, text="not found"),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="404"):
        c._request("GET", "/rest/v1/tabela")


def test_request_500_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(500, text="internal error"),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="500"):
        c._request("GET", "/rest/v1/tabela")


def test_request_406_pgrst116_zero_rows_retorna_204(monkeypatch):
    """406 com código PGRST116 e '0 rows' deve converter em 204 (not found limpo)."""
    error_body = {"code": "PGRST116", "details": "Results contain 0 rows", "message": ""}

    def fake_406(self, method, url, **kw):
        req = httpx.Request(method, url)
        return httpx.Response(406, json=error_body, request=req)

    monkeypatch.setattr(httpx.Client, "request", fake_406, raising=False)
    c = _make_client()
    resp = c._request("GET", "/rest/v1/tabela", accept_object=True)
    assert resp.status_code == 204


def test_request_406_outro_erro_levanta(monkeypatch):
    """406 sem PGRST116 deve propagar como RuntimeError."""
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(406, json={"code": "OTHER", "message": "not acceptable"}),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="406"):
        c._request("GET", "/rest/v1/tabela", accept_object=True)


def test_request_406_json_invalido_levanta(monkeypatch):
    """406 com body não-JSON sem PGRST116 deve levantar RuntimeError."""
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(406, content=b"not json"),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="406"):
        c._request("GET", "/rest/v1/tabela", accept_object=True)


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------

def test_select_retorna_lista(monkeypatch):
    _patch_request(monkeypatch, json_body=[{"id": 1}])
    c = _make_client()
    rows = c.select("tabela")
    assert rows == [{"id": 1}]


def test_select_200_vazio_retorna_none(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(200, content=b""),
        raising=False,
    )
    c = _make_client()
    result = c.select("tabela")
    assert result is None


def test_select_single_retorna_objeto(monkeypatch):
    _patch_request(monkeypatch, json_body={"id": 42, "nome": "X"})
    c = _make_client()
    result = c.select("tabela", single=True)
    assert result == {"id": 42, "nome": "X"}


def test_select_204_retorna_none(monkeypatch):
    """single=True com resposta 406/PGRST116 (0 rows) converte para None."""
    error_body = {"code": "PGRST116", "details": "0 rows", "message": ""}

    def fake_406(self, method, url, **kw):
        req = httpx.Request(method, url)
        return httpx.Response(406, json=error_body, request=req)

    monkeypatch.setattr(httpx.Client, "request", fake_406, raising=False)
    c = _make_client()
    result = c.select("tabela", single=True)
    assert result is None


def test_select_com_filtros(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client()
    c.select("tabela", filters=[("status", "eq", "ativo"), ("id", "in", [1, 2])])
    assert captured["params"]["status"] == "eq.ativo"
    assert captured["params"]["id"] == "in.(1,2)"


def test_select_com_limit_offset_order(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client()
    c.select("tabela", limit=10, offset=20, order="created_at.desc")
    assert captured["params"]["limit"] == "10"
    assert captured["params"]["offset"] == "20"
    assert captured["params"]["order"] == "created_at.desc"


def test_select_colunas_especificas(monkeypatch):
    captured = _patch_request(monkeypatch)
    c = _make_client()
    c.select("tabela", columns="id,nome,status")
    assert captured["params"]["select"] == "id,nome,status"


def test_select_nested_columns(monkeypatch):
    """select com nested join notation (ex: clientes(id,nome))."""
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.select("pedidos", columns="id,clientes(id,nome)")
    assert "clientes(id,nome)" in captured["params"]["select"]


# ---------------------------------------------------------------------------
# select_all — paginação
# ---------------------------------------------------------------------------

def test_select_all_pagina_unica(monkeypatch):
    _patch_request(monkeypatch, json_body=[{"id": i} for i in range(5)])
    c = _make_client()
    rows = c.select_all("tabela", page_size=1000)
    assert len(rows) == 5


def test_select_all_multiplas_paginas(monkeypatch):
    """Simula 2 páginas: primeira com page_size itens, segunda com menos."""
    call_count = [0]

    def fake_request(self, method, url, headers=None, params=None, json=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(200, json=[{"id": i} for i in range(3)])
        return httpx.Response(200, json=[{"id": i} for i in range(1)])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    c = _make_client()
    rows = c.select_all("tabela", page_size=3)
    assert len(rows) == 4
    assert call_count[0] == 2


def test_select_all_body_vazio_retorna_lista_vazia(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(200, content=b""),
        raising=False,
    )
    c = _make_client()
    rows = c.select_all("tabela")
    assert rows == []


def test_select_all_resposta_nao_lista(monkeypatch):
    """Body que não é lista é ignorado (batch = [])."""
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(200, json={"erro": "sim"}),
        raising=False,
    )
    c = _make_client()
    rows = c.select_all("tabela")
    assert rows == []


def test_select_all_envia_range_header(monkeypatch):
    captured: dict = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured["headers"] = headers
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    c = _make_client()
    c.select_all("tabela", page_size=50)
    assert captured["headers"]["Range"] == "0-49"


def test_select_all_filtros_multiplos_mesmo_campo(monkeypatch):
    """Filtros gte+lte no mesmo campo devem ir como lista de tuples."""
    captured: dict = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured["params"] = params
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    c = _make_client()
    c.select_all(
        "tabela",
        filters=[
            ("data", "gte", "2024-01-01"),
            ("data", "lte", "2024-12-31"),
        ],
    )
    params = captured["params"]
    data_values = [v for k, v in params if k == "data"]
    assert "gte.2024-01-01" in data_values
    assert "lte.2024-12-31" in data_values


def test_select_all_com_order(monkeypatch):
    captured: dict = {}

    def fake_request(self, method, url, headers=None, params=None, json=None):
        captured["params"] = params
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    c = _make_client()
    c.select_all("tabela", order="created_at.asc")
    keys = [k for k, v in captured["params"]]
    assert "order" in keys


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------

def test_insert_retorna_lista(monkeypatch):
    _patch_request(monkeypatch, json_body=[{"id": 99}])
    c = _make_client()
    result = c.insert("tabela", {"nome": "X"})
    assert result == [{"id": 99}]


def test_insert_objeto_singular_wraps_em_lista(monkeypatch):
    _patch_request(monkeypatch, json_body={"id": 99})
    c = _make_client()
    result = c.insert("tabela", {"nome": "X"})
    assert result == [{"id": 99}]


def test_insert_body_vazio_retorna_lista_vazia(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(201, content=b""),
        raising=False,
    )
    c = _make_client()
    result = c.insert("tabela", {"nome": "X"})
    assert result == []


def test_insert_upsert_envia_prefer_correto(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.insert("tabela", {"id": 1}, upsert=True, on_conflict="id")
    assert "resolution=merge-duplicates" in captured["headers"]["Prefer"]
    assert captured["params"]["on_conflict"] == "id"


def test_insert_returning_minimal(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.insert("tabela", {"id": 1}, returning="minimal")
    assert captured["headers"]["Prefer"] == "return=minimal"


def test_insert_lista_de_objetos(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[{"id": 1}, {"id": 2}])
    c = _make_client()
    result = c.insert("tabela", [{"nome": "A"}, {"nome": "B"}])
    assert len(result) == 2
    assert captured["json"] == [{"nome": "A"}, {"nome": "B"}]


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_retorna_lista(monkeypatch):
    _patch_request(monkeypatch, json_body=[{"id": 1, "status": "novo"}])
    c = _make_client()
    result = c.update("tabela", {"status": "novo"}, filters=[("id", "eq", 1)])
    assert result == [{"id": 1, "status": "novo"}]


def test_update_objeto_singular_wraps_em_lista(monkeypatch):
    _patch_request(monkeypatch, json_body={"id": 1})
    c = _make_client()
    result = c.update("tabela", {"status": "ok"})
    assert result == [{"id": 1}]


def test_update_body_vazio_retorna_lista_vazia(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(204, content=b""),
        raising=False,
    )
    c = _make_client()
    result = c.update("tabela", {"x": 1})
    assert result == []


def test_update_envia_prefer_representation(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.update("tabela", {"x": 1}, returning="representation")
    assert "return=representation" in captured["headers"]["Prefer"]


def test_update_envia_prefer_minimal(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.update("tabela", {"x": 1}, returning="minimal")
    assert "return=minimal" in captured["headers"]["Prefer"]


def test_update_com_filtro_eq(monkeypatch):
    captured = _patch_request(monkeypatch, json_body=[])
    c = _make_client()
    c.update("tabela", {"status": "ok"}, filters=[("id", "eq", "abc-123")])
    assert captured["params"]["id"] == "eq.abc-123"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_retorna_status_code(monkeypatch):
    _patch_request(monkeypatch, status=204, json_body=[])
    c = _make_client()
    code = c.delete("tabela", filters=[("id", "eq", 1)])
    assert code == 204


def test_delete_envia_prefer_minimal(monkeypatch):
    captured = _patch_request(monkeypatch, status=204, json_body=[])
    c = _make_client()
    c.delete("tabela")
    assert "return=minimal" in captured["headers"]["Prefer"]


def test_delete_com_filtro(monkeypatch):
    captured = _patch_request(monkeypatch, status=204, json_body=[])
    c = _make_client()
    c.delete("tabela", filters=[("status", "eq", "inativo")])
    assert captured["params"]["status"] == "eq.inativo"


def test_delete_sem_filtro_chama_delete(monkeypatch):
    captured = _patch_request(monkeypatch, status=204, json_body=[])
    c = _make_client()
    c.delete("tabela")
    assert captured["method"] == "DELETE"


# ---------------------------------------------------------------------------
# rpc
# ---------------------------------------------------------------------------

def test_rpc_retorna_resultado(monkeypatch):
    _patch_request(monkeypatch, json_body={"soma": 42})
    c = _make_client()
    result = c.rpc("calcular_soma", {"a": 20, "b": 22})
    assert result == {"soma": 42}


def test_rpc_sem_args_envia_objeto_vazio(monkeypatch):
    captured = _patch_request(monkeypatch, json_body={})
    c = _make_client()
    c.rpc("fn_sem_args")
    assert captured["json"] == {}


def test_rpc_body_vazio_retorna_none(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(200, content=b""),
        raising=False,
    )
    c = _make_client()
    result = c.rpc("fn")
    assert result is None


def test_rpc_envia_post_para_rpc_path(monkeypatch):
    captured = _patch_request(monkeypatch, json_body={})
    c = _make_client(rest_path="/rest/v1")
    c.rpc("minha_funcao", {"x": 1})
    assert "/rpc/minha_funcao" in captured["url"]
    assert captured["method"] == "POST"


# ---------------------------------------------------------------------------
# upsert_one
# ---------------------------------------------------------------------------

def test_upsert_one_retorna_primeiro_elemento(monkeypatch):
    _patch_request(monkeypatch, json_body=[{"id": 10, "nome": "A"}])
    c = _make_client()
    result = c.upsert_one("tabela", {"id": 10, "nome": "A"}, on_conflict="id")
    assert result == {"id": 10, "nome": "A"}


def test_upsert_one_resposta_vazia_levanta(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "request",
        lambda self, m, u, **kw: httpx.Response(201, content=b""),
        raising=False,
    )
    c = _make_client()
    with pytest.raises(RuntimeError, match="Empty upsert response"):
        c.upsert_one("tabela", {"id": 1}, on_conflict="id")


# ---------------------------------------------------------------------------
# now_iso
# ---------------------------------------------------------------------------

def test_now_iso_retorna_string_utc():
    result = SupabaseRestClient.now_iso()
    assert isinstance(result, str)
    assert "T" in result
    # deve ter offset UTC
    assert "+00:00" in result or "Z" in result


# ---------------------------------------------------------------------------
# fetch_project_status
# ---------------------------------------------------------------------------

def test_fetch_project_status_usa_probe_postgrest(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://raylook-postgrest:3000")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")

    class FakeClient:
        def select(self, table, **kwargs):
            return [{"key": "k"}]

    monkeypatch.setattr(supabase_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    payload = fetch_project_status()
    assert payload["backend"] == "postgrest"
    assert payload["status"] == "ok"
    assert payload["sample_rows"] == 1


def test_fetch_project_status_sem_rows_sample_zero(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://local:3000")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")

    class FakeClient:
        def select(self, table, **kwargs):
            return []

    monkeypatch.setattr(supabase_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    payload = fetch_project_status()
    assert payload["sample_rows"] == 0


def test_fetch_project_status_probe_retorna_objeto_unico(monkeypatch):
    """Quando select retorna dict (não lista), wraps em [dict]."""
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://local:3000")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")

    class FakeClient:
        def select(self, table, **kwargs):
            return {"key": "only_one"}

    monkeypatch.setattr(supabase_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    payload = fetch_project_status()
    assert payload["sample_rows"] == 1


def test_fetch_project_status_probe_retorna_none(monkeypatch):
    """Quando select retorna None, sample_rows=0."""
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://local:3000")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", None)
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_SCHEMA", "public")

    class FakeClient:
        def select(self, table, **kwargs):
            return None

    monkeypatch.setattr(supabase_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    payload = fetch_project_status()
    assert payload["sample_rows"] == 0


def test_fetch_project_status_supabase_cloud_200(monkeypatch):
    """Quando token+ref+supabase.co configurados, chama API Management e retorna dict."""
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", "tok-abc")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", "xyzref")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://xyzref.supabase.co")

    api_data = {
        "id": "xyzref",
        "ref": "xyzref",
        "name": "My Project",
        "region": "sa-east-1",
        "status": "ACTIVE_HEALTHY",
        "database": {"host": "db.xyzref.supabase.co"},
    }
    monkeypatch.setattr(
        httpx.Client, "get",
        lambda self, url, headers=None: httpx.Response(200, json=api_data),
        raising=False,
    )

    result = fetch_project_status()
    assert result["backend"] == "supabase"
    assert result["status"] == "ACTIVE_HEALTHY"
    assert result["db_host"] == "db.xyzref.supabase.co"


def test_fetch_project_status_supabase_cloud_401(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", "tok-bad")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", "ref1")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://ref1.supabase.co")

    monkeypatch.setattr(
        httpx.Client, "get",
        lambda self, url, headers=None: httpx.Response(401, text="unauthorized"),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="invalid or expired"):
        fetch_project_status()


def test_fetch_project_status_supabase_cloud_404(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", "tok-ok")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", "ref-notfound")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://ref-notfound.supabase.co")

    monkeypatch.setattr(
        httpx.Client, "get",
        lambda self, url, headers=None: httpx.Response(404, text="not found"),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="not found"):
        fetch_project_status()


def test_fetch_project_status_supabase_cloud_503(monkeypatch):
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_ACCESS_TOKEN", "tok-ok")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_PROJECT_REF", "ref1")
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "https://ref1.supabase.co")

    monkeypatch.setattr(
        httpx.Client, "get",
        lambda self, url, headers=None: httpx.Response(503, text="service unavailable"),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Supabase API error: 503"):
        fetch_project_status()


# ---------------------------------------------------------------------------
# Testes anteriores (mantidos para não regredir)
# ---------------------------------------------------------------------------

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
    monkeypatch.setattr(supabase_service.settings, "SUPABASE_URL", "http://raylook-postgrest:3000")
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
