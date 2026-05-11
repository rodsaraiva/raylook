"""Testes de app/services/customer_service.py.

Cobre funções puras (_normalize_phone, _load/_save via arquivo) e métodos
DB-bound (load_customers, save_customers, update_customer, list_customer_rows_page,
search_customers_light) usando FakeSupabaseClient.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services import customer_service as svc
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables


# ── _normalize_phone ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("5511999999999", "5511999999999"),
    ("+55 (11) 99999-9999", "5511999999999"),
    ("55 11 99999 9999", "5511999999999"),
    ("", ""),
    (None, ""),
    ("abc", ""),
    (5511999999999, "5511999999999"),
    ("  11 999.999.9999  ", "119999999999"),
])
def test_normalize_phone_strips_non_digits(raw, expected):
    assert svc._normalize_phone(raw) == expected


# ── _load_customers_from_file / _save_customers_to_file ──────────────────────

def test_load_customers_from_file_missing_returns_empty(monkeypatch, tmp_path):
    """Arquivo inexistente → dict vazio sem erros."""
    missing = tmp_path / "naoexiste.json"
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", missing)
    assert svc._load_customers_from_file() == {}


def test_load_customers_from_file_reads_dict(monkeypatch, tmp_path):
    f = tmp_path / "customers.json"
    f.write_text('{"5511999999999": "Ana", "+55 11 88888-8888": "Bia"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    result = svc._load_customers_from_file()
    assert result["5511999999999"] == "Ana"
    assert result["5511888888888"] == "Bia"


def test_load_customers_from_file_normalizes_keys(monkeypatch, tmp_path):
    """Chaves são normalizadas (só dígitos) ao carregar."""
    f = tmp_path / "customers.json"
    f.write_text('{"+55 11 99999-9999": "Carlos"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    result = svc._load_customers_from_file()
    assert "5511999999999" in result
    assert result["5511999999999"] == "Carlos"


def test_load_customers_from_file_ignores_non_dict(monkeypatch, tmp_path):
    """Conteúdo não-dict (lista) → retorna {} sem exceção."""
    f = tmp_path / "customers.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    assert svc._load_customers_from_file() == {}


def test_load_customers_from_file_ignores_malformed_json(monkeypatch, tmp_path):
    """JSON inválido → retorna {} sem propagar exceção."""
    f = tmp_path / "customers.json"
    f.write_text("{bad json}", encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    assert svc._load_customers_from_file() == {}


def test_load_customers_from_file_skips_empty_phones(monkeypatch, tmp_path):
    """Chaves que viram string vazia após normalização são descartadas."""
    f = tmp_path / "customers.json"
    f.write_text('{"abc": "Nome", "5511999999999": "Ana"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    result = svc._load_customers_from_file()
    assert "abc" not in result
    assert len(result) == 1


def test_save_and_reload_roundtrip(monkeypatch, tmp_path):
    """Salvar e recarregar produz o mesmo dict."""
    f = tmp_path / "data" / "customers.json"
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    data = {"5511999999999": "Ana", "5511888888888": "Bia"}
    svc._save_customers_to_file(data)
    assert f.exists()
    result = svc._load_customers_from_file()
    assert result == data


def test_save_customers_to_file_creates_parent_dirs(monkeypatch, tmp_path):
    """Diretório pai é criado se não existir."""
    f = tmp_path / "deep" / "nested" / "customers.json"
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    svc._save_customers_to_file({"5511999999999": "Ana"})
    assert f.exists()
    content = json.loads(f.read_text(encoding="utf-8"))
    assert content["5511999999999"] == "Ana"


# ── load_customers (sem Supabase) ─────────────────────────────────────────────

def test_load_customers_file_mode(monkeypatch, tmp_path):
    """Sem supabase_domain_enabled, carrega do arquivo."""
    f = tmp_path / "customers.json"
    f.write_text('{"5511999999999": "Ana"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    result = svc.load_customers()
    assert result == {"5511999999999": "Ana"}


# ── load_customers (com Supabase fake) ───────────────────────────────────────

def test_load_customers_supabase_mode(monkeypatch):
    """Com supabase habilitado, lê da tabela clientes."""
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"celular": "5511999999999", "nome": "Ana", "updated_at": "2026-05-10"},
        {"celular": "+55 11 88888-8888", "nome": "Bia", "updated_at": "2026-05-09"},
        {"celular": "", "nome": "Sem Phone", "updated_at": "2026-05-08"},  # descartado
    ]})
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    result = svc.load_customers()
    assert result["5511999999999"] == "Ana"
    assert result["5511888888888"] == "Bia"
    assert "" not in result
    assert len(result) == 2


# ── save_customers ────────────────────────────────────────────────────────────

def test_save_customers_file_mode(monkeypatch, tmp_path):
    """Sem supabase, persiste no arquivo."""
    f = tmp_path / "customers.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    svc.save_customers({"5511999999999": "Ana"})
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["5511999999999"] == "Ana"


def test_save_customers_supabase_mode_upserts(monkeypatch):
    """Com supabase, chama insert upsert e ignora phones vazios."""
    inserted = []
    fake = FakeSupabaseClient(empty_tables())
    original_insert = fake.insert

    def capturing_insert(table, values, **kwargs):
        if isinstance(values, list):
            inserted.extend(values)
        return {}

    fake.insert = capturing_insert
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: [])

    svc.save_customers({
        "5511999999999": "Ana",
        "abc": "Invalido",   # phone vazio após normalize → ignorado
        "5511888888888": "Bia",
    })
    phones = [r["celular"] for r in inserted]
    assert "5511999999999" in phones
    assert "5511888888888" in phones
    assert all(svc._normalize_phone(p) == p for p in phones)


def test_save_customers_supabase_empty_payload_skips_insert(monkeypatch):
    """Se todos os phones forem inválidos, não chama insert."""
    called = []
    fake = FakeSupabaseClient(empty_tables())
    fake.insert = lambda *a, **kw: called.append(a)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    svc.save_customers({"abc": "Sem Digito"})
    assert called == []


# ── update_customer ───────────────────────────────────────────────────────────

def test_update_customer_file_mode(monkeypatch, tmp_path):
    """Sem supabase, atualiza/cria entrada no arquivo."""
    f = tmp_path / "customers.json"
    f.write_text('{"5511999999999": "Ana"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    svc.update_customer("+55 11 99999-9999", "Ana Nova")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["5511999999999"] == "Ana Nova"


def test_update_customer_ignores_empty_phone(monkeypatch, tmp_path):
    """Phone que normaliza para vazio → operação é no-op."""
    f = tmp_path / "customers.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    svc.update_customer("abc", "Nome")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data == {}


def test_update_customer_supabase_mode(monkeypatch):
    """Com supabase, chama upsert_one e depois refresh snapshot."""
    upserted = []
    refreshed = []

    class FakeClientWithUpsert(FakeSupabaseClient):
        def upsert_one(self, table, values, **kwargs):
            upserted.append((table, values))

    fake = FakeClientWithUpsert(empty_tables())
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: refreshed.append(1) or [])

    svc.update_customer("5511999999999", "Ana")
    assert len(upserted) == 1
    assert upserted[0] == ("clientes", {"celular": "5511999999999", "nome": "Ana"})
    assert len(refreshed) == 1


# ── get_customer_name ─────────────────────────────────────────────────────────

def test_get_customer_name_found(monkeypatch, tmp_path):
    f = tmp_path / "customers.json"
    f.write_text('{"5511999999999": "Ana"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    assert svc.get_customer_name("5511999999999") == "Ana"


def test_get_customer_name_not_found_returns_default(monkeypatch, tmp_path):
    f = tmp_path / "customers.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    assert svc.get_customer_name("5511000000000") == "Desconhecido"
    assert svc.get_customer_name("5511000000000", default="N/A") == "N/A"


def test_get_customer_name_normalizes_phone(monkeypatch, tmp_path):
    """Formato com traço/parênteses é normalizado antes de buscar."""
    f = tmp_path / "customers.json"
    f.write_text('{"5511999999999": "Carlos"}', encoding="utf-8")
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", f)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    assert svc.get_customer_name("+55 (11) 99999-9999") == "Carlos"


# ── sync_customer_names ───────────────────────────────────────────────────────

def test_sync_customer_names_passthrough():
    """Identidade — retorna o próprio argumento sem modificação."""
    data = {"key": "value", "nested": [1, 2, 3]}
    assert svc.sync_customer_names(data) is data


# ── list_customer_rows_page ───────────────────────────────────────────────────

def _make_rows(n: int):
    return [{"name": f"Cliente {i}", "phone": f"551199900{i:04d}",
             "qty": i, "total_paid": float(i * 10)} for i in range(1, n + 1)]


def test_list_customer_rows_page_default(monkeypatch):
    """Sem filtro: retorna página 1 com page_size itens."""
    rows = _make_rows(10)
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(page=1, page_size=5)
    assert result["total"] == 10
    assert result["page"] == 1
    assert result["page_size"] == 5
    assert len(result["items"]) == 5
    assert result["has_prev"] is False
    assert result["has_next"] is True


def test_list_customer_rows_page_last_page(monkeypatch):
    rows = _make_rows(7)
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(page=2, page_size=5)
    assert len(result["items"]) == 2
    assert result["has_prev"] is True
    assert result["has_next"] is False


def test_list_customer_rows_page_search_by_name(monkeypatch):
    rows = [
        {"name": "Ana Silva", "phone": "5511111111111", "qty": 1, "total_paid": 10.0},
        {"name": "Bia Santos", "phone": "5511222222222", "qty": 2, "total_paid": 20.0},
        {"name": "Carlos Ana", "phone": "5511333333333", "qty": 3, "total_paid": 30.0},
    ]
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(search="ana")
    assert result["total"] == 2
    names = [r["name"] for r in result["items"]]
    assert "Ana Silva" in names
    assert "Carlos Ana" in names


def test_list_customer_rows_page_search_by_phone(monkeypatch):
    rows = [
        {"name": "Ana", "phone": "5511111111111", "qty": 1, "total_paid": 0.0},
        {"name": "Bia", "phone": "5511222222222", "qty": 2, "total_paid": 0.0},
    ]
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(search="2222")
    assert result["total"] == 1
    assert result["items"][0]["name"] == "Bia"


def test_list_customer_rows_page_search_no_match(monkeypatch):
    rows = _make_rows(5)
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(search="XYZ_NAOEXISTE")
    assert result["total"] == 0
    assert result["items"] == []


def test_list_customer_rows_page_clamps_values(monkeypatch):
    """page<=0 vira 1; page_size=0 usa default 50 via 'or 50'; page_size>200 vira 200."""
    rows = _make_rows(3)
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)

    # page negativa é normalizada pra 1
    result_neg = svc.list_customer_rows_page(page=0, page_size=10)
    assert result_neg["page"] == 1

    # page_size=0 usa fallback 50 (comportamento de `int(0 or 50)`)
    result_zero_ps = svc.list_customer_rows_page(page=1, page_size=0)
    assert result_zero_ps["page_size"] == 50

    # page_size acima de 200 é limitado a 200
    result_big = svc.list_customer_rows_page(page=1, page_size=999)
    assert result_big["page_size"] == 200


def test_list_customer_rows_page_empty_search_returns_all(monkeypatch):
    rows = _make_rows(3)
    monkeypatch.setattr("app.services.customer_service.list_customer_rows", lambda: rows)
    result = svc.list_customer_rows_page(search="   ")  # só espaços
    assert result["total"] == 3


# ── search_customers_light ────────────────────────────────────────────────────

def test_search_customers_light_returns_empty_without_supabase(monkeypatch):
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    assert svc.search_customers_light("Ana") == []


def test_search_customers_light_short_query_returns_empty(monkeypatch):
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    assert svc.search_customers_light("A") == []
    assert svc.search_customers_light("") == []


def test_search_customers_light_by_name(monkeypatch):
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"celular": "5511111111111", "nome": "Ana Silva"},
        {"celular": "5511222222222", "nome": "Bia Santos"},
    ]})

    # Override select para simular ilike por nome
    original_select = fake.select
    def fake_select(table, *, columns="*", filters=None, limit=None, **kwargs):
        rows = fake.tables.get(table, [])
        if filters:
            result = []
            for row in rows:
                match = True
                for field, op, value in filters:
                    if op == "ilike":
                        pattern = value.replace("*", "").lower()
                        if pattern not in str(row.get(field) or "").lower():
                            match = False
                            break
                if match:
                    result.append(row)
            rows = result
        if limit:
            rows = rows[:limit]
        return rows

    fake.select = fake_select

    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    result = svc.search_customers_light("ana")
    phones = [r["phone"] for r in result]
    assert "5511111111111" in phones


def test_search_customers_light_deduplicates_results(monkeypatch):
    """Mesmo cliente retornado por nome e por phone → aparece só uma vez."""
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"celular": "5511111111111", "nome": "Ana"},
    ]})

    def fake_select(table, *, columns="*", filters=None, limit=None, **kwargs):
        return [{"celular": "5511111111111", "nome": "Ana"}]

    fake.select = fake_select
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    result = svc.search_customers_light("Ana")
    assert len(result) == 1
    assert result[0]["phone"] == "5511111111111"


def test_search_customers_light_respects_limit(monkeypatch):
    """Retorna no máximo `limit` resultados."""
    rows = [{"celular": f"5511{i:09d}", "nome": f"Cliente {i}"} for i in range(20)]
    fake = FakeSupabaseClient({**empty_tables(), "clientes": rows})

    def fake_select(table, *, columns="*", filters=None, limit=None, **kwargs):
        result = list(fake.tables.get(table, []))
        if limit:
            result = result[:limit]
        return result

    fake.select = fake_select
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    result = svc.search_customers_light("cliente", limit=5)
    assert len(result) <= 5


def test_search_customers_light_digits_only_query(monkeypatch):
    """Query numérica busca por celular."""
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"celular": "5511999999999", "nome": "Ana"},
    ]})

    def fake_select(table, *, columns="*", filters=None, limit=None, **kwargs):
        rows = list(fake.tables.get(table, []))
        if filters:
            for field, op, value in filters:
                if op == "ilike":
                    pattern = value.replace("*", "").lower()
                    rows = [r for r in rows if pattern in str(r.get(field) or "").lower()]
        if limit:
            rows = rows[:limit]
        return rows

    fake.select = fake_select
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    result = svc.search_customers_light("9999")
    assert any(r["phone"] == "5511999999999" for r in result)


# ── _build_customer_rows_supabase ─────────────────────────────────────────────

class FakeClientWithRpc(FakeSupabaseClient):
    """FakeSupabaseClient com suporte a rpc()."""

    def __init__(self, tables, rpc_results=None):
        super().__init__(tables)
        self._rpc_results = rpc_results or {}

    def rpc(self, name: str, params: dict):
        result = self._rpc_results.get(name)
        if isinstance(result, Exception):
            raise result
        return result


def test_build_customer_rows_supabase_basic(monkeypatch):
    """Agrega stats + legacy_charges e produz linhas ordenadas por total_paid desc."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(), "legacy_charges": [
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 100.0, "quantity": 3, "status": "paid"},
            {"customer_phone": "5511222222222", "customer_name": "Bia",
             "total_amount": 200.0, "quantity": 6, "status": "paid"},
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 50.0, "quantity": 1, "status": "pending"},
        ], "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    rows = svc._build_customer_rows_supabase()
    phones = [r["phone"] for r in rows]
    # Bia tem mais total_paid (200) → aparece antes
    assert phones.index("5511222222222") < phones.index("5511111111111")
    ana = next(r for r in rows if r["phone"] == "5511111111111")
    assert ana["total_paid"] == 100.0
    assert ana["total_debt"] == 50.0
    assert ana["qty"] == 3


