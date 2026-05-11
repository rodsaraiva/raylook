"""Testes de normalize_webhook_events — função pura que recebe payload
WHAPI/Evolution e retorna uma lista normalizada de WebhookEvent."""
from datetime import datetime, timezone

from app.services import whatsapp_domain_service as wds


def _whapi_poll_msg(msg_id="poll-1", chat_id="120@g.us", title="3, 6, 9, 12",
                    options=None, ts=1747000000):
    return {
        "id": msg_id, "type": "poll", "chat_id": chat_id,
        "timestamp": ts,
        "poll": {
            "title": title,
            "options": options or [
                {"id": "o1", "name": "3 peças"},
                {"id": "o2", "name": "6 peças"},
                {"id": "o3", "name": "9 peças"},
                {"id": "o4", "name": "12 peças"},
            ],
        },
    }


def _whapi_vote_update(poll_id="poll-1", voter="5511999999999", option_id="o2",
                      qty_text="6 peças", chat_id="120@g.us", ts=1747000060,
                      trigger_id="trig-1", from_name="Ana"):
    return {
        "id": poll_id,
        "event_id": "evt-1",
        "timestamp": ts,
        "trigger": {
            "id": trigger_id,
            "chat_id": chat_id,
            "from": voter,
            "from_name": from_name,
            "action": {"type": "vote", "votes": [option_id]},
        },
        "after_update": {
            "poll": {
                "results": [{"id": option_id, "name": qty_text}],
            }
        },
    }


# ── WHAPI poll_created ─────────────────────────────────────────────────────
def test_normalize_whapi_poll_created():
    payload = {"messages": [_whapi_poll_msg()]}
    events = wds.normalize_webhook_events(payload)
    assert len(events) == 1
    e = events[0]
    assert e.kind == "poll_created"
    assert e.provider == "whapi"
    assert e.external_poll_id == "poll-1"
    assert e.chat_id == "120@g.us"
    assert e.title == "3, 6, 9, 12"
    # opções normalizadas → 4 com qty 3/6/9/12
    assert [o["qty"] for o in e.options] == [3, 6, 9, 12]


def test_normalize_skips_message_without_id():
    payload = {"messages": [{"id": "", "type": "poll", "poll": {}}]}
    assert wds.normalize_webhook_events(payload) == []


def test_normalize_skips_non_poll_text_message():
    payload = {"messages": [{"id": "x", "type": "text", "text": {"body": "oi"}}]}
    assert wds.normalize_webhook_events(payload) == []


# ── WHAPI vote_updated ─────────────────────────────────────────────────────
def test_normalize_whapi_vote_updated():
    payload = {"messages_updates": [_whapi_vote_update()]}
    events = wds.normalize_webhook_events(payload)
    assert len(events) == 1
    e = events[0]
    assert e.kind == "vote_updated"
    assert e.voter_phone == "5511999999999"
    assert e.voter_name == "Ana"
    assert e.option_external_id == "o2"
    assert e.option_label == "6 peças"
    assert e.qty == 6


def test_normalize_vote_skips_lid_voter():
    """LID @lid → criaria cliente fantasma (incidente 2026-04-18)."""
    upd = _whapi_vote_update(voter="123456@lid")
    payload = {"messages_updates": [upd]}
    assert wds.normalize_webhook_events(payload) == []


def test_normalize_vote_skips_non_br_phone():
    """Phone sem prefixo 55 não casa com BR_PHONE_RE."""
    upd = _whapi_vote_update(voter="11999999999")  # sem 55
    payload = {"messages_updates": [upd]}
    assert wds.normalize_webhook_events(payload) == []


def test_normalize_vote_qty_zero_when_label_has_no_qty():
    """Voto em opção com label sem qty válida → qty=0."""
    upd = _whapi_vote_update(qty_text="Não quero")
    payload = {"messages_updates": [upd]}
    events = wds.normalize_webhook_events(payload)
    assert len(events) == 1
    assert events[0].qty == 0


# ── allowed_chat_ids ───────────────────────────────────────────────────────
def test_normalize_filters_chat_ids_when_allowlist_set():
    payload = {
        "messages": [
            _whapi_poll_msg(msg_id="ok", chat_id="OFFICIAL"),
            _whapi_poll_msg(msg_id="filtered", chat_id="OTHER"),
        ]
    }
    events = wds.normalize_webhook_events(payload, allowed_chat_ids={"OFFICIAL"})
    assert [e.external_poll_id for e in events] == ["ok"]


def test_normalize_empty_allowlist_lets_all_through():
    payload = {"messages": [_whapi_poll_msg(chat_id="X")]}
    events = wds.normalize_webhook_events(payload, allowed_chat_ids=set())
    assert len(events) == 1


# ── ordenação por timestamp ────────────────────────────────────────────────
def test_normalize_sorts_events_by_occurred_at():
    payload = {
        "messages": [_whapi_poll_msg(msg_id="p1", ts=1747000000)],
        "messages_updates": [
            _whapi_vote_update(ts=1746000000, trigger_id="early"),
            _whapi_vote_update(ts=1748000000, trigger_id="late"),
        ],
    }
    events = wds.normalize_webhook_events(payload)
    timestamps = [e.occurred_at for e in events]
    assert timestamps == sorted(timestamps)


# ── body wrapper ───────────────────────────────────────────────────────────
def test_normalize_unwraps_body_envelope():
    """Alguns gateways embrulham o payload em {"body": {...}}."""
    inner = {"messages": [_whapi_poll_msg()]}
    payload = {"body": inner}
    events = wds.normalize_webhook_events(payload)
    assert len(events) == 1


# ── Evolution: pollCreationMessage ────────────────────────────────────────
def test_normalize_evolution_poll_created():
    payload = {
        "data": {
            "key": {"id": "evo-poll-1", "remoteJid": "120@g.us"},
            "message": {
                "pollCreationMessage": {
                    "name": "Camiseta · M/G",
                    "options": [
                        {"optionName": "3 peças"},
                        {"optionName": "6 peças"},
                    ],
                }
            },
            "messageTimestamp": 1747000000,
        }
    }
    events = wds.normalize_webhook_events(payload)
    assert len(events) == 1
    e = events[0]
    assert e.kind == "poll_created"
    assert e.provider == "evolution"
    assert e.external_poll_id == "evo-poll-1"
    assert e.title == "Camiseta · M/G"
    assert [o["qty"] for o in e.options] == [3, 6]


def test_normalize_evolution_poll_update_skips_lid_participant():
    payload = {
        "data": {
            "key": {"id": "x", "remoteJid": "120@g.us", "participant": "123@lid"},
            "message": {
                "pollUpdateMessage": {
                    "pollCreationMessageKey": {"id": "p-1"},
                    "vote": {"selectedOptions": [{"name": "6"}]},
                }
            },
            "pushName": "Ana",
        }
    }
    events = wds.normalize_webhook_events(payload)
    assert events == []
