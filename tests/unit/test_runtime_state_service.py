"""Testes unitários para app/services/runtime_state_service.py.

Usa FakeSupabaseClient para simular o banco sem chamadas reais.
"""
from __future__ import annotations

import pytest

import app.services.runtime_state_service as svc
from app.services.supabase_service import SupabaseRestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, FROZEN_NOW


# ---------------------------------------------------------------------------
# Fixture: cliente falso com patch em from_settings
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    """Retorna FakeSupabaseClient com tabela app_runtime_state vazia."""
    fake = FakeSupabaseClient(tables={"app_runtime_state": []})

    # Garante que runtime_state_enabled() == True
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.runtime_state_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake),
    )
    return fake


# ---------------------------------------------------------------------------
# runtime_state_enabled
# ---------------------------------------------------------------------------

def test_runtime_state_enabled_delega_para_supabase_domain_enabled(monkeypatch):
    """runtime_state_enabled deve retornar o mesmo valor que supabase_domain_enabled."""
    # Patcha supabase_domain_enabled no namespace do módulo svc
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: False)
    assert svc.runtime_state_enabled() is False

    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    assert svc.runtime_state_enabled() is True


# ---------------------------------------------------------------------------
# load_runtime_state
# ---------------------------------------------------------------------------

def test_load_runtime_state_retorna_none_quando_desabilitado(monkeypatch):
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    assert svc.load_runtime_state("qualquer_chave") is None


def test_load_runtime_state_retorna_payload_existente(fake_db):
    """Deve retornar o dict quando a chave existe."""
    payload = {"temperatura": 36.5, "ativo": True}
    fake_db.tables["app_runtime_state"].append(
        {"key": "minha_chave", "payload_json": payload, "updated_at": FROZEN_NOW}
    )
    result = svc.load_runtime_state("minha_chave")
    assert result == payload


def test_load_runtime_state_retorna_none_chave_inexistente(fake_db):
    result = svc.load_runtime_state("nao_existe")
    assert result is None


def test_load_runtime_state_retorna_none_quando_payload_nao_dict(fake_db):
    """payload_json string não é dict — deve retornar None."""
    fake_db.tables["app_runtime_state"].append(
        {"key": "chave_str", "payload_json": "valor_simples", "updated_at": FROZEN_NOW}
    )
    assert svc.load_runtime_state("chave_str") is None


def test_load_runtime_state_retorna_none_payload_none(fake_db):
    fake_db.tables["app_runtime_state"].append(
        {"key": "chave_null", "payload_json": None, "updated_at": FROZEN_NOW}
    )
    assert svc.load_runtime_state("chave_null") is None


# ---------------------------------------------------------------------------
# load_runtime_state_metadata
# ---------------------------------------------------------------------------

def test_load_runtime_state_metadata_retorna_vazio_quando_desabilitado(monkeypatch):
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    assert svc.load_runtime_state_metadata(["k1", "k2"]) == {}


def test_load_runtime_state_metadata_retorna_vazio_keys_vazias(fake_db):
    assert svc.load_runtime_state_metadata([]) == {}


def test_load_runtime_state_metadata_retorna_vazio_keys_apenas_espacos(fake_db):
    assert svc.load_runtime_state_metadata(["  ", "\t"]) == {}


def test_load_runtime_state_metadata_retorna_updated_at(fake_db):
    """Deve mapear cada chave ao seu updated_at."""
    fake_db.tables["app_runtime_state"] = [
        {"key": "k1", "updated_at": "2026-01-01T00:00:00+00:00", "payload_json": {}},
        {"key": "k2", "updated_at": "2026-02-01T00:00:00+00:00", "payload_json": {}},
    ]
    meta = svc.load_runtime_state_metadata(["k1", "k2"])
    assert set(meta.keys()) == {"k1", "k2"}
    assert meta["k1"]["updated_at"] == "2026-01-01T00:00:00+00:00"
    assert meta["k2"]["updated_at"] == "2026-02-01T00:00:00+00:00"