def test_build_customer_rows_supabase_rpc_populates_names(monkeypatch):
    """RPC get_customer_stats popula nomes antes do legacy_charges."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(), "legacy_charges": [], "pagamentos": [],
                "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": [
            {"celular": "5511999999999", "nome": "Carlos"},
        ]},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    rows = svc._build_customer_rows_supabase()
    assert len(rows) == 1
    assert rows[0]["name"] == "Carlos"
    assert rows[0]["phone"] == "5511999999999"


def test_build_customer_rows_supabase_rpc_exception_continues(monkeypatch):
    """Falha no RPC não interrompe — segue com legacy_charges."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(), "legacy_charges": [
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 80.0, "quantity": 2, "status": "paid"},
        ], "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": RuntimeError("RPC down")},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    rows = svc._build_customer_rows_supabase()
    assert len(rows) == 1
    assert rows[0]["phone"] == "5511111111111"


def test_build_customer_rows_supabase_legacy_charges_pending_statuses(monkeypatch):
    """Statuses em PENDING vão para total_debt; cancelled e outros são ignorados."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(), "legacy_charges": [
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 30.0, "quantity": 1, "status": "enviando"},
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 10.0, "quantity": 1, "status": "erro no envio"},
            {"customer_phone": "5511111111111", "customer_name": "Ana",
             "total_amount": 5.0, "quantity": 1, "status": "cancelled"},  # ignorado
        ], "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    rows = svc._build_customer_rows_supabase()
    assert len(rows) == 1
    ana = rows[0]
    assert ana["total_debt"] == 40.0
    assert ana["total_paid"] == 0.0


def test_build_customer_rows_supabase_legacy_charges_phone_empty_skipped(monkeypatch):
    """Entrada de legacy_charges sem phone válido é descartada."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(), "legacy_charges": [
            {"customer_phone": "", "customer_name": "Sem Phone",
             "total_amount": 100.0, "quantity": 3, "status": "paid"},
        ], "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))

    rows = svc._build_customer_rows_supabase()
    assert rows == []


