"""Testes unitários para app/services/routing_service.py.

Cobertura alvo: ≥75% das 84 stmts.
Funções puras são parametrizadas com múltiplos cenários.
load_poll_chat_map é testada via mock de load_domain_poll_chat_map.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

import app.services.routing_service as rs


# ---------------------------------------------------------------------------
# _normalize_text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (None, ""),
    ("", ""),
    ("  hello  ", "hello"),
    (123, "123"),
    (0, "0"),
    (False, "False"),
])
def test_normalize_text(value: Any, expected: str) -> None:
    """Converte qualquer valor para string limpa, None vira string vazia."""
    assert rs._normalize_text(value) == expected


# ---------------------------------------------------------------------------
# _normalize_phone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phone, expected", [
    ("(62) 99335-3390", "62993353390"),
    ("+55 62 9 9335-3390", "5562993353390"),
    ("", ""),
    (None, ""),          # None cai no `phone or ""` dentro da função
    ("abc", ""),
    ("123", "123"),
])
def test_normalize_phone(phone: Any, expected: str) -> None:
    """Remove qualquer caractere não-dígito do telefone."""
    assert rs._normalize_phone(phone) == expected


# ---------------------------------------------------------------------------
# resolve_test_phone
# ---------------------------------------------------------------------------

def test_resolve_test_phone_usa_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Argumento explícito tem prioridade sobre settings."""
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "5562999999999")
    result = rs.resolve_test_phone("(62) 91234-5678")
    assert result == "62912345678"


def test_resolve_test_phone_fallback_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem argumento, usa TEST_PHONE_NUMBER das settings."""
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", "62988887777")
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "5562993353390")
    result = rs.resolve_test_phone()
    assert result == "62988887777"


def test_resolve_test_phone_fallback_estoque(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem TEST_PHONE_NUMBER, usa ESTOQUE_PHONE_NUMBER."""
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "5562993353390")
    result = rs.resolve_test_phone()
    assert result == "5562993353390"