def test_load_runtime_state_metadata_chave_duplicada_normalizada(fake_db):
    """Chaves duplicadas no input não devem causar erro."""
    fake_db.tables["app_runtime_state"] = [
        {"key": "k1", "updated_at": "2026-03-01T00:00:00+00:00", "payload_json": {}}
    ]
    meta = svc.load_runtime_state_metadata(["k1", "k1"])
    assert "k1" in meta


def test_load_runtime_state_metadata_filtra_chave_sem_key(fake_db):
    """Linha com key em branco deve ser ignorada."""
    fake_db.tables["app_runtime_state"] = [
        {"key": "", "updated_at": "2026-03-01T00:00:00+00:00", "payload_json": {}},
        {"key": "k1", "updated_at": "2026-03-01T00:00:00+00:00", "payload_json": {}},
    ]
    meta = svc.load_runtime_state_metadata(["k1"])
    assert "" not in meta
    assert "k1" in meta


# ---------------------------------------------------------------------------
# save_runtime_state
# ---------------------------------------------------------------------------

def test_save_runtime_state_retorna_payload_quando_desabilitado(monkeypatch):
    """Sem backend, deve devolver o payload recebido sem modificar."""
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    payload = {"pix": "combinado", "valor": 150}
    result = svc.save_runtime_state("chave", payload)
    assert result == payload


def test_save_runtime_state_persiste_e_retorna_stored(fake_db, monkeypatch):
    """Deve fazer upsert e retornar o dict armazenado."""
    payload = {"pix": "combinado", "valor": 150}

    # FakeSupabaseClient.insert não suporta upsert_one nativamente;
    # precisamos adicionar o método ao fake para esse teste.
    stored = {}

    def fake_upsert_one(table, data, *, on_conflict):
        stored.update(data)
        fake_db.tables.setdefault(table, []).append(data)
        return data

    fake_db.upsert_one = fake_upsert_one

    result = svc.save_runtime_state("chave_pix", payload)
    assert result == payload


def test_save_runtime_state_retorna_payload_original_quando_stored_nao_dict(fake_db):
    """Se upsert retornar payload_json não-dict, devolve o payload original."""
    def fake_upsert_one(table, data, *, on_conflict):
        return {"payload_json": "string_invalida"}

    fake_db.upsert_one = fake_upsert_one

    payload = {"temperatura": 37.2}
    result = svc.save_runtime_state("temp_key", payload)
    assert result == payload


# ---------------------------------------------------------------------------
# delete_runtime_state
# ---------------------------------------------------------------------------

def test_delete_runtime_state_noop_quando_desabilitado(monkeypatch):
    """Sem backend, não deve lançar exceção."""
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    svc.delete_runtime_state("qualquer")  # não deve lançar


def test_delete_runtime_state_remove_linha(fake_db, monkeypatch):
    """Linha com a chave deve ser removida do banco."""
    fake_db.tables["app_runtime_state"] = [
        {"key": "k1", "payload_json": {}, "updated_at": FROZEN_NOW},
        {"key": "k2", "payload_json": {}, "updated_at": FROZEN_NOW},
    ]
    svc.delete_runtime_state("k1")
    remaining = [r["key"] for r in fake_db.tables["app_runtime_state"]]
    assert "k1" not in remaining
    assert "k2" in remaining


def test_delete_runtime_state_chave_inexistente_sem_erro(fake_db):
    """Deletar chave que não existe não deve lançar exceção."""
    svc.delete_runtime_state("nao_existe")


# ---------------------------------------------------------------------------
# Constantes públicas
# ---------------------------------------------------------------------------

def test_constantes_de_chave_existem():
    """Constantes exportadas devem estar definidas e ser strings."""
    assert isinstance(svc.DASHBOARD_METRICS_STATE_KEY, str)
    assert isinstance(svc.RECENT_IMAGES_STATE_KEY, str)
    assert isinstance(svc.FINANCE_CHARGES_STATE_KEY, str)
    assert isinstance(svc.FINANCE_STATS_STATE_KEY, str)
    assert isinstance(svc.CUSTOMER_ROWS_STATE_KEY, str)
