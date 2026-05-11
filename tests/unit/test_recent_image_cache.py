"""Testes unitários para app/services/recent_image_cache.py.

Estado em memória (ou arquivo/runtime_state). Testes isolados via
monkeypatch de _load_cache / _save_cache quando necessário.
"""
from __future__ import annotations

from app.services import recent_image_cache as svc


# ---------------------------------------------------------------------------
# Helpers de isolamento de estado
# ---------------------------------------------------------------------------

def _make_cache_monkeypatch(monkeypatch, initial: dict | None = None):
    """Cria store em memória e faz patch das funções de I/O."""
    store: dict = dict(initial or {})
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: dict(store))
    monkeypatch.setattr(svc._storage, "save", lambda d: store.update(d) or store.clear() or store.update(d))
    return store


# ---------------------------------------------------------------------------
# Teste original (mantido)
# ---------------------------------------------------------------------------

def test_recent_image_cache_uses_runtime_state_in_supabase_mode(monkeypatch):
    cache = {}

    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: True)
    monkeypatch.setattr(svc, "load_runtime_state", lambda key: dict(cache))
    monkeypatch.setattr(svc, "save_runtime_state", lambda key, value: cache.update(value))
    monkeypatch.setattr(
        svc,
        "_utc_now",
        lambda: svc.datetime(2026, 3, 30, 16, 30, 0, tzinfo=svc.timezone.utc),
    )

    svc.remember_recent_image(
        chat_id="group-1",
        message_id="msg-1",
        media_id="media-1",
        occurred_at="2026-03-30T16:00:00+00:00",
    )

    item = svc.find_recent_image(chat_id="group-1", poll_ts=svc._parse_timestamp("2026-03-30T16:10:00+00:00"))

    assert item is not None
    assert item["media_id"] == "media-1"
    assert "group-1" in cache


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------

def test_parse_timestamp_inteiro_segundos():
    ts = 1_700_000_000
    assert svc._parse_timestamp(ts) == ts


def test_parse_timestamp_inteiro_milissegundos():
    ts_ms = 1_700_000_000_000
    assert svc._parse_timestamp(ts_ms) == ts_ms // 1000


def test_parse_timestamp_float():
    assert svc._parse_timestamp(1_700_000_000.5) == 1_700_000_000


def test_parse_timestamp_string_digitos_segundos():
    assert svc._parse_timestamp("1700000000") == 1_700_000_000


def test_parse_timestamp_string_digitos_milissegundos():
    assert svc._parse_timestamp("1700000000000") == 1_700_000_000


def test_parse_timestamp_iso_com_z():
    result = svc._parse_timestamp("2026-03-30T16:00:00Z")
    assert result is not None and result > 0


def test_parse_timestamp_iso_com_offset():
    result = svc._parse_timestamp("2026-03-30T16:00:00+00:00")
    assert result is not None and result > 0


def test_parse_timestamp_none_retorna_none():
    assert svc._parse_timestamp(None) is None


def test_parse_timestamp_string_invalida_retorna_none():
    assert svc._parse_timestamp("nao_e_data") is None


def test_parse_timestamp_string_vazia_retorna_none():
    assert svc._parse_timestamp("") is None


# ---------------------------------------------------------------------------
# remember_recent_image — modo arquivo local
# ---------------------------------------------------------------------------

def test_remember_recent_image_adiciona_entrada(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: dict(store))

    saved: list[dict] = []
    monkeypatch.setattr(svc._storage, "save", lambda d: saved.append(dict(d)))

    svc.remember_recent_image(
        chat_id="chat-A",
        message_id="msg-1",
        media_id="media-X",
        occurred_at=1_700_000_000,
    )
    assert len(saved) == 1
    entries = saved[0].get("chat-A", [])
    assert entries[0]["media_id"] == "media-X"


def test_remember_recent_image_deduplica_media_id(monkeypatch):
    """Mesmo media_id inserido duas vezes deve aparecer só uma vez."""
    store: dict = {}
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    def fake_load():
        return dict(store)

    def fake_save(d):
        store.clear()
        store.update(d)

    monkeypatch.setattr(svc._storage, "load", fake_load)
    monkeypatch.setattr(svc._storage, "save", fake_save)

    svc.remember_recent_image(chat_id="chat-B", message_id="m1", media_id="dup", occurred_at=1_700_000_000)
    svc.remember_recent_image(chat_id="chat-B", message_id="m2", media_id="dup", occurred_at=1_700_000_001)

    assert len(store["chat-B"]) == 1


def test_remember_recent_image_sem_chat_id_noop(monkeypatch):
    saved: list = []
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: {})
    monkeypatch.setattr(svc._storage, "save", lambda d: saved.append(d))
    svc.remember_recent_image(chat_id="", message_id="m1", media_id="m", occurred_at=1_700_000_000)
    assert saved == []


def test_remember_recent_image_sem_media_id_noop(monkeypatch):
    saved: list = []
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: {})
    monkeypatch.setattr(svc._storage, "save", lambda d: saved.append(d))
    svc.remember_recent_image(chat_id="chat-X", message_id="m1", media_id="", occurred_at=1_700_000_000)
    assert saved == []


