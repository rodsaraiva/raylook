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


# ── _require_supabase_in_test_mode ────────────────────────────────────────────

def test_require_supabase_in_test_mode_raises_when_test_mode_no_supabase(monkeypatch):
    """TEST_MODE=True + supabase desabilitado → RuntimeError."""
    from app.config import settings
    monkeypatch.setattr(settings, "TEST_MODE", True, raising=False)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="Supabase customer storage"):
        svc._require_supabase_in_test_mode()


def test_require_supabase_in_test_mode_ok_when_supabase_enabled(monkeypatch):
    """TEST_MODE=True + supabase habilitado → sem exceção."""
    from app.config import settings
    monkeypatch.setattr(settings, "TEST_MODE", True, raising=False)
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    svc._require_supabase_in_test_mode()  # não deve levantar


def test_require_supabase_in_test_mode_no_op_when_test_mode_false(monkeypatch):
    """TEST_MODE=False → nunca levanta, independente de supabase.

    Usa patch em getattr de settings para isolar do estado global da suite.
    """
    # Patchamos settings via getattr para garantir isolamento mesmo
    # quando o settings singleton é compartilhado entre testes
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: False)

    original_getattr = getattr(svc.settings, "TEST_MODE", False)
    try:
        # Forçar TEST_MODE = False diretamente no objeto settings
        svc.settings.__class__.TEST_MODE = property(lambda self: False)
        svc._require_supabase_in_test_mode()  # não deve levantar
    finally:
        # Restaurar — a property original é o bool field
        del svc.settings.__class__.TEST_MODE


# ── _save_customers_to_file (exception handler) ───────────────────────────────

def test_save_customers_to_file_exception_handler(monkeypatch, tmp_path, caplog):
    """Quando a escrita lança exceção, loga error e limpa tmp sem propagar."""
    import logging
    from pathlib import Path
    caplog.set_level(logging.ERROR, logger="raylook.customer_service")

    bad_path = tmp_path / "data" / "customers.json"
    monkeypatch.setattr(svc, "CUSTOMERS_FILE", bad_path)

    # Patch json.dump para lançar no momento da escrita
    import json as json_mod
    original_dump = json_mod.dump

    def exploding_dump(obj, fp, **kwargs):
        raise OSError("disco cheio simulado")

    monkeypatch.setattr(json_mod, "dump", exploding_dump)
    # Não deve propagar
    svc._save_customers_to_file({"5511999999999": "Ana"})
    assert any("Erro ao salvar" in r.message for r in caplog.records)


# ── _build_customer_rows_supabase — joins pagamentos/vendas/clientes ──────────

def test_build_customer_rows_supabase_asaas_paid_counts(monkeypatch):
    """Venda + pagamento status=paid: total_paid acumulado e qty correto."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-1", "venda_id": "v-1", "status": "paid", "updated_at": "2026-05-01"}],
                "vendas": [{"id": "v-1", "cliente_id": "c-1", "total_amount": 150.0, "qty": 5}],
                "clientes": [{"id": "c-1", "celular": "5511111111111", "nome": "Ana"}],
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert len(rows) == 1
    ana = rows[0]
    assert ana["total_paid"] == 150.0
    assert ana["qty"] == 5
    assert ana["last_pay_click_at"] == "2026-05-01"


def test_build_customer_rows_supabase_asaas_pending_counts_debt(monkeypatch):
    """Venda + pagamento status=pending: vai para total_debt."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-2", "venda_id": "v-2", "status": "pending", "updated_at": None}],
                "vendas": [{"id": "v-2", "cliente_id": "c-2", "total_amount": 80.0, "qty": 2}],
                "clientes": [{"id": "c-2", "celular": "5511222222222", "nome": "Bia"}],
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert len(rows) == 1
    bia = rows[0]
    assert bia["total_debt"] == 80.0
    assert bia["total_paid"] == 0.0


def test_build_customer_rows_supabase_asaas_cancelled_skipped(monkeypatch):
    """Pagamento cancelado não conta em nada."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-3", "venda_id": "v-3", "status": "cancelled", "updated_at": None}],
                "vendas": [{"id": "v-3", "cliente_id": "c-3", "total_amount": 200.0, "qty": 4}],
                "clientes": [{"id": "c-3", "celular": "5511333333333", "nome": "Carlos"}],
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    # Carlos foi criado mas está zerado por estar cancelado
    assert len(rows) == 1
    carlos = rows[0]
    assert carlos["total_paid"] == 0.0
    assert carlos["total_debt"] == 0.0
    assert carlos["qty"] == 0


def test_build_customer_rows_supabase_venda_sem_pagamento_ignored(monkeypatch):
    """Venda sem pagamento correspondente é descartada."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [],  # sem pagamento
                "vendas": [{"id": "v-4", "cliente_id": "c-4", "total_amount": 50.0, "qty": 1}],
                "clientes": [{"id": "c-4", "celular": "5511444444444", "nome": "Dani"}],
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert rows == []


