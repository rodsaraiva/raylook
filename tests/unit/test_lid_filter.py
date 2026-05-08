"""Testes do filtro de LIDs do WhatsApp.

LIDs (Linked Identifiers) chegam em webhooks/voters do WHAPI quando o
participante tem privacidade alta no grupo. Se forem processados como phones,
criam clientes fantasmas no banco (incidente 2026-04-18: 42 clientes
'Cliente' com celulares de 14-15 dígitos criados via poll_reconcile).
"""
from unittest.mock import MagicMock

import pytest


def test_is_lid_explicit_suffix():
    from app.services.whatsapp_domain_service import _is_lid_or_invalid_phone
    assert _is_lid_or_invalid_phone("53068834074801@lid") is True
    assert _is_lid_or_invalid_phone("215521593139288@lid") is True


def test_is_lid_invalid_digit_format():
    from app.services.whatsapp_domain_service import _is_lid_or_invalid_phone
    # Sem sufixo @lid mas formato inválido (muito longo) — também bloqueado
    assert _is_lid_or_invalid_phone("53068834074801") is True
    assert _is_lid_or_invalid_phone("215521593139288") is True
    # Sem dígitos
    assert _is_lid_or_invalid_phone("") is True
    assert _is_lid_or_invalid_phone(None) is True
    assert _is_lid_or_invalid_phone("@lid") is True
    # Phone US (não BR)
    assert _is_lid_or_invalid_phone("12127363100") is True


def test_is_lid_accepts_valid_br_phones():
    from app.services.whatsapp_domain_service import _is_lid_or_invalid_phone
    # 55 + DDD + 8 ou 9 dígitos = 12 ou 13 chars
    assert _is_lid_or_invalid_phone("558496472233") is False  # 12 dígitos
    assert _is_lid_or_invalid_phone("5584964722333") is False  # 13 dígitos (com 9º)
    # Formato com @s.whatsapp.net também passa (dígitos casam)
    assert _is_lid_or_invalid_phone("558496472233@s.whatsapp.net") is False


def test_poll_reconcile_diff_skips_lids(monkeypatch):
    """_diff_votes do reconciler deve ignorar voters com @lid."""
    from app.services import poll_reconcile_service as prs

    monkeypatch.setattr(prs, "SupabaseRestClient", MagicMock(from_settings=MagicMock(return_value=MagicMock())))
    monkeypatch.setattr(prs, "WHAPIClient", MagicMock(side_effect=Exception("no whapi")))
    svc = prs.PollReconcileService()
    db_votes = []  # nada no banco
    whapi_state = {
        "id": "POLL-1",
        "results": [
            {"name": "03", "voters": [
                "558496472233",           # phone válido
                "53068834074801@lid",     # LID — IGNORAR
                "215521593139288@lid",    # LID — IGNORAR
                "556392914040",           # phone válido
                "33402011017264",         # inválido (não bate BR) — IGNORAR
            ]},
        ],
    }
    missing, extra = svc._diff(db_votes, whapi_state)
    phones = {m["phone"] for m in missing}
    assert phones == {"558496472233", "556392914040"}


def test_whatsapp_webhook_skips_lid_trigger(monkeypatch):
    """normalize_webhook_events do WHAPI deve ignorar quando trigger.from é LID."""
    from app.services.whatsapp_domain_service import normalize_webhook_events

    payload = {
        "messages_updates": [{
            "id": "POLL-1",
            "timestamp": 1776519482,
            "trigger": {
                "action": {"type": "vote", "votes": ["opt-hash"], "target": "POLL-1"},
                "from": "53068834074801@lid",   # LID — deve ignorar evento
                "chat_id": "120363295598413696@g.us",
                "id": "trigger-1",
            },
            "after_update": {"poll": {"results": [{"id": "opt-hash", "name": "03"}]}},
        }],
    }
    events = normalize_webhook_events(payload)
    assert events == []


def test_whatsapp_webhook_accepts_valid_phone(monkeypatch):
    from app.services.whatsapp_domain_service import normalize_webhook_events

    payload = {
        "messages_updates": [{
            "id": "POLL-1",
            "timestamp": 1776519482,
            "trigger": {
                "action": {"type": "vote", "votes": ["opt-hash"], "target": "POLL-1"},
                "from": "558496472233",
                "from_name": "Maria",
                "chat_id": "120363295598413696@g.us",
                "id": "trigger-2",
            },
            "after_update": {"poll": {"results": [{"id": "opt-hash", "name": "03"}]}},
        }],
    }
    events = normalize_webhook_events(payload)
    assert len(events) == 1
    assert events[0].voter_phone == "558496472233"
