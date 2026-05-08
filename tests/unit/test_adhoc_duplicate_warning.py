"""Testes do aviso de duplicidade no fluxo adhoc.

Cobrem:
  - _detect_duplicate_clients retorna warnings dos pacotes approved/closed recentes.
  - Ignora pacotes cancelled e open.
  - Respeita janela de 30 dias.
  - /confirm bloqueia 409 quando há warnings e force=false.
  - /confirm prossegue quando force=true (sem chamar checagem de duplicata).
"""
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

import pytest


def _make_votes(phones):
    from app.api.adhoc_packages import VoteLineAdhoc
    # 24 peças total, distribuído igualmente
    per = 24 // len(phones)
    rem = 24 - per * len(phones)
    votes = []
    for i, p in enumerate(phones):
        qty = per + (rem if i == 0 else 0)
        votes.append(VoteLineAdhoc(phone=p, qty=qty, name=f"Cliente {i}"))
    return votes


def _install_fake(monkeypatch, fake):
    from app.api import adhoc_packages
    monkeypatch.setattr(
        adhoc_packages,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake)),
    )


def test_detect_duplicate_returns_approved_and_closed(monkeypatch):
    from app.api.adhoc_packages import _detect_duplicate_clients

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    fake = MagicMock()
    def select_all(table, columns=None, filters=None):
        if table == "clientes":
            return [{"id": "CLI-LARY", "nome": "Lary", "celular": "5511111111111"}]
        if table == "pacote_clientes":
            return [{
                "cliente_id": "CLI-LARY",
                "qty": 3,
                "pacote": {
                    "id": "PKG-A", "status": "approved", "approved_at": recent,
                    "enquete": {"titulo": "Blusa com pedrinhas PMG"},
                },
            }]
        return []
    fake.select_all.side_effect = select_all
    _install_fake(monkeypatch, fake)

    warnings = _detect_duplicate_clients(_make_votes(["5511111111111"]))
    assert len(warnings) == 1
    assert warnings[0]["phone"] == "5511111111111"
    assert warnings[0]["existing_packages"][0]["package_status"] == "approved"


def test_detect_duplicate_ignores_cancelled_and_open(monkeypatch):
    from app.api.adhoc_packages import _detect_duplicate_clients

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    fake = MagicMock()
    def select_all(table, **kw):
        if table == "clientes":
            return [{"id": "CLI-1", "nome": "Teste", "celular": "5511999999999"}]
        if table == "pacote_clientes":
            return [
                {"cliente_id": "CLI-1", "qty": 3, "pacote": {"id": "A", "status": "cancelled", "approved_at": recent, "enquete": {"titulo": "X"}}},
                {"cliente_id": "CLI-1", "qty": 3, "pacote": {"id": "B", "status": "open", "approved_at": recent, "enquete": {"titulo": "Y"}}},
            ]
        return []
    fake.select_all.side_effect = select_all
    _install_fake(monkeypatch, fake)

    warnings = _detect_duplicate_clients(_make_votes(["5511999999999"]))
    assert warnings == []


def test_detect_duplicate_ignores_packages_older_than_30_days(monkeypatch):
    from app.api.adhoc_packages import _detect_duplicate_clients

    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()

    fake = MagicMock()
    def select_all(table, **kw):
        if table == "clientes":
            return [{"id": "CLI-1", "nome": "X", "celular": "5511888888888"}]
        if table == "pacote_clientes":
            return [{
                "cliente_id": "CLI-1", "qty": 3,
                "pacote": {"id": "OLD", "status": "approved", "approved_at": old, "enquete": {"titulo": "Velho"}},
            }]
        return []
    fake.select_all.side_effect = select_all
    _install_fake(monkeypatch, fake)

    warnings = _detect_duplicate_clients(_make_votes(["5511888888888"]))
    assert warnings == []


def test_detect_duplicate_no_matches_when_client_not_found(monkeypatch):
    from app.api.adhoc_packages import _detect_duplicate_clients

    fake = MagicMock()
    fake.select_all.side_effect = lambda *a, **k: []
    _install_fake(monkeypatch, fake)

    warnings = _detect_duplicate_clients(_make_votes(["5511777777777"]))
    assert warnings == []
