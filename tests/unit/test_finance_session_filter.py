"""Filtro de sessão Bernardo nos builders de finance."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
from app.services import finance_service
from app.services.finance_service import (
    build_receivables_by_client,
    build_aging_summary,
    build_paid_by_client,
    build_paid_summary,
)

NOW = "2026-05-12T00:00:00+00:00"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    f.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511999990001"},
        {"id": "c2", "nome": "Bia", "celular": "5511999990002"},
    ])
    # v1 → enquete Bernardo; v2 → enquete comum
    f.tables["vendas"].extend([
        {"id": "v1", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 400.0,
         "commission_amount": 40.0},
        {"id": "v2", "cliente_id": "c2", "pacote_id": "p2", "total_amount": 600.0,
         "commission_amount": 60.0},
    ])
    f.tables["pacotes"].extend([
        {"id": "p1", "enquete_id": "e1", "sequence_no": 1},
        {"id": "p2", "enquete_id": "e2", "sequence_no": 2},
    ])
    f.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Pacote Bernardo 24"},
        {"id": "e2", "titulo": "Coleção Verão"},
    ])
    return f


def _seed_pending(f):
    f.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent", "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "sent", "created_at": "2026-05-01T10:00:00+00:00"},
    ])


def _seed_paid(f):
    f.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "paid",
         "created_at": "2026-05-01T10:00:00+00:00", "paid_at": "2026-05-02T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "paid",
         "created_at": "2026-05-01T10:00:00+00:00", "paid_at": "2026-05-02T10:00:00+00:00"},
    ])


def test_receivables_none_returns_all(fake):
    _seed_pending(fake)
    rows = build_receivables_by_client(now_iso=NOW, session=None)
    assert {r["cliente_id"] for r in rows} == {"c1", "c2"}


def test_receivables_bernardo_filters_to_bernardo(fake):
    _seed_pending(fake)
    rows = build_receivables_by_client(now_iso=NOW, session="Bernardo")
    assert {r["cliente_id"] for r in rows} == {"c1"}


def test_aging_summary_bernardo_only_counts_bernardo(fake):
    _seed_pending(fake)
    full = build_aging_summary(now_iso=NOW, session=None)
    bern = build_aging_summary(now_iso=NOW, session="Bernardo")
    assert full["total_receivable"] == 1000.0 and full["count"] == 2
    assert bern["total_receivable"] == 400.0 and bern["count"] == 1
    assert bern["clients_count"] == 1


def test_paid_by_client_bernardo_filters(fake):
    _seed_paid(fake)
    rows = build_paid_by_client(now_iso=NOW, session="Bernardo")
    assert {r["cliente_id"] for r in rows} == {"c1"}


def test_paid_summary_bernardo_only_counts_bernardo(fake):
    _seed_paid(fake)
    full = build_paid_summary(now_iso=NOW, session=None)
    bern = build_paid_summary(now_iso=NOW, session="Bernardo")
    assert full["total_paid"] == 1000.0 and full["count"] == 2
    assert bern["total_paid"] == 400.0 and bern["count"] == 1