# ── list_customer_rows / refresh_customer_rows_snapshot (sem supabase) ────────

def test_list_customer_rows_no_supabase_delegates_to_build(monkeypatch):
    """Sem supabase, delega para build_customer_rows do staging service."""
    expected = [{"name": "Ana", "phone": "5511999999999", "qty": 1, "total_paid": 10.0}]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._require_supabase_in_test_mode", lambda: None)
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: {})

    # Patch imports dentro da função
    import app.services.finance_service as fs
    import app.services.staging_dry_run_service as sds
    monkeypatch.setattr(fs, "list_charges", lambda: [])
    monkeypatch.setattr(sds, "build_customer_rows", lambda customers, charges: expected)

    result = svc.list_customer_rows()
    assert result == expected


def test_refresh_customer_rows_snapshot_no_supabase_sorts(monkeypatch):
    """Sem supabase, refresh ordena por total_paid desc, qty desc, name."""
    rows_unordered = [
        {"name": "Bia", "phone": "5511222222222", "qty": 1, "total_paid": 50.0},
        {"name": "Ana", "phone": "5511111111111", "qty": 3, "total_paid": 100.0},
        {"name": "Carlos", "phone": "5511333333333", "qty": 2, "total_paid": 50.0},
    ]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: {})

    import app.services.finance_service as fs
    import app.services.staging_dry_run_service as sds
    monkeypatch.setattr(fs, "list_charges", lambda: [])
    monkeypatch.setattr(sds, "build_customer_rows", lambda customers, charges: list(rows_unordered))

    result = svc.refresh_customer_rows_snapshot()
    assert result[0]["total_paid"] == 100.0
    # Ana (100) primeiro; Bia e Carlos empatam em total_paid=50, ordenados por qty desc
    assert result[1]["name"] == "Carlos"  # qty=2 > qty=1
    assert result[2]["name"] == "Bia"
