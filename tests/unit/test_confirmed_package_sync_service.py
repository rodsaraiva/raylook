"""Testes de app/services/confirmed_package_sync_service.

Cobre as partes que dão mais valor sem precisar mockar joins de
PostgREST: _calc_financials, analyze() e os helpers que tocam só
em tabelas simples (clientes, vendas, pagamentos).
"""
from __future__ import annotations

import pytest

from app.services import confirmed_package_sync_service as svc
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables


# ── _calc_financials ───────────────────────────────────────────────────────
def test_calc_financials_basic():
    """unit=80, qty=3 → subtotal=240, commission=3*5=15, total=255."""
    result = svc._calc_financials(80.0, 3)
    assert result == {"subtotal": 240.0, "commission_amount": 15.0, "total_amount": 255.0}


def test_calc_financials_round_correctly():
    """unit=33.33, qty=6 → subtotal=199.98, commission=6*5=30, total=229.98."""
    result = svc._calc_financials(33.33, 6)
    assert result["subtotal"] == 199.98
    assert result["commission_amount"] == 30.0
    assert result["total_amount"] == round(199.98 + 30.0, 2)


def test_calc_financials_zero_qty():
    result = svc._calc_financials(80.0, 0)
    assert result == {"subtotal": 0.0, "commission_amount": 0.0, "total_amount": 0.0}


# ── _find_client_by_phone ──────────────────────────────────────────────────
def test_find_client_by_phone_returns_match():
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"id": "c1", "nome": "Ana", "celular": "5511999999999"},
        {"id": "c2", "nome": "Bia", "celular": "5511888888888"},
    ]})
    s = svc.ConfirmedPackageSyncService(sb=fake)
    result = s._find_client_by_phone("5511888888888")
    assert result is not None
    assert result["id"] == "c2"


def test_find_client_by_phone_normalizes_digits():
    """_clean_phone tira não-dígitos antes de comparar."""
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"id": "c1", "nome": "Ana", "celular": "5511999999999"},
    ]})
    s = svc.ConfirmedPackageSyncService(sb=fake)
    result = s._find_client_by_phone("+55 (11) 99999-9999")
    assert result is not None
    assert result["id"] == "c1"


def test_find_client_by_phone_returns_none_when_empty():
    fake = FakeSupabaseClient(empty_tables())
    s = svc.ConfirmedPackageSyncService(sb=fake)
    assert s._find_client_by_phone("") is None
    assert s._find_client_by_phone(None) is None


def test_find_client_by_phone_returns_none_when_no_match():
    fake = FakeSupabaseClient({**empty_tables(), "clientes": [
        {"id": "c1", "nome": "Ana", "celular": "5511999999999"},
    ]})
    s = svc.ConfirmedPackageSyncService(sb=fake)
    assert s._find_client_by_phone("5511000000000") is None


# ── _detect_paid_removals ──────────────────────────────────────────────────
def test_detect_paid_removals_empty_list_returns_empty():
    fake = FakeSupabaseClient(empty_tables())
    s = svc.ConfirmedPackageSyncService(sb=fake)
    assert s._detect_paid_removals("pkg-1", []) == []


def test_detect_paid_removals_flags_paid_client():
    fake = FakeSupabaseClient({**empty_tables(),
        "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
        "vendas": [{"id": "v1", "pacote_id": "pkg-1", "cliente_id": "c1"}],
        "pagamentos": [{"id": "p1", "venda_id": "v1", "status": "paid",
                        "paid_at": "2026-05-10T10:00:00Z"}],
    })
    s = svc.ConfirmedPackageSyncService(sb=fake)
    removed = [{"phone": "5511999999999", "name": "Ana", "qty": 3}]
    result = s._detect_paid_removals("pkg-1", removed)
    assert len(result) == 1
    assert result[0]["phone"] == "5511999999999"
    assert result[0]["pagamento_id"] == "p1"
    assert result[0]["paid_at"] == "2026-05-10T10:00:00Z"