def test_build_customer_rows_supabase_venda_sem_cliente_id_ignored(monkeypatch):
    """Venda cujo cliente_id não tem celular correspondente é descartada."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-5", "venda_id": "v-5", "status": "paid", "updated_at": None}],
                "vendas": [{"id": "v-5", "cliente_id": "c-unknown", "total_amount": 50.0, "qty": 1}],
                "clientes": [],  # sem cliente c-unknown
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert rows == []


def test_build_customer_rows_supabase_asaas_sent_updates_last_click(monkeypatch):
    """Status=sent registra last_pay_click_at com o updated_at do pagamento."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-6", "venda_id": "v-6", "status": "sent",
                                "updated_at": "2026-03-15T10:00:00"}],
                "vendas": [{"id": "v-6", "cliente_id": "c-6", "total_amount": 30.0, "qty": 1}],
                "clientes": [{"id": "c-6", "celular": "5511666666666", "nome": "Eva"}],
                "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert rows[0]["last_pay_click_at"] == "2026-03-15T10:00:00"


def test_build_customer_rows_supabase_pagamentos_exception_continues(monkeypatch, caplog):
    """Exceção no bloco pagamentos/vendas loga warning mas não interrompe."""
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.customer_service")

    class ErrorOnPagamentos(FakeClientWithRpc):
        def select_all(self, table, **kwargs):
            if table == "pagamentos":
                raise RuntimeError("DB indisponível")
            return super().select_all(table, **kwargs)

    fake = ErrorOnPagamentos(
        tables={**empty_tables(),
                "legacy_charges": [
                    {"customer_phone": "5511111111111", "customer_name": "Ana",
                     "total_amount": 50.0, "quantity": 1, "status": "paid"}
                ],
                "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    # legacy_charges ainda deve ter sido processado
    assert any(r["phone"] == "5511111111111" for r in rows)
    assert any("aggregation falhou" in r.message for r in caplog.records)


def test_build_customer_rows_supabase_legacy_name_fallback(monkeypatch):
    """Quando RPC não retorna nome mas legacy_charges tem, o nome é preenchido."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [
                    {"customer_phone": "5511777777777", "customer_name": "Fábio",
                     "total_amount": 60.0, "quantity": 2, "status": "paid"},
                ],
                "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": [
            {"celular": "5511777777777", "nome": ""},  # nome vazio no RPC
        ]},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert rows[0]["name"] == "Fábio"


# ── combined_pix (app_runtime_state) ─────────────────────────────────────────

def test_build_customer_rows_supabase_combined_pix_updates_last_click(monkeypatch):
    """combined_pix state com created_at mais recente atualiza last_pay_click_at."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [
                    {"customer_phone": "5511888888888", "customer_name": "Gabi",
                     "total_amount": 100.0, "quantity": 2, "status": "paid"},
                ],
                "pagamentos": [], "vendas": [],
                "clientes": [{"id": "c-gabi", "celular": "5511888888888", "nome": "Gabi"}],
                "app_runtime_state": [
                    {
                        "key": "combined_pix_c-gabi",
                        "payload_json": {"cliente_id": "c-gabi", "created_at": "2026-05-10T12:00:00"},
                        "updated_at": "2026-05-10T12:00:00",
                    }
                ]},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    gabi = next(r for r in rows if r["phone"] == "5511888888888")
    assert gabi["last_pay_click_at"] == "2026-05-10T12:00:00"


