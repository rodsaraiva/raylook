"""Testes dos endpoints de cliente em app/routers/dashboard.py:
- GET  /api/dashboard/clientes
- POST /api/dashboard/packages/{id}/clients
- DELETE /api/dashboard/packages/{id}/clients/{cli}
- POST /api/dashboard/packages/{id}/clients/{cli}/advance
- POST /api/dashboard/packages/{id}/clients/{cli}/mark-paid
- POST /api/dashboard/packages/{id}/resend-pix
- GET  /api/dashboard/packages/{id}/swap-candidates/{cli}
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["enquete_alternativas"] = []
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


# ── GET /clientes ──────────────────────────────────────────────────────────
def test_list_clientes_empty(fake_client):
    client, _ = fake_client
    res = client.get("/api/dashboard/clientes")
    assert res.status_code == 200
    assert res.json() == []


def test_list_clientes_with_query_filters_by_name(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana Silva", "celular": "5511111"},
        {"id": "c2", "nome": "Bia Costa", "celular": "5511222"},
        {"id": "c3", "nome": "Carlos", "celular": "5511333"},
    ])
    res = client.get("/api/dashboard/clientes?q=ana")
    assert res.status_code == 200
    nomes = [c["nome"] for c in res.json()]
    assert nomes == ["Ana Silva"]


def test_list_clientes_excludes_those_already_in_package(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511"},
        {"id": "c2", "nome": "Bia", "celular": "5522"},
    ])
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 3,
    })
    res = client.get("/api/dashboard/clientes?exclude_pacote=p1")
    assert res.status_code == 200
    ids = [c["id"] for c in res.json()]
    assert ids == ["c2"]


# ── POST /packages/{id}/clients (add) ──────────────────────────────────────
def test_add_client_400_when_qty_invalid(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "open"})
    res = client.post("/api/dashboard/packages/p1/clients",
                      json={"cliente_id": "c1", "qty": 4})
    assert res.status_code == 400


def test_add_client_400_when_cliente_id_missing(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "open"})
    res = client.post("/api/dashboard/packages/p1/clients", json={"qty": 3})
    assert res.status_code == 400


def test_add_client_404_when_package_missing(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/ghost/clients",
                      json={"cliente_id": "c1", "qty": 3})
    assert res.status_code == 404


def test_add_client_open_creates_voto(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "open", "enquete_id": "e1",
        "total_qty": 0, "participants_count": 0,
    })
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana", "celular": "5511"})
    res = client.post("/api/dashboard/packages/p1/clients",
                      json={"cliente_id": "c1", "qty": 6})
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "voto_added"
    # voto criado em pacote aberto
    assert len(fake.tables["votos"]) == 1
    assert fake.tables["votos"][0]["cliente_id"] == "c1"
    assert fake.tables["votos"][0]["qty"] == 6
    # contadores atualizados
    pkg = fake.tables["pacotes"][0]
    assert pkg["total_qty"] == 6
    assert pkg["participants_count"] == 1


def test_add_client_closed_returns_400(fake_client):
    """Adicionar cliente em pacote fechado não é suportado."""
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "closed"})
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana", "celular": "5511"})
    res = client.post("/api/dashboard/packages/p1/clients",
                      json={"cliente_id": "c1", "qty": 3})
    assert res.status_code == 400


# ── DELETE /packages/{id}/clients/{cli} ────────────────────────────────────
def test_remove_client_open_marks_voto_out(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "open", "enquete_id": "e1",
        "total_qty": 6, "participants_count": 1,
    })
    fake.tables["votos"].append({
        "id": "v1", "enquete_id": "e1", "cliente_id": "c1",
        "qty": 6, "status": "in",
    })
    res = client.delete("/api/dashboard/packages/p1/clients/c1")
    assert res.status_code == 200
    assert res.json()["action"] == "voto_removed"
    assert fake.tables["votos"][0]["status"] == "out"
    assert fake.tables["pacotes"][0]["total_qty"] == 0


def test_remove_client_closed_deletes_pacote_cliente_and_venda(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
        "total_qty": 6, "participants_count": 1,
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 6,
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({"id": "pag1", "venda_id": "v1", "status": "created"})
    res = client.delete("/api/dashboard/packages/p1/clients/c1")
    assert res.status_code == 200
    assert res.json()["action"] == "pacote_cliente_removed"
    assert fake.tables["pacote_clientes"] == []
    assert fake.tables["vendas"] == []
    assert fake.tables["pagamentos"] == []


def test_remove_client_404_when_not_in_package(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    res = client.delete("/api/dashboard/packages/p1/clients/ghost")
    assert res.status_code == 404


# ── /clients/{cli}/mark-paid ───────────────────────────────────────────────
def test_mark_paid_happy(fake_client):
    client, fake = fake_client
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 3,
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({"id": "pag1", "venda_id": "v1", "status": "created"})
    res = client.post("/api/dashboard/packages/p1/clients/c1/mark-paid")
    assert res.status_code == 200
    pag = fake.tables["pagamentos"][0]
    assert pag["status"] == "paid"
    assert pag["paid_at"] == fake.now_iso()


def test_mark_paid_400_when_already_paid(fake_client):
    client, fake = fake_client
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 3,
    })
    fake.tables["vendas"].append({"id": "v1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({"id": "pag1", "venda_id": "v1", "status": "paid"})
    res = client.post("/api/dashboard/packages/p1/clients/c1/mark-paid")
    assert res.status_code == 400


def test_mark_paid_404_when_cliente_not_in_package(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/p1/clients/c1/mark-paid")
    assert res.status_code == 404


# ── /resend-pix ────────────────────────────────────────────────────────────
def test_resend_pix_marks_open_payments_as_sent(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["vendas"].append({"id": "v1", "pacote_id": "p1"})
    fake.tables["pagamentos"].extend([
        {"id": "pag1", "venda_id": "v1", "status": "created"},
        {"id": "pag2", "venda_id": "v1", "status": "sent"},
        {"id": "pag3", "venda_id": "v1", "status": "paid"},
    ])
    res = client.post("/api/dashboard/packages/p1/resend-pix")
    assert res.status_code == 200
    assert res.json()["reminded"] == 2  # created + sent
    statuses = sorted(p["status"] for p in fake.tables["pagamentos"])
    assert statuses == ["paid", "sent", "sent"]


def test_resend_pix_404_when_package_missing(fake_client):
    client, _ = fake_client
    res = client.post("/api/dashboard/packages/ghost/resend-pix")
    assert res.status_code == 404


# ── /swap-candidates ───────────────────────────────────────────────────────
def test_swap_candidates_returns_voters_with_same_qty(fake_client):
    """Pacote fechado tem c1 com qty=6. c2 votou na mesma enquete também
    com qty=6 e não está consumido — deve ser candidato."""
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 6,
    })
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511"},
        {"id": "c2", "nome": "Bia", "celular": "5522"},
        {"id": "c3", "nome": "Caio", "celular": "5533"},
    ])
    fake.tables["votos"].extend([
        {"id": "v2", "enquete_id": "e1", "cliente_id": "c2", "qty": 6, "status": "in"},
        {"id": "v3", "enquete_id": "e1", "cliente_id": "c3", "qty": 3, "status": "in"},
    ])
    res = client.get("/api/dashboard/packages/p1/swap-candidates/c1")
    assert res.status_code == 200
    body = res.json()
    assert [c["id"] for c in body] == ["c2"]
    assert body[0]["qty"] == 6


def test_swap_candidates_empty_when_no_match(fake_client):
    client, fake = fake_client
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 6,
    })
    res = client.get("/api/dashboard/packages/p1/swap-candidates/c1")
    assert res.status_code == 200
    assert res.json() == []
