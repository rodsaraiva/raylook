"""Testes dos endpoints POST que mudam estado do pacote:
- /api/dashboard/packages/{id}/cancel
- /api/dashboard/packages/{id}/restore
- /api/dashboard/packages/{id}/regress
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


# ── /cancel ────────────────────────────────────────────────────────────────
def test_cancel_404_when_package_missing(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/ghost/cancel")
    assert res.status_code == 404


def test_cancel_sets_status_cancelled(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    res = client.post("/api/dashboard/packages/p1/cancel")
    assert res.status_code == 200
    assert res.json()["new_state"] == "cancelled"
    assert fake.tables["pacotes"][0]["status"] == "cancelled"
    assert fake.tables["pacotes"][0]["cancelled_at"] == fake.now_iso()


def test_cancel_400_when_already_cancelled(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "cancelled"})
    res = client.post("/api/dashboard/packages/p1/cancel")
    assert res.status_code == 400


# ── /restore ───────────────────────────────────────────────────────────────
def test_restore_404_when_package_missing(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/ghost/restore")
    assert res.status_code == 404


def test_restore_brings_cancelled_back_to_closed(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "cancelled",
        "cancelled_at": "2026-05-10T10:00:00+00:00",
        "cancelled_by": "alguem@dev",
    })
    res = client.post("/api/dashboard/packages/p1/restore")
    assert res.status_code == 200
    assert res.json()["new_state"] == "fechado"
    pkg = fake.tables["pacotes"][0]
    assert pkg["status"] == "closed"
    assert pkg["cancelled_at"] is None
    assert pkg["cancelled_by"] is None


def test_restore_400_when_not_cancelled(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    res = client.post("/api/dashboard/packages/p1/restore")
    assert res.status_code == 400


# ── /regress ───────────────────────────────────────────────────────────────
def test_regress_400_in_aberto(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "open"})
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 400
    assert "aberto" in res.json()["detail"].lower()


def test_regress_400_in_cancelled(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "cancelled"})
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 400


def test_regress_400_in_fechado(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 400


def test_regress_pendente_unsets_validated_at(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "payment_validated_at": "2026-05-10T10:00:00+00:00",
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 200
    body = res.json()
    assert body["previous"] == "pendente"
    assert body["new_state"] == "pago"
    assert fake.tables["pacotes"][0]["payment_validated_at"] is None


def test_regress_confirmado_deletes_vendas_and_pagamentos(fake_client):
    """status=approved sem payment_validated_at e com pagamento(s) NÃO pagos
    → state='confirmado'. Regress deve apagar vendas/pagamentos e voltar
    pacote pra 'closed'."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "approved_at": "2026-05-10T10:00:00+00:00",
        "confirmed_by": "alguem@dev",
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "created",
    })
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 200
    body = res.json()
    assert body["previous"] == "confirmado"
    assert body["new_state"] == "fechado"
    assert fake.tables["pacotes"][0]["status"] == "closed"
    assert fake.tables["pacotes"][0]["approved_at"] is None
    assert fake.tables["vendas"] == []
    assert fake.tables["pagamentos"] == []


def test_regress_pago_reverts_paid_to_sent(fake_client):
    """state='pago': todos pagamentos paid, sem validated_at. Regress reverte
    paid→sent (não apaga)."""
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "approved", "enquete_id": "e1"})
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    res = client.post("/api/dashboard/packages/p1/regress")
    assert res.status_code == 200
    body = res.json()
    assert body["previous"] == "pago"
    assert body["new_state"] == "confirmado"
    pag = fake.tables["pagamentos"][0]
    assert pag["status"] == "sent"
    assert pag["paid_at"] is None
