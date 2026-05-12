"""Testes de build_payment_history."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.services.finance_service as finance_service


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    f.tables["clientes"].append({
        "id": "c1", "nome": "Ana", "celular": "5511999990001",
        "session_expires_at": "2026-06-10T00:00:00+00:00",
    })
    f.tables["vendas"].append({"id": "v1", "cliente_id": "c1", "pacote_id": "p1"})
    return f


def test_basic_timeline(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-03T14:00:00+00:00",
        "pix_payload": "00020126...",
        "paid_at": None,
        "written_off_at": None,
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")

    kinds = [e["kind"] for e in events]
    assert "package_confirmed" in kinds
    assert "pix_generated" in kinds
    assert "last_portal_access" in kinds
    # Cronologicamente ordenado
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_paid_event_present(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "paid",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-04T10:00:00+00:00",
        "pix_payload": "00020126...",
        "paid_at": "2026-05-05T11:00:00+00:00",
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    assert any(e["kind"] == "paid" for e in events)


def test_no_pix_event_when_no_payload(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "created",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-01T10:00:00+00:00",
        "pix_payload": None,
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    assert not any(e["kind"] == "pix_generated" for e in events)


def test_written_off_event(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "written_off",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-10T10:00:00+00:00",
        "written_off_at": "2026-05-10T10:00:00+00:00",
        "written_off_reason": "Cliente sumiu",
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    wo = [e for e in events if e["kind"] == "written_off"]
    assert len(wo) == 1
    assert wo[0]["reason"] == "Cliente sumiu"


def test_returns_empty_for_unknown_id(fake):
    from app.services.finance_service import build_payment_history
    assert build_payment_history("nope") == []
