"""Testes de build_receivables_by_client."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
from app.services import finance_service


@pytest.fixture
def fake_setup(monkeypatch):
    fake = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: fake)
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana Silva", "celular": "5511999990001"},
        {"id": "c2", "nome": "Bia Costa", "celular": "5511999990002"},
    ])
    fake.tables["vendas"].extend([
        {"id": "v1", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 400.0},
        {"id": "v2", "cliente_id": "c1", "pacote_id": "p2", "total_amount": 800.0},
        {"id": "v3", "cliente_id": "c2", "pacote_id": "p1", "total_amount": 600.0},
    ])
    fake.tables["pacotes"].extend([
        {"id": "p1", "enquete_id": "e1", "sequence_no": 1},
        {"id": "p2", "enquete_id": "e2", "sequence_no": 2},
    ])
    fake.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Enquete 1"},
        {"id": "e2", "titulo": "Enquete 2"},
    ])
    return fake


def test_groups_pagamentos_by_cliente(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "created",
         "created_at": "2026-04-20T10:00:00+00:00"},
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-05-05T10:00:00+00:00"},
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")

    assert len(rows) == 2
    ana = next(r for r in rows if r["cliente_id"] == "c1")
    assert ana["nome"] == "Ana Silva"
    assert ana["total"] == 1200.0
    assert ana["count"] == 2
    assert ana["oldest_age_days"] == 22  # 2026-05-12 - 2026-04-20
    assert len(ana["charges"]) == 2


def test_excludes_paid_and_written_off(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "paid",
         "created_at": "2026-04-20T10:00:00+00:00"},
        {"id": "pg3", "venda_id": "v3", "status": "written_off",
         "created_at": "2026-03-01T10:00:00+00:00"},
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")

    assert len(rows) == 1
    assert rows[0]["cliente_id"] == "c1"
    assert rows[0]["count"] == 1


def test_sorted_by_oldest_age_desc(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-10T10:00:00+00:00"},  # 2d
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-04-01T10:00:00+00:00"},  # 41d
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")
    assert [r["cliente_id"] for r in rows] == ["c2", "c1"]


def test_bucket_classification(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-10T00:00:00+00:00"},  # 2d → 0-7
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-03-25T00:00:00+00:00"},  # 48d → 30+
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")
    by_id = {r["cliente_id"]: r for r in rows}
    assert by_id["c1"]["bucket"] == "0-7"
    assert by_id["c2"]["bucket"] == "30+"
