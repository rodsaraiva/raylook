"""Testes do GET /api/dashboard/packages/{id} — drill-down de um pacote."""
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


def test_drill_down_404_when_missing(fake_client):
    client, _ = fake_client
    res = client.get("/api/dashboard/packages/ghost")
    assert res.status_code == 404


def test_drill_down_returns_clientes_from_pacote_clientes(fake_client):
    """Pacote fechado com pacote_clientes registrados — devem vir em clientes
    com is_voter_only=False."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "closed", "enquete_id": "e1",
        "sequence_no": 7, "capacidade_total": 24, "total_qty": 24,
        "opened_at": "2026-05-10T10:00:00+00:00",
        "closed_at": "2026-05-11T11:00:00+00:00",
    })
    fake.tables["enquetes"].append({
        "id": "e1", "produto_id": "prod-1", "titulo": "Camiseta",
        "external_poll_id": "wa-1", "chat_id": "chat-1", "status": "active",
    })
    fake.tables["produtos"].append({
        "id": "prod-1", "nome": "Camiseta", "valor_unitario": 80.0,
    })
    fake.tables["clientes"].append({
        "id": "cli-1", "nome": "Ana", "celular": "5511999",
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc-1", "pacote_id": "p1", "cliente_id": "cli-1",
        "qty": 24, "subtotal": 1920.0, "total_amount": 2169.60,
    })

    res = client.get("/api/dashboard/packages/p1")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "p1"
    assert body["state"] == "fechado"
    assert body["sequence_no"] == 7
    assert body["produto"]["nome"] == "Camiseta"
    assert body["enquete"]["external_poll_id"] == "wa-1"
    assert len(body["clientes"]) == 1
    cli = body["clientes"][0]
    assert cli["nome"] == "Ana"
    assert cli["qty"] == 24
    assert cli["is_voter_only"] is False


def test_drill_down_falls_back_to_voters_when_no_pacote_clientes(fake_client):
    """Pacote aberto sem pacote_clientes mas com votos in → voters como
    candidatos. is_voter_only=True."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "open", "enquete_id": "e1",
        "sequence_no": 1, "capacidade_total": 24, "total_qty": 0,
        "opened_at": "2026-05-11T10:00:00+00:00",
    })
    fake.tables["enquetes"].append({
        "id": "e1", "produto_id": "prod-1", "titulo": "Camiseta",
    })
    fake.tables["produtos"].append({
        "id": "prod-1", "nome": "Camiseta", "valor_unitario": 80.0,
    })
    fake.tables["clientes"].extend([
        {"id": "cli-1", "nome": "Bia", "celular": "5511111"},
        {"id": "cli-2", "nome": "Caio", "celular": "5511222"},
    ])
    fake.tables["votos"].extend([
        {"id": "v1", "enquete_id": "e1", "cliente_id": "cli-1", "qty": 10,
         "status": "in", "voted_at": "2026-05-11T11:00:00+00:00"},
        {"id": "v2", "enquete_id": "e1", "cliente_id": "cli-2", "qty": 6,
         "status": "in", "voted_at": "2026-05-11T11:05:00+00:00"},
    ])

    res = client.get("/api/dashboard/packages/p1")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "aberto"
    assert len(body["clientes"]) == 2
    assert all(c["is_voter_only"] for c in body["clientes"])
    bia = next(c for c in body["clientes"] if c["nome"] == "Bia")
    assert bia["qty"] == 10
    assert bia["subtotal"] == 800.0  # 10 * 80
    assert bia["total_amount"] == 850.0  # 800 + 10 * R$5/peça


def test_drill_down_timeline_ordered_by_timestamp(fake_client):
    """Timeline deve ter entradas só pros estados com timestamp, ordenadas."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "capacidade_total": 24, "total_qty": 24,
        "opened_at": "2026-05-10T10:00:00+00:00",
        "closed_at": "2026-05-10T12:00:00+00:00",
        "approved_at": "2026-05-10T13:00:00+00:00",
        "confirmed_by": "gerente@dev",
    })
    fake.tables["enquetes"].append({"id": "e1", "titulo": "X"})

    res = client.get("/api/dashboard/packages/p1")
    assert res.status_code == 200
    timeline = res.json()["timeline"]
    states = [t["state"] for t in timeline]
    assert states == ["aberto", "fechado", "confirmado"]
    assert "gerente@dev" in timeline[-1]["note"]


def test_drill_down_without_enquete_or_produto(fake_client):
    """Pacote isolado (sem enquete_id) — não deve quebrar, retorna nulls."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "open", "enquete_id": None,
        "capacidade_total": 24, "total_qty": 0,
        "opened_at": "2026-05-11T10:00:00+00:00",
    })
    res = client.get("/api/dashboard/packages/p1")
    assert res.status_code == 200
    body = res.json()
    assert body["produto"] is None
    assert body["enquete"] is None
    assert body["clientes"] == []
