"""Testes dos utilitários puros de app/services/whatsapp_domain_service.

São funções "_*" privadas mas críticas pra ingestão. Testar separado
deixa cada caso isolado sem precisar montar payload de webhook inteiro.
"""
from datetime import datetime, timezone

import pytest

from app.services import whatsapp_domain_service as wds


# ── _digits ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("5511999999999", "5511999999999"),
    ("+55 (11) 99999-9999", "5511999999999"),
    (None, ""),
    ("", ""),
    ("abc", ""),
    (5511999999999, "5511999999999"),
    ("  55 11 999  ", "5511999"),
])
def test_digits_extracts_only_numeric(raw, expected):
    assert wds._digits(raw) == expected


# ── _is_lid_or_invalid_phone ───────────────────────────────────────────────
@pytest.mark.parametrize("raw,is_invalid", [
    ("5511999999999", False),         # BR celular 11 dígitos
    ("5511888888888", False),         # BR celular 11 dígitos
    ("551199999999", False),          # BR fixo 10 dígitos
    ("11999999999", True),            # sem DDI 55
    ("5599999999", True),             # 5 dígitos depois do 55: inválido
    ("123456@lid", True),             # LID literal
    ("123@LID", True),                # @LID maiúsculo
    ("", True),
    (None, True),
    ("abc", True),                    # sem dígitos
])
def test_is_lid_or_invalid_phone(raw, is_invalid):
    assert wds._is_lid_or_invalid_phone(raw) == is_invalid


# ── _qty / _qty_from_text ──────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    (3, 3), (6, 6), (9, 9), (12, 12), (0, 0),
    (4, 0),    # não pertence ao conjunto permitido
    (15, 0),   # idem
    ("6", 6),
    ("6.0", 6),
    ("", 0),
    (None, 0),
    ("abc", 0),
])
def test_qty_filters_to_allowed_set(raw, expected):
    assert wds._qty(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("6 peças", 6),
    ("9 unidades", 9),
    ("Quero 12 itens", 12),
    ("3", 3),
    ("não tem número", 0),
    ("100 itens", 0),  # 100 não está em ALLOWED_QTY
    ("", 0),
])
def test_qty_from_text(raw, expected):
    assert wds._qty_from_text(raw) == expected


# ── _sanitize_name ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Ana Silva", "Ana Silva"),
    ("Ana   Silva", "Ana Silva"),         # colapsa espaços múltiplos
    ("Ana\nSilva", "Ana Silva"),          # \n → espaço
    ("Ana\tSilva", "Ana Silva"),          # \t → espaço
    ("Ana \r\n Silva ", "Ana Silva"),     # \r\n + trim
    ("   ", "Cliente"),                   # vazio após trim → fallback
    (None, "Cliente"),
    ("", "Cliente"),
])
def test_sanitize_name(raw, expected):
    assert wds._sanitize_name(raw) == expected


def test_sanitize_name_custom_fallback():
    assert wds._sanitize_name(None, fallback="Anônimo") == "Anônimo"


# ── _event_key ─────────────────────────────────────────────────────────────
def test_event_key_basic():
    assert wds._event_key("whapi", "abc123", "vote") == "whapi:abc123:vote"


def test_event_key_with_suffix():
    assert wds._event_key("whapi", "abc123", "vote", suffix="opt-9") == "whapi:abc123:vote:opt-9"


# ── _safe_datetime ─────────────────────────────────────────────────────────
def test_safe_datetime_passthrough_aware():
    dt = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    assert wds._safe_datetime(dt) == dt


def test_safe_datetime_assigns_utc_to_naive():
    dt = datetime(2026, 5, 11, 12, 0)
    result = wds._safe_datetime(dt)
    assert result.tzinfo == timezone.utc
    assert result.year == 2026


def test_safe_datetime_parses_iso_string():
    result = wds._safe_datetime("2026-05-11T12:00:00Z")
    assert result == datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def test_safe_datetime_parses_iso_with_offset():
    result = wds._safe_datetime("2026-05-11T12:00:00-03:00")
    assert result == datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def test_safe_datetime_parses_unix_seconds():
    result = wds._safe_datetime("1747000000")  # algum unix ts
    assert result.tzinfo == timezone.utc


def test_safe_datetime_parses_unix_milliseconds():
    """Valores > 10^10 são tratados como milissegundos."""
    ms = 1747000000000
    result = wds._safe_datetime(str(ms))
    assert result.tzinfo == timezone.utc
    assert result.year >= 2025


def test_safe_datetime_none_returns_now():
    before = wds._utc_now()
    result = wds._safe_datetime(None)
    assert (result - before).total_seconds() < 5


def test_safe_datetime_garbage_returns_now():
    before = wds._utc_now()
    result = wds._safe_datetime("not-a-date")
    assert (result - before).total_seconds() < 5


# ── _normalize_chat_id ─────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("123@g.us", "123@g.us"),
    ("  123@g.us  ", "123@g.us"),
    ("", ""),
    (None, ""),
    (12345, "12345"),
])
def test_normalize_chat_id(raw, expected):
    assert wds._normalize_chat_id(raw) == expected


# ── _unwrap ────────────────────────────────────────────────────────────────
def test_unwrap_returns_body_when_present():
    payload = {"body": {"inner": True}, "outer": "ignored"}
    assert wds._unwrap(payload) == {"inner": True}


def test_unwrap_returns_payload_when_no_body():
    payload = {"inner": True}
    assert wds._unwrap(payload) == {"inner": True}


def test_unwrap_keeps_payload_when_body_is_not_dict():
    payload = {"body": "string", "x": 1}
    assert wds._unwrap(payload) == {"body": "string", "x": 1}


# ── _normalize_options ─────────────────────────────────────────────────────
def test_normalize_options_dict_with_qty_in_label():
    options = [
        {"id": "opt-1", "name": "3 unidades"},
        {"id": "opt-2", "name": "6 itens"},
        {"id": "opt-3", "name": "Não quero"},  # sem qty válida → ignorado
        {"id": "opt-4", "name": "12"},
    ]
    result = wds._normalize_options(options)
    assert [o["qty"] for o in result] == [3, 6, 12]
    assert result[0]["option_external_id"] == "opt-1"
    assert result[0]["position"] == 0
    assert result[2]["position"] == 3  # posição original mantida


def test_normalize_options_falls_back_to_optionId():
    options = [{"optionId": "alt-9", "optionName": "9 peças"}]
    result = wds._normalize_options(options)
    assert result == [
        {"option_external_id": "alt-9", "label": "9 peças", "qty": 9, "position": 0}
    ]


def test_normalize_options_string_inputs():
    options = ["3 peças", "6", "100 (inválido)"]
    result = wds._normalize_options(options)
    assert [o["qty"] for o in result] == [3, 6]


def test_normalize_options_not_a_list_returns_empty():
    assert wds._normalize_options(None) == []
    assert wds._normalize_options({}) == []
    assert wds._normalize_options("string") == []