def test_build_customer_rows_supabase_combined_pix_no_override_if_older(monkeypatch):
    """combined_pix com created_at mais antigo que last_pay_click_at existente não sobrescreve."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [{"id": "pag-h", "venda_id": "v-h", "status": "sent",
                                "updated_at": "2026-06-01T10:00:00"}],
                "vendas": [{"id": "v-h", "cliente_id": "c-h", "total_amount": 50.0, "qty": 1}],
                "clientes": [{"id": "c-h", "celular": "5511900000001", "nome": "Hugo"}],
                "app_runtime_state": [
                    {
                        "key": "combined_pix_c-h",
                        "payload_json": {"cliente_id": "c-h", "created_at": "2026-01-01T00:00:00"},
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ]},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    hugo = next(r for r in rows if r["phone"] == "5511900000001")
    # O sent em 2026-06-01 é mais recente; combined_pix 2026-01-01 não deve sobrescrever
    assert hugo["last_pay_click_at"] == "2026-06-01T10:00:00"


def test_build_customer_rows_supabase_combined_pix_phone_not_in_map_ignored(monkeypatch):
    """combined_pix para cliente sem entradas em qty_by_phone é ignorado."""
    fake = FakeClientWithRpc(
        tables={**empty_tables(),
                "legacy_charges": [],
                "pagamentos": [], "vendas": [],
                "clientes": [{"id": "c-x", "celular": "5511900000099", "nome": "X"}],
                "app_runtime_state": [
                    {
                        "key": "combined_pix_c-x",
                        "payload_json": {"cliente_id": "c-x", "created_at": "2026-05-01T00:00:00"},
                        "updated_at": "2026-05-01T00:00:00",
                    }
                ]},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    # Sem entradas de charges/pagamentos, phone não entrou em qty_by_phone → ignorado
    assert rows == []


def test_build_customer_rows_supabase_combined_pix_exception_continues(monkeypatch, caplog):
    """Exceção no bloco combined_pix loga warning e retorna linhas parciais."""
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.customer_service")

    call_count = {"n": 0}

    class ErrorOnRuntimeState(FakeClientWithRpc):
        def select_all(self, table, **kwargs):
            if table == "app_runtime_state":
                raise RuntimeError("tabela inacessível")
            return super().select_all(table, **kwargs)

    fake = ErrorOnRuntimeState(
        tables={**empty_tables(),
                "legacy_charges": [
                    {"customer_phone": "5511555555555", "customer_name": "Iris",
                     "total_amount": 40.0, "quantity": 1, "status": "paid"},
                ],
                "pagamentos": [], "vendas": [], "clientes": [], "app_runtime_state": []},
        rpc_results={"get_customer_stats": []},
    )
    monkeypatch.setattr("app.services.customer_service.SupabaseRestClient.from_settings",
                        staticmethod(lambda: fake))
    rows = svc._build_customer_rows_supabase()
    assert any(r["phone"] == "5511555555555" for r in rows)
    assert any("combined_pix aggregation falhou" in r.message for r in caplog.records)


# ── list_customer_rows — com cache (runtime_state) ────────────────────────────

def test_list_customer_rows_supabase_with_cache_returns_cached(monkeypatch):
    """Com supabase + runtime_state habilitado e cache presente, retorna cache."""
    cached = [{"name": "Ana", "phone": "5511111111111", "qty": 1, "total_paid": 10.0}]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.load_runtime_state",
                        lambda key: {"items": cached})
    # refresh não deve ser chamado
    refreshed = []
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: refreshed.append(1) or [])

    result = svc.list_customer_rows()
    assert result == cached
    assert refreshed == []


def test_list_customer_rows_supabase_cache_miss_calls_refresh(monkeypatch):
    """Cache ausente → chama refresh_customer_rows_snapshot."""
    expected = [{"name": "Bia", "phone": "5511222222222", "qty": 2, "total_paid": 20.0}]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.load_runtime_state", lambda key: None)
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: expected)
    result = svc.list_customer_rows()
    assert result == expected


def test_list_customer_rows_supabase_cache_items_not_list_calls_refresh(monkeypatch):
    """Cache com payload mas 'items' não é lista → chama refresh."""
    expected = [{"name": "Carlos", "phone": "5511333333333", "qty": 1, "total_paid": 5.0}]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.load_runtime_state",
                        lambda key: {"items": "not-a-list"})
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: expected)
    result = svc.list_customer_rows()
    assert result == expected


def test_list_customer_rows_no_runtime_state_calls_refresh(monkeypatch):
    """Com supabase mas sem runtime_state → vai direto para refresh."""
    expected = [{"name": "Dani", "phone": "5511444444444", "qty": 3, "total_paid": 30.0}]
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service.refresh_customer_rows_snapshot",
                        lambda: expected)
    result = svc.list_customer_rows()
    assert result == expected


# ── refresh_customer_rows_snapshot — com supabase + save_runtime_state ────────

def test_refresh_customer_rows_snapshot_supabase_saves_state(monkeypatch):
    """Com supabase + runtime_state, chama _build e salva no estado de runtime."""
    built_rows = [{"name": "Eva", "phone": "5511555555555", "qty": 1, "total_paid": 5.0}]
    saved = []
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service._build_customer_rows_supabase",
                        lambda: built_rows)
    monkeypatch.setattr("app.services.customer_service.save_runtime_state",
                        lambda key, payload: saved.append((key, payload)))

    result = svc.refresh_customer_rows_snapshot()
    assert result == built_rows
    assert len(saved) == 1
    assert saved[0][1] == {"items": built_rows}


def test_refresh_customer_rows_snapshot_supabase_no_runtime_state(monkeypatch):
    """Com supabase mas sem runtime_state, só chama _build sem salvar."""
    built_rows = [{"name": "Fábio", "phone": "5511666666666", "qty": 1, "total_paid": 10.0}]
    saved = []
    monkeypatch.setattr("app.services.customer_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.customer_service.runtime_state_enabled", lambda: False)
    monkeypatch.setattr("app.services.customer_service._build_customer_rows_supabase",
                        lambda: built_rows)
    monkeypatch.setattr("app.services.customer_service.save_runtime_state",
                        lambda key, payload: saved.append((key, payload)))

    result = svc.refresh_customer_rows_snapshot()
    assert result == built_rows
    assert saved == []