def test_detect_paid_removals_ignores_unpaid_status():
    """Status diferente de PAID_STATUSES não é flagueado."""
    fake = FakeSupabaseClient({**empty_tables(),
        "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
        "vendas": [{"id": "v1", "pacote_id": "pkg-1", "cliente_id": "c1"}],
        "pagamentos": [{"id": "p1", "venda_id": "v1", "status": "created"}],
    })
    s = svc.ConfirmedPackageSyncService(sb=fake)
    removed = [{"phone": "5511999999999", "qty": 3}]
    assert s._detect_paid_removals("pkg-1", removed) == []


def test_detect_paid_removals_skips_unknown_client():
    """Cliente não encontrado por phone → não tem como detectar, ignora."""
    fake = FakeSupabaseClient(empty_tables())
    s = svc.ConfirmedPackageSyncService(sb=fake)
    removed = [{"phone": "5511999999999", "qty": 3}]
    assert s._detect_paid_removals("pkg-1", removed) == []


def test_detect_paid_removals_skips_when_no_venda():
    """Cliente existe mas sem venda no pacote → não detecta."""
    fake = FakeSupabaseClient({**empty_tables(),
        "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
    })
    s = svc.ConfirmedPackageSyncService(sb=fake)
    removed = [{"phone": "5511999999999", "qty": 3}]
    assert s._detect_paid_removals("pkg-1", removed) == []


def test_detect_paid_removals_accepts_all_paid_statuses():
    """PAID_STATUSES = {paid, received, confirmed, completed}."""
    for status in ("paid", "received", "confirmed", "completed"):
        fake = FakeSupabaseClient({**empty_tables(),
            "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
            "vendas": [{"id": "v1", "pacote_id": "pkg-1", "cliente_id": "c1"}],
            "pagamentos": [{"id": "p1", "venda_id": "v1", "status": status}],
        })
        s = svc.ConfirmedPackageSyncService(sb=fake)
        removed = [{"phone": "5511999999999", "qty": 3}]
        result = s._detect_paid_removals("pkg-1", removed)
        assert len(result) == 1, f"status={status} should be detected as paid"


# ── analyze() ──────────────────────────────────────────────────────────────
def test_analyze_no_changes_returns_clean_diff():
    fake = FakeSupabaseClient(empty_tables())
    s = svc.ConfirmedPackageSyncService(sb=fake)
    votes = [{"phone": "5511999999999", "qty": 3, "name": "Ana"}]
    result = s.analyze("pkg-1", votes, votes)
    assert result["diff"]["added"] == []
    assert result["diff"]["removed"] == []
    assert result["diff"]["changed"] == []
    assert result["requires_confirmation"] is False


def test_analyze_added_voter_no_confirmation_needed():
    fake = FakeSupabaseClient(empty_tables())
    s = svc.ConfirmedPackageSyncService(sb=fake)
    result = s.analyze(
        "pkg-1",
        current_votes=[],
        new_votes=[{"phone": "5511999999999", "qty": 3, "name": "Ana"}],
    )
    assert len(result["diff"]["added"]) == 1
    assert result["requires_confirmation"] is False
    assert result["paid_removals"] == []


def test_analyze_removed_unpaid_no_confirmation_needed():
    """Removeu um voter mas o cliente não tem pagamento → safe."""
    fake = FakeSupabaseClient({**empty_tables(),
        "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
    })
    s = svc.ConfirmedPackageSyncService(sb=fake)
    result = s.analyze(
        "pkg-1",
        current_votes=[{"phone": "5511999999999", "qty": 3, "name": "Ana"}],
        new_votes=[],
    )
    assert len(result["diff"]["removed"]) == 1
    assert result["requires_confirmation"] is False


def test_analyze_removed_paid_requires_confirmation():
    fake = FakeSupabaseClient({**empty_tables(),
        "clientes": [{"id": "c1", "nome": "Ana", "celular": "5511999999999"}],
        "vendas": [{"id": "v1", "pacote_id": "pkg-1", "cliente_id": "c1"}],
        "pagamentos": [{"id": "p1", "venda_id": "v1", "status": "paid"}],
    })
    s = svc.ConfirmedPackageSyncService(sb=fake)
    result = s.analyze(
        "pkg-1",
        current_votes=[{"phone": "5511999999999", "qty": 3, "name": "Ana"}],
        new_votes=[],
    )
    assert len(result["paid_removals"]) == 1
    assert result["requires_confirmation"] is True
