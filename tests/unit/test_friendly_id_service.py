"""Testes do friendly_id_service usando SQLite real em memória.

A RPC `assign_pacote_friendly_id` é mockada em SQLite via
`_rpc_assign_pacote_friendly_id` no sqlite_service. Aqui validamos:
1. Numeração sequencial dentro do dia.
2. Idempotência (chamar 2x não muda o ID).
3. Reset por dia (passando `when` diferente).
4. Resposta None pra pacote inexistente.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.sqlite_service import SQLiteRestClient
from app.services.friendly_id_service import assign_friendly_id


SCHEMA_PATH = str(Path(__file__).resolve().parent.parent.parent / "deploy" / "sqlite" / "schema.sql")
_TZ_SP = timezone(timedelta(hours=-3))


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA journal_mode=MEMORY")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return SQLiteRestClient(db_path=db_file, schema_path=SCHEMA_PATH)


def _make_pacote(db: SQLiteRestClient, enquete_id: str, seq: int) -> Dict[str, Any]:
    rows = db.insert(
        "pacotes",
        {
            "enquete_id": enquete_id,
            "sequence_no": seq,
            "capacidade_total": 24,
            "total_qty": 24,
            "participants_count": 1,
            "status": "closed",
        },
    )
    return rows[0]


def _make_enquete(db: SQLiteRestClient) -> str:
    prod = db.insert("produtos", {"nome": "p", "valor_unitario": 10})[0]
    enq = db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": prod["id"],
        "titulo": "t",
        "status": "open",
    })[0]
    return enq["id"]


def test_sequencial_no_mesmo_dia(db):
    enq = _make_enquete(db)
    when = datetime(2026, 5, 18, 14, 0, tzinfo=_TZ_SP)
    ids = []
    for i in range(1, 4):
        p = _make_pacote(db, enq, i)
        ids.append(assign_friendly_id(db, p["id"], when=when))
    assert ids == ["PAC001/1805", "PAC002/1805", "PAC003/1805"]


def test_idempotente(db):
    enq = _make_enquete(db)
    when = datetime(2026, 5, 18, 14, 0, tzinfo=_TZ_SP)
    p = _make_pacote(db, enq, 1)
    a = assign_friendly_id(db, p["id"], when=when)
    b = assign_friendly_id(db, p["id"], when=when)
    assert a == b == "PAC001/1805"


def test_reset_por_dia(db):
    enq = _make_enquete(db)
    dia1 = datetime(2026, 5, 18, 14, 0, tzinfo=_TZ_SP)
    dia2 = datetime(2026, 5, 19, 9, 0, tzinfo=_TZ_SP)
    p1 = _make_pacote(db, enq, 1)
    p2 = _make_pacote(db, enq, 2)
    p3 = _make_pacote(db, enq, 3)
    assert assign_friendly_id(db, p1["id"], when=dia1) == "PAC001/1805"
    assert assign_friendly_id(db, p2["id"], when=dia1) == "PAC002/1805"
    assert assign_friendly_id(db, p3["id"], when=dia2) == "PAC001/1905"


def test_pacote_inexistente_retorna_none(db):
    when = datetime(2026, 5, 18, 14, 0, tzinfo=_TZ_SP)
    assert assign_friendly_id(db, "nao-existe", when=when) is None


def test_dia_padrao_usa_now_em_sp(db, monkeypatch):
    """Quando `when` é omitido, usa now() em America/Sao_Paulo."""
    enq = _make_enquete(db)
    p = _make_pacote(db, enq, 1)

    fake_now = datetime(2026, 12, 31, 23, 59, tzinfo=_TZ_SP)

    import app.services.friendly_id_service as mod
    real_dt = mod.datetime

    class _FakeDT:
        @classmethod
        def now(cls, tz=None):
            return fake_now.astimezone(tz) if tz else fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDT)
    try:
        result = assign_friendly_id(db, p["id"])
    finally:
        monkeypatch.setattr(mod, "datetime", real_dt)

    assert result == "PAC001/3112"
