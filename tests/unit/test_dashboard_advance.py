"""Testes do POST /api/dashboard/packages/{id}/advance — transições para frente."""
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


def test_advance_404_when_missing(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/ghost/advance")
    assert res.status_code == 404


def test_advance_open_to_closed(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "open"})
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    body = res.json()
    assert body["previous"] == "aberto"
    assert body["new_state"] == "fechado"
    pkg = fake.tables["pacotes"][0]
    assert pkg["status"] == "closed"
    assert pkg["closed_at"] == fake.now_iso()


def test_advance_closed_creates_vendas_and_pagamentos(fake_client):
    """fechado→confirmado: cria 1 venda + 1 pagamento por pacote_cliente."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "closed", "enquete_id": "e1",
    })
    fake.tables["enquetes"].append({"id": "e1", "produto_id": "prod-1"})
    fake.tables["pacote_clientes"].extend([
        {"id": "pc1", "pacote_id": "p1", "cliente_id": "cli-1",
         "qty": 10, "unit_price": 80.0, "subtotal": 800.0,
         "commission_percent": 13.0, "commission_amount": 104.0,
         "total_amount": 904.0},
        {"id": "pc2", "pacote_id": "p1", "cliente_id": "cli-2",
         "qty": 14, "unit_price": 80.0, "subtotal": 1120.0,
         "commission_percent": 13.0, "commission_amount": 145.6,
         "total_amount": 1265.6},
    ])

    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "confirmado"
    assert fake.tables["pacotes"][0]["status"] == "approved"
    assert len(fake.tables["vendas"]) == 2
    assert len(fake.tables["pagamentos"]) == 2
    for pag in fake.tables["pagamentos"]:
        assert pag["status"] == "created"
        assert pag.get("payment_link") is None


def test_advance_confirmado_marks_pagamentos_paid(fake_client):
    """confirmado→pago: todos pagamentos viram paid."""
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "approved", "enquete_id": "e1"})
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].extend([
        {"id": "pag1", "venda_id": "v1", "status": "created"},
        {"id": "pag2", "venda_id": "v1", "status": "sent"},
    ])
    # No estado inicial state='confirmado' (approved sem todos pagos).
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "pago"
    for pag in fake.tables["pagamentos"]:
        assert pag["status"] == "paid"
        assert pag["paid_at"] == fake.now_iso()


def test_advance_pago_to_pendente_sets_validated_at(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "approved", "enquete_id": "e1"})
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    # Estado derivado = pago (todos paid, sem validated_at)
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["previous"] == "pago"
    assert res.json()["new_state"] == "pendente"
    assert fake.tables["pacotes"][0]["payment_validated_at"] == fake.now_iso()


def test_advance_pendente_to_separado_generates_pdf_name(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "sequence_no": 42,
        "payment_validated_at": "2026-05-10T10:00:00+00:00",
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "separado"
    pkg = fake.tables["pacotes"][0]
    assert pkg["pdf_status"] == "sent"
    assert pkg["pdf_file_name"] == "etiqueta-42.pdf"
    assert pkg["pdf_sent_at"] == fake.now_iso()


def test_advance_separado_to_enviado(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "payment_validated_at": "2026-05-10T10:00:00+00:00",
        "pdf_sent_at": "2026-05-10T11:00:00+00:00",
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "enviado"
    pkg = fake.tables["pacotes"][0]
    assert pkg["shipped_at"] == fake.now_iso()


def test_advance_enviado_returns_400(fake_client):
    """Estado final não avança."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "payment_validated_at": "2026-05-10T10:00:00+00:00",
        "pdf_sent_at": "2026-05-10T11:00:00+00:00",
        "shipped_at": "2026-05-10T12:00:00+00:00",
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v1", "status": "paid",
        "paid_at": "2026-05-10T09:00:00+00:00",
    })
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 400


def test_advance_cancelled_returns_400(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "cancelled"})
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 400


def test_advance_with_to_target_jumps_multiple_steps(fake_client):
    """?to=confirmado em pacote aberto: pula 2 steps (aberto→fechado→confirmado)."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "open", "enquete_id": "e1",
    })
    fake.tables["enquetes"].append({"id": "e1", "produto_id": "prod-1"})
    # Sem pacote_clientes — vai criar 0 vendas, mas state→approved (confirmado).

    res = client.post("/api/dashboard/packages/p1/advance?to=confirmado")
    assert res.status_code == 200
    body = res.json()
    assert body["previous"] == "aberto"
    assert body["new_state"] == "confirmado"
    assert body["steps"] >= 1


def test_advance_with_invalid_target_returns_400(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "open"})
    res = client.post("/api/dashboard/packages/p1/advance?to=invalid")
    assert res.status_code == 400


def test_advance_with_target_in_past_returns_400(fake_client):
    """Tentar pular pra trás (estado já alcançado) → 400."""
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    res = client.post("/api/dashboard/packages/p1/advance?to=aberto")
    assert res.status_code == 400
