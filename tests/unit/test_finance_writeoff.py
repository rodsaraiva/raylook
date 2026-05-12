"""Testes de mark_payment_written_off."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.services.finance_service as finance_service


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    return f


def test_marks_as_written_off(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
    })
    from app.services.finance_service import mark_payment_written_off
    out = mark_payment_written_off("pg1", reason="Cliente abandonou")
    assert out["status"] == "written_off"
    assert out["written_off_reason"] == "Cliente abandonou"
    assert out["written_off_at"] == fake.now_iso()
    assert fake.tables["pagamentos"][0]["status"] == "written_off"


def test_returns_404_when_missing(fake):
    from app.services.finance_service import mark_payment_written_off, PaymentNotFound
    with pytest.raises(PaymentNotFound):
        mark_payment_written_off("ghost", reason="x")


def test_idempotent_when_already_written_off(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "status": "written_off",
        "written_off_at": "2026-05-01T00:00:00+00:00",
        "written_off_reason": "Old",
    })
    from app.services.finance_service import mark_payment_written_off
    out = mark_payment_written_off("pg1", reason="Novo")
    # Não sobrescreve: mantém timestamp e reason originais
    assert out["written_off_at"] == "2026-05-01T00:00:00+00:00"
    assert out["written_off_reason"] == "Old"
