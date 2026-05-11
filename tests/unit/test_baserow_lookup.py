"""Testes unitários para app/services/baserow_lookup.py.

Isola chamadas HTTP mockando `metrics.clients.fetch_rows_filtered`
via monkeypatch — sem hit em API real.
"""
from __future__ import annotations

import pytest

import app.services.baserow_lookup as svc


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------

def test_normalize_phone_remove_espacos_e_hifens():
    """Remove tudo que não é dígito."""
    assert svc.normalize_phone("+55 (11) 9 8765-4321") == "5511987654321"


def test_normalize_phone_apenas_digitos():
    assert svc.normalize_phone("11987654321") == "11987654321"


def test_normalize_phone_string_vazia():
    assert svc.normalize_phone("") == ""


def test_normalize_phone_none():
    """None deve retornar string vazia sem exceção."""
    assert svc.normalize_phone(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# poll_id_from_package_snapshot
# ---------------------------------------------------------------------------

def test_poll_id_from_snapshot_campo_poll_id():
    snap = {"poll_id": "abc123"}
    assert svc.poll_id_from_package_snapshot(snap) == "abc123"


def test_poll_id_from_snapshot_campo_pollId_camelCase():
    snap = {"pollId": "xyz"}
    assert svc.poll_id_from_package_snapshot(snap) == "xyz"


def test_poll_id_from_snapshot_poll_id_tem_prioridade():
    snap = {"poll_id": "first", "pollId": "second"}
    assert svc.poll_id_from_package_snapshot(snap) == "first"


def test_poll_id_from_snapshot_deriva_do_id_com_sufixo_numerico():
    """Pacotes seguem o padrão <poll_id>_<i>."""
    snap = {"id": "enquete42_3"}
    assert svc.poll_id_from_package_snapshot(snap) == "enquete42"


def test_poll_id_from_snapshot_id_sem_sufixo_numerico_retorna_none():
    snap = {"id": "somente_texto"}
    assert svc.poll_id_from_package_snapshot(snap) is None


def test_poll_id_from_snapshot_sem_campos_retorna_none():
    assert svc.poll_id_from_package_snapshot({}) is None


def test_poll_id_from_snapshot_poll_id_espaco_retorna_none():
    snap = {"poll_id": "   "}
    assert svc.poll_id_from_package_snapshot(snap) is None


def test_poll_id_from_snapshot_id_nao_string_retorna_none():
    snap = {"id": 123}
    assert svc.poll_id_from_package_snapshot(snap) is None


# ---------------------------------------------------------------------------
# get_poll_data_by_poll_id
# ---------------------------------------------------------------------------

def test_get_poll_data_retorna_titulo_e_valor(monkeypatch):
    """Deve mapear campos da linha Baserow corretamente."""
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [{"title": "Minha Enquete", "valor": "100"}],
    )
    result = svc.get_poll_data_by_poll_id("poll-1")
    assert result == {"title": "Minha Enquete", "valor": "100"}


def test_get_poll_data_usa_field_173_quando_sem_title(monkeypatch):
    """Fallback para campo legado field_173."""
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [{"field_173": "Título Legado", "valor": "50"}],
    )
    result = svc.get_poll_data_by_poll_id("poll-2")
    assert result is not None
    assert result["title"] == "Título Legado"


def test_get_poll_data_retorna_none_quando_sem_linhas(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [],
    )
    assert svc.get_poll_data_by_poll_id("inexistente") is None


def test_get_poll_data_retorna_none_em_excecao(monkeypatch):
    """Exceção na chamada HTTP deve ser capturada e retornar None."""
    def boom(table, params, size=1):
        raise ConnectionError("sem rede")

    monkeypatch.setattr("metrics.clients.fetch_rows_filtered", boom)
    assert svc.get_poll_data_by_poll_id("poll-x") is None


def test_get_poll_data_valor_none_quando_campo_ausente(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [{"title": "Só título"}],
    )
    result = svc.get_poll_data_by_poll_id("poll-3")
    assert result is not None
    assert result["valor"] is None


# ---------------------------------------------------------------------------
# get_poll_title_by_poll_id
# ---------------------------------------------------------------------------

def test_get_poll_title_retorna_titulo(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [{"title": " Título Com Espaços ", "valor": None}],
    )
    assert svc.get_poll_title_by_poll_id("p1") == "Título Com Espaços"


def test_get_poll_title_retorna_none_quando_sem_dados(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=1: [],
    )
    assert svc.get_poll_title_by_poll_id("p2") is None


# ---------------------------------------------------------------------------
# get_latest_vote_row
# ---------------------------------------------------------------------------

def test_get_latest_vote_row_retorna_ultima_linha(monkeypatch):
    """Linha com maior id deve ser retornada."""
    linhas = [
        {"id": 1, "qty": "5"},
        {"id": 3, "qty": "15"},
        {"id": 2, "qty": "10"},
    ]
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: list(linhas),
    )
    row = svc.get_latest_vote_row("poll-1", "11999999999")
    assert row is not None
    assert row["id"] == 3


def test_get_latest_vote_row_sem_linhas_retorna_none(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: [],
    )
    assert svc.get_latest_vote_row("poll-1", "11999999999") is None


def test_get_latest_vote_row_poll_id_vazio_retorna_none():
    """Deve retornar None sem bater na API."""
    assert svc.get_latest_vote_row("", "11999999999") is None


def test_get_latest_vote_row_phone_vazio_retorna_none():
    assert svc.get_latest_vote_row("poll-1", "") is None


def test_get_latest_vote_row_excecao_retorna_none(monkeypatch):
    def boom(table, params, size=200):
        raise TimeoutError("timeout")

    monkeypatch.setattr("metrics.clients.fetch_rows_filtered", boom)
    assert svc.get_latest_vote_row("poll-1", "11999999999") is None


def test_get_latest_vote_row_normaliza_telefone(monkeypatch):
    """Telefone com formatação deve ser normalizado antes da consulta."""
    chamados: list[dict] = []

    def captura(table, params, size=200):
        chamados.append(params)
        return [{"id": 1, "qty": "3"}]

    monkeypatch.setattr("metrics.clients.fetch_rows_filtered", captura)
    svc.get_latest_vote_row("poll-1", "+55 (11) 9 8765-4321")
    assert len(chamados) == 1
    # O valor do filtro deve conter só dígitos
    filtro_phone = chamados[0].get(f"filter__field_{svc._FIELD_VOTOS_VOTER_PHONE}__equal")
    assert filtro_phone == "5511987654321"


# ---------------------------------------------------------------------------
# get_latest_vote_qty
# ---------------------------------------------------------------------------

def test_get_latest_vote_qty_retorna_inteiro(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: [{"id": 1, "qty": "7"}],
    )
    assert svc.get_latest_vote_qty("poll-1", "11999999999") == 7


def test_get_latest_vote_qty_usa_field_164_como_fallback(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: [{"id": 1, "field_164": "4.0"}],
    )
    assert svc.get_latest_vote_qty("poll-1", "11999999999") == 4


def test_get_latest_vote_qty_retorna_none_quando_sem_row(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: [],
    )
    assert svc.get_latest_vote_qty("poll-1", "11999999999") is None


def test_get_latest_vote_qty_retorna_none_quando_qty_invalido(monkeypatch):
    monkeypatch.setattr(
        "metrics.clients.fetch_rows_filtered",
        lambda table, params, size=200: [{"id": 1, "qty": "nao_numero"}],
    )
    assert svc.get_latest_vote_qty("poll-1", "11999999999") is None