def test_remember_recent_image_occurred_at_none_usa_utc_now(monkeypatch):
    """Se occurred_at for None, deve usar _utc_now como timestamp."""
    from datetime import datetime, timezone

    store: dict = {}
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: dict(store))

    fixed_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(svc, "_utc_now", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    saved: list[dict] = []
    monkeypatch.setattr(svc._storage, "save", lambda d: saved.append(dict(d)))

    svc.remember_recent_image(chat_id="c1", message_id="m1", media_id="mid", occurred_at=None)
    entry = saved[0]["c1"][0]
    assert entry["timestamp"] == fixed_ts


def test_remember_recent_image_respeita_max_items(monkeypatch):
    """Cache por chat não deve exceder _MAX_ITEMS_PER_CHAT."""
    store: dict = {}
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    def fake_load():
        return {k: list(v) for k, v in store.items()}

    def fake_save(d):
        store.clear()
        for k, v in d.items():
            store[k] = list(v)

    monkeypatch.setattr(svc._storage, "load", fake_load)
    monkeypatch.setattr(svc._storage, "save", fake_save)

    for i in range(svc._MAX_ITEMS_PER_CHAT + 10):
        svc.remember_recent_image(
            chat_id="big-chat",
            message_id=f"m{i}",
            media_id=f"media-{i}",
            occurred_at=1_700_000_000 + i,
        )

    assert len(store["big-chat"]) == svc._MAX_ITEMS_PER_CHAT


# ---------------------------------------------------------------------------
# find_recent_image — modo arquivo local
# ---------------------------------------------------------------------------

def test_find_recent_image_retorna_mais_recente_antes_do_poll_ts(monkeypatch):
    """Deve retornar o candidato com maior timestamp que ainda <= poll_ts."""
    from datetime import datetime, timezone

    now = datetime(2026, 3, 30, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "_utc_now", lambda: now)
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    ts_base = int(now.timestamp()) - 3600  # 1h atrás, dentro do _MAX_AGE_SECONDS
    cache_data = {
        "chat-Z": [
            {"message_id": "m1", "media_id": "img-old", "timestamp": ts_base},
            {"message_id": "m2", "media_id": "img-new", "timestamp": ts_base + 300},
        ]
    }
    monkeypatch.setattr(svc._storage, "load", lambda: cache_data)

    result = svc.find_recent_image(chat_id="chat-Z", poll_ts=ts_base + 400)
    assert result is not None
    assert result["media_id"] == "img-new"


def test_find_recent_image_chat_id_vazio_retorna_none(monkeypatch):
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: {})
    assert svc.find_recent_image(chat_id="", poll_ts=1_700_000_000) is None


def test_find_recent_image_cache_vazio_retorna_none(monkeypatch):
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(svc._storage, "load", lambda: {})
    assert svc.find_recent_image(chat_id="nenhum", poll_ts=1_700_000_000) is None


def test_find_recent_image_exclui_entradas_antigas(monkeypatch):
    """Imagens mais antigas que _MAX_AGE_SECONDS não devem ser retornadas."""
    from datetime import datetime, timezone

    now = datetime(2026, 3, 30, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "_utc_now", lambda: now)
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    now_ts = int(now.timestamp())
    old_ts = now_ts - svc._MAX_AGE_SECONDS - 60  # além do limite

    cache_data = {
        "chat-old": [
            {"message_id": "m1", "media_id": "velha", "timestamp": old_ts},
        ]
    }
    monkeypatch.setattr(svc._storage, "load", lambda: cache_data)
    result = svc.find_recent_image(chat_id="chat-old", poll_ts=now_ts)
    assert result is None


def test_find_recent_image_exclui_entradas_apos_poll_ts(monkeypatch):
    """Imagens posteriores ao poll_ts não devem ser candidatas."""
    from datetime import datetime, timezone

    now = datetime(2026, 3, 30, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "_utc_now", lambda: now)
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    poll_ts = int(now.timestamp()) - 1800
    future_ts = int(now.timestamp()) - 100  # após poll_ts mas dentro da janela

    cache_data = {
        "chat-fut": [
            {"message_id": "m1", "media_id": "futura", "timestamp": future_ts},
        ]
    }
    monkeypatch.setattr(svc._storage, "load", lambda: cache_data)
    result = svc.find_recent_image(chat_id="chat-fut", poll_ts=poll_ts)
    assert result is None


def test_find_recent_image_entrada_sem_timestamp_ignorada(monkeypatch):
    """Entradas sem timestamp válido devem ser descartadas."""
    from datetime import datetime, timezone

    now = datetime(2026, 3, 30, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "_utc_now", lambda: now)
    monkeypatch.setattr(svc, "runtime_state_enabled", lambda: False)

    cache_data = {
        "chat-sem-ts": [
            {"message_id": "m1", "media_id": "sem_ts", "timestamp": None},
        ]
    }
    monkeypatch.setattr(svc._storage, "load", lambda: cache_data)
    result = svc.find_recent_image(chat_id="chat-sem-ts", poll_ts=int(now.timestamp()))
    assert result is None