def test_resolve_test_phone_sem_qualquer_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem nenhuma configuração, retorna string vazia."""
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    result = rs.resolve_test_phone()
    assert result == ""


# ---------------------------------------------------------------------------
# resolve_outbound_instance_name
# ---------------------------------------------------------------------------

def test_resolve_outbound_instance_name_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em TEST_MODE, sempre retorna sandbox independente do argumento."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", True)
    assert rs.resolve_outbound_instance_name("producao-instance") == "raylook-sandbox"
    assert rs.resolve_outbound_instance_name(None) == "raylook-sandbox"


def test_resolve_outbound_instance_name_producao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fora de TEST_MODE, retorna o nome configurado normalizado."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    assert rs.resolve_outbound_instance_name("  minha-instancia  ") == "minha-instancia"


def test_resolve_outbound_instance_name_nenhum_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fora de TEST_MODE com None, retorna string vazia."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    assert rs.resolve_outbound_instance_name(None) == ""


# ---------------------------------------------------------------------------
# resolve_poll_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snapshot, expected", [
    # Campo poll_id explícito
    ({"poll_id": "poll123"}, "poll123"),
    # Campo camelCase pollId
    ({"pollId": "abc"}, "abc"),
    # poll_id tem prioridade sobre pollId
    ({"poll_id": "snake", "pollId": "camel"}, "snake"),
    # Derivado de id no formato "{poll}_{index}"
    ({"id": "poll999_0"}, "poll999"),
    ({"id": "abc_2"}, "abc"),
    ({"id": "grupo-teste_10"}, "grupo-teste"),
    # id sem underscore → None
    ({"id": "nodash"}, None),
    # id vazio → None
    ({"id": ""}, None),
    # sem nenhum campo relevante → None
    ({}, None),
    # poll_id vazio mas id válido
    ({"poll_id": "", "id": "x_1"}, "x"),
    # tail não é dígito → None
    ({"id": "abc_xyz"}, None),
])
def test_resolve_poll_id(snapshot: Dict, expected: Any) -> None:
    """Extrai poll_id do snapshot com múltiplas estratégias de fallback."""
    assert rs.resolve_poll_id(snapshot) == expected


def test_resolve_poll_id_id_sem_separador(snapshot=None) -> None:
    """id sem underline retorna None."""
    assert rs.resolve_poll_id({"id": "semunderline"}) is None


# ---------------------------------------------------------------------------
# resolve_chat_id
# ---------------------------------------------------------------------------

def test_resolve_chat_id_campo_direto() -> None:
    """chat_id explícito no snapshot tem prioridade máxima."""
    snap = {"chat_id": "120363000111@g.us"}
    assert rs.resolve_chat_id(snap, {}) == "120363000111@g.us"


def test_resolve_chat_id_camelcase() -> None:
    """chatId camelCase é aceito como fallback de chat_id."""
    snap = {"chatId": "120363000222@g.us"}
    assert rs.resolve_chat_id(snap, {}) == "120363000222@g.us"


def test_resolve_chat_id_via_cache() -> None:
    """Sem chat_id direto, busca pelo poll_id no cache."""
    snap = {"poll_id": "pollX"}
    cache = {"pollX": "120363000333@g.us"}
    assert rs.resolve_chat_id(snap, cache) == "120363000333@g.us"


def test_resolve_chat_id_via_id_composto_e_cache() -> None:
    """id composto → deriva poll_id → busca no cache."""
    snap = {"id": "pollY_3"}
    cache = {"pollY": "120363000444@g.us"}
    assert rs.resolve_chat_id(snap, cache) == "120363000444@g.us"


def test_resolve_chat_id_cache_miss_retorna_none() -> None:
    """poll_id resolvido mas ausente no cache → None."""
    snap = {"poll_id": "pollZ"}
    assert rs.resolve_chat_id(snap, {}) is None


def test_resolve_chat_id_sem_poll_id_retorna_none() -> None:
    """Snapshot vazio sem nenhuma informação → None."""
    assert rs.resolve_chat_id({}, {}) is None


# ---------------------------------------------------------------------------
# resolve_target_phone
# ---------------------------------------------------------------------------

def test_resolve_target_phone_test_mode_forca_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST_MODE com safe_phone configurado sempre retorna safe_phone + forced_test_mode."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", True)
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", "62999999999")
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    phone, reason = rs.resolve_target_phone(
        chat_id="qualquer",
        vote_phone="62988887777",
        test_phone="62911111111",
        test_group_chat_id="grupo@g.us",
    )
    assert phone == "62999999999"
    assert reason == "forced_test_mode"


def test_resolve_target_phone_test_mode_sem_safe_cai_para_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST_MODE sem safe_phone → avança lógica normal → member quando chat_id == test_group."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", True)
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    phone, reason = rs.resolve_target_phone(
        chat_id="grupo@g.us",
        vote_phone="62977776666",
        test_phone="",
        test_group_chat_id="grupo@g.us",
    )
    assert phone == "62977776666"
    assert reason == "member"


def test_resolve_target_phone_chat_id_igual_test_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id igual ao test_group → retorna member_phone."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    phone, reason = rs.resolve_target_phone(
        chat_id="grupo@g.us",
        vote_phone="62977776666",
        test_phone="",
        test_group_chat_id="grupo@g.us",
    )
    assert phone == "62977776666"
    assert reason == "member"


def test_resolve_target_phone_fallback_test_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id diferente do test_group → retorna safe/test_phone."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    phone, reason = rs.resolve_target_phone(
        chat_id="outro@g.us",
        vote_phone="62977776666",
        test_phone="62911111111",
        test_group_chat_id="grupo@g.us",
    )
    assert phone == "62911111111"
    assert reason == "test"


def test_resolve_target_phone_sem_safe_retorna_vazio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem nenhum safe_phone configurado → string vazia + test_missing."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    phone, reason = rs.resolve_target_phone(
        chat_id="outro@g.us",
        vote_phone="62977776666",
        test_phone="",
        test_group_chat_id="grupo@g.us",
    )
    assert phone == ""
    assert reason == "test_missing"


def test_resolve_target_phone_vote_phone_vazio_nao_e_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """vote_phone vazio com chat_id igual ao grupo não vira member (phone vazio)."""
    monkeypatch.setattr(rs.settings, "TEST_MODE", False)
    monkeypatch.setattr(rs.settings, "TEST_PHONE_NUMBER", None)
    monkeypatch.setattr(rs.settings, "ESTOQUE_PHONE_NUMBER", "")
    phone, reason = rs.resolve_target_phone(
        chat_id="grupo@g.us",
        vote_phone="",
        test_phone="",
        test_group_chat_id="grupo@g.us",
    )
    # member_phone vazio → cai para safe_phone vazio → test_missing
    assert phone == ""
    assert reason == "test_missing"


# ---------------------------------------------------------------------------
# load_poll_chat_map
# ---------------------------------------------------------------------------

def test_load_poll_chat_map_delega_para_domain() -> None:
    """Deve retornar o mapa retornado por load_domain_poll_chat_map."""
    fake_map = {"poll1": "chat1@g.us", "poll2": "chat2@g.us"}
    with patch("app.services.routing_service.load_domain_poll_chat_map", return_value=fake_map):
        result = rs.load_poll_chat_map()
    assert result == fake_map


def test_load_poll_chat_map_retorna_dict_vazio_quando_domain_vazio() -> None:
    """Mapea vazio quando domain_lookup não retorna registros."""
    with patch("app.services.routing_service.load_domain_poll_chat_map", return_value={}):
        result = rs.load_poll_chat_map()
    assert result == {}


# ---------------------------------------------------------------------------
# backfill_metrics_routing
# ---------------------------------------------------------------------------

def _make_data(sections: Dict[str, list]) -> Dict:
    """Constrói estrutura data com votos.packages."""
    return {"votos": {"packages": sections}}


def test_backfill_sem_packages_retorna_zeros() -> None:
    """Data sem pacotes → todos os contadores zerados."""
    data: Dict = {}
    result = rs.backfill_metrics_routing(data, {})
    assert result == {"updated": 0, "unchanged": 0, "failed": 0}


def test_backfill_atualiza_poll_id_a_partir_de_id() -> None:
    """Snapshot com id composto mas sem poll_id → preenche poll_id."""
    snap = {"id": "pollA_0"}
    data = _make_data({"open": [snap]})
    result = rs.backfill_metrics_routing(data, {"pollA": "chatA@g.us"})
    assert snap["poll_id"] == "pollA"
    assert snap["chat_id"] == "chatA@g.us"
    assert result["updated"] == 1
    assert result["failed"] == 0


def test_backfill_nao_altera_snapshot_ja_preenchido() -> None:
    """Snapshot já com poll_id e chat_id corretos → unchanged."""
    snap = {"id": "pollB_1", "poll_id": "pollB", "chat_id": "chatB@g.us", "chatId": "chatB@g.us"}
    data = _make_data({"open": [snap]})
    result = rs.backfill_metrics_routing(data, {"pollB": "chatB@g.us"})
    assert result["unchanged"] == 1
    assert result["updated"] == 0


def test_backfill_falha_quando_sem_chat_id_resolvivel() -> None:
    """Snapshot sem chat_id e poll_id não presente no cache → failed."""
    snap = {"id": "pollC_0"}
    data = _make_data({"open": [snap]})
    result = rs.backfill_metrics_routing(data, {})
    assert result["failed"] == 1


def test_backfill_item_nao_dict_conta_como_failed() -> None:
    """Item que não é dict (ex: None, str) é contabilizado como failed."""
    data = _make_data({"open": [None, "invalido"]})
    result = rs.backfill_metrics_routing(data, {})
    assert result["failed"] == 2


def test_backfill_section_nao_lista_e_ignorada() -> None:
    """Section com valor que não é lista é ignorada sem erro."""
    data = _make_data({"open": "nao-e-lista"})
    result = rs.backfill_metrics_routing(data, {})
    assert result == {"updated": 0, "unchanged": 0, "failed": 0}


def test_backfill_multiplas_sections() -> None:
    """Testa todas as quatro sections de uma vez."""
    cache = {
        "p1": "c1@g.us",
        "p2": "c2@g.us",
        "p3": "c3@g.us",
        "p4": "c4@g.us",
    }
    data = _make_data({
        "open": [{"id": "p1_0"}],
        "closed_today": [{"id": "p2_0"}],
        "closed_week": [{"id": "p3_0"}],
        "confirmed_today": [{"id": "p4_0"}],
    })
    result = rs.backfill_metrics_routing(data, cache)
    assert result["updated"] == 4
    assert result["failed"] == 0


def test_backfill_atualiza_caminho_camelcase() -> None:
    """chatId sem chat_id → resolve_chat_id retorna chatId existente e preenche chat_id snake_case."""
    # resolve_chat_id usa chatId antes de recorrer ao cache, portanto "velho" é retornado.
    snap = {"id": "pollD_0", "chatId": "velho@g.us"}
    data = _make_data({"open": [snap]})
    rs.backfill_metrics_routing(data, {"pollD": "outro@g.us"})
    # chat_id snake_case deve ser preenchido com o valor vindo de chatId
    assert snap["chat_id"] == "velho@g.us"
    # chatId já estava correto → não muda
    assert snap["chatId"] == "velho@g.us"


def test_backfill_section_none_e_ignorada() -> None:
    """Section com None (via `or []`) não causa erro."""
    data = _make_data({"open": None})
    result = rs.backfill_metrics_routing(data, {})
    assert result == {"updated": 0, "unchanged": 0, "failed": 0}


def test_backfill_inicializa_votos_se_ausente() -> None:
    """data sem chave 'votos' é criada via setdefault."""
    data: Dict = {}
    rs.backfill_metrics_routing(data, {})
    assert "votos" in data
    assert "packages" in data["votos"]


def test_backfill_poll_id_ja_preenchido_mas_chat_ausente() -> None:
    """poll_id já correto mas chat_id ausente → updated com chat_id preenchido."""
    snap = {"poll_id": "pollE"}
    data = _make_data({"open": [snap]})
    result = rs.backfill_metrics_routing(data, {"pollE": "chatE@g.us"})
    assert snap["chat_id"] == "chatE@g.us"
    assert result["updated"] == 1
