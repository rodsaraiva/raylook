"""Testes dos endpoints de Enquetes em app/routers/dashboard.py:
- GET /api/dashboard/enquetes
- GET /api/dashboard/enquetes/{id}
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


# ── GET /enquetes (lista) ────────────────────────────────────────────────
def test_list_enquetes_empty(fake_client):
    client, _ = fake_client
    res = client.get("/api/dashboard/enquetes")
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 50


def test_list_enquetes_paginates(fake_client):
    """Mais que page_size enquetes devolve apenas a primeira página + total."""
    client, fake = fake_client
    for i in range(7):
        fake.tables["enquetes"].append({
            "id": f"e{i}", "titulo": f"Enquete {i}", "status": "closed",
            "created_at": f"2026-05-{10 + i:02d}T12:00:00+00:00",
        })
    res = client.get("/api/dashboard/enquetes?page=1&page_size=3")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 7
    assert body["page"] == 1
    assert body["page_size"] == 3
    assert len(body["items"]) == 3
    # ordem desc: e6, e5, e4
    assert [it["id"] for it in body["items"]] == ["e6", "e5", "e4"]
    # página 3 traz 1 item (índice 6)
    res2 = client.get("/api/dashboard/enquetes?page=3&page_size=3")
    assert [it["id"] for it in res2.json()["items"]] == ["e0"]


def test_list_enquetes_orders_desc_and_counts_pacotes(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Camiseta Vermelha", "status": "closed",
         "created_at": "2026-05-10T12:00:00+00:00", "fornecedor": "Cia X"},
        {"id": "e2", "titulo": "Calça Jeans", "status": "open",
         "created_at": "2026-05-15T08:00:00+00:00", "fornecedor": "Cia Y"},
    ])
    fake.tables["pacotes"].extend([
        {"id": "p1", "enquete_id": "e1", "status": "closed"},
        {"id": "p2", "enquete_id": "e1", "status": "approved"},
        {"id": "p3", "enquete_id": "e1", "status": "open"},
        {"id": "p4", "enquete_id": "e2", "status": "cancelled"},
    ])
    res = client.get("/api/dashboard/enquetes")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    # ordem desc por created_at
    assert [it["id"] for it in body["items"]] == ["e2", "e1"]
    e1 = next(it for it in body["items"] if it["id"] == "e1")
    assert e1["pacotes_total"] == 3
    # "fechados" = closed + approved
    assert e1["pacotes_fechados"] == 2
    assert e1["pacotes_by_status"] == {"open": 1, "closed": 1, "approved": 1, "cancelled": 0}
    e2 = next(it for it in body["items"] if it["id"] == "e2")
    assert e2["pacotes_total"] == 1
    assert e2["pacotes_by_status"]["cancelled"] == 1


def test_list_enquetes_filters_by_created_at_brt(fake_client):
    """Filtro since/until usa BRT — 2026-05-12 BRT → corte UTC 2026-05-12T03:00."""
    client, fake = fake_client
    fake.tables["enquetes"].extend([
        {"id": "e_old", "titulo": "Antiga", "status": "closed",
         "created_at": "2026-05-08T20:00:00+00:00"},
        {"id": "e_in", "titulo": "Recente", "status": "open",
         "created_at": "2026-05-14T15:00:00+00:00"},
    ])
    res = client.get("/api/dashboard/enquetes?since=2026-05-12")
    assert res.status_code == 200
    ids = [it["id"] for it in res.json()["items"]]
    assert ids == ["e_in"]


def test_list_enquetes_filters_by_title_query(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Camiseta vermelha", "status": "closed",
         "created_at": "2026-05-10T12:00:00+00:00"},
        {"id": "e2", "titulo": "Calça jeans azul", "status": "open",
         "created_at": "2026-05-15T08:00:00+00:00"},
    ])
    res = client.get("/api/dashboard/enquetes?q=camiseta")
    assert res.status_code == 200
    ids = [it["id"] for it in res.json()["items"]]
    assert ids == ["e1"]


def test_list_enquetes_rejects_invalid_date(fake_client):
    client, _ = fake_client
    res = client.get("/api/dashboard/enquetes?since=not-a-date")
    assert res.status_code == 400


# ── GET /enquetes/{id} (detalhe) ─────────────────────────────────────────
def test_get_enquete_detail_404_when_missing(fake_client):
    client, _ = fake_client
    res = client.get("/api/dashboard/enquetes/ghost")
    assert res.status_code == 404


def test_get_enquete_detail_returns_pacotes_with_clientes_and_states(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].append({
        "id": "e1", "titulo": "Camiseta", "status": "closed",
        "produto_id": "prod1", "fornecedor": "Cia X",
        "created_at": "2026-05-10T12:00:00+00:00",
    })
    fake.tables["produtos"].append({
        "id": "prod1", "nome": "Camiseta P", "valor_unitario": 50.0,
    })
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511"},
        {"id": "c2", "nome": "Bia", "celular": "5522"},
        {"id": "c3", "nome": "Caio", "celular": "5533"},
    ])
    # Pacote 1 — approved, com 2 clientes pagos (1 enviado, 1 pago)
    fake.tables["pacotes"].append({
        "id": "p1", "enquete_id": "e1", "status": "approved",
        "sequence_no": 1, "total_qty": 24, "capacidade_total": 24,
        "shipped_at": None,
    })
    fake.tables["pacote_clientes"].extend([
        {"id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 12,
         "total_amount": 600.0, "shipped_at": "2026-05-12T10:00:00+00:00"},
        {"id": "pc2", "pacote_id": "p1", "cliente_id": "c2", "qty": 12,
         "total_amount": 600.0},
    ])
    fake.tables["vendas"].extend([
        {"id": "vd1", "pacote_id": "p1", "pacote_cliente_id": "pc1",
         "cliente_id": "c1", "status": "approved"},
        {"id": "vd2", "pacote_id": "p1", "pacote_cliente_id": "pc2",
         "cliente_id": "c2", "status": "approved"},
    ])
    fake.tables["pagamentos"].extend([
        {"id": "pag1", "venda_id": "vd1", "status": "paid"},
        {"id": "pag2", "venda_id": "vd2", "status": "paid"},
    ])
    # Pacote 2 — open
    fake.tables["pacotes"].append({
        "id": "p2", "enquete_id": "e1", "status": "open",
        "sequence_no": 2, "total_qty": 9, "capacidade_total": 24,
    })

    res = client.get("/api/dashboard/enquetes/e1")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "e1"
    assert body["titulo"] == "Camiseta"
    assert body["produto"]["nome"] == "Camiseta P"
    assert body["pacotes_total"] == 2
    assert body["pacotes_fechados"] == 1  # só p1 (approved)
    assert body["pacotes_by_status"]["open"] == 1
    assert body["pacotes_by_status"]["approved"] == 1

    pacotes = body["pacotes"]
    assert len(pacotes) == 2
    p1_out = next(p for p in pacotes if p["id"] == "p1")
    # state derivado: todos pagos + shipped_at em pc → enviado pra c1, pago pra c2
    by_cliente = {c["cliente_id"]: c for c in p1_out["clientes"]}
    assert by_cliente["c1"]["state"] == "enviado"
    assert by_cliente["c2"]["state"] == "pago"
    assert by_cliente["c1"]["qty"] == 12
    assert by_cliente["c1"]["pagamento_status"] == "paid"

    p2_out = next(p for p in pacotes if p["id"] == "p2")
    assert p2_out["state"] == "aberto"
    assert p2_out["clientes"] == []  # sem pacote_clientes


def test_list_enquetes_cleans_product_name_from_whatsapp_title(fake_client):
    """Nome do produto vem cru do WhatsApp (REF=CAMISA + TOP 💰 *VALOR=$* 31 …).
    O endpoint deve devolver só o texto entre REF= e o próximo marcador."""
    client, fake = fake_client
    fake.tables["enquetes"].append({
        "id": "e1", "titulo": "tit", "status": "closed",
        "produto_id": "prod1",
        "created_at": "2026-05-10T12:00:00+00:00",
    })
    fake.tables["produtos"].append({
        "id": "prod1",
        "nome": "➡️ *REF=* CAMISA + TOP 💰 *VALOR=$* 31 🔖 *TECIDO=* LINHO",
        "valor_unitario": 31.0,
    })
    res = client.get("/api/dashboard/enquetes")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["produto"]["nome"] == "CAMISA + TOP"


def test_clean_product_name_handles_already_clean(fake_client):
    """Quando o nome já vem limpo, deixa como está."""
    from app.routers.dashboard import _clean_product_name
    assert _clean_product_name("Camiseta Polo M") == "Camiseta Polo M"
    assert _clean_product_name("") == ""
    assert _clean_product_name(None) is None


def test_clean_product_name_strips_emojis_and_separators(fake_client):
    """Sem REF=, devolve sem asteriscos e emojis-bullet iniciais."""
    from app.routers.dashboard import _clean_product_name
    assert _clean_product_name("*Calça Jeans*") == "Calça Jeans"
    assert _clean_product_name("➡️ Vestido midi") == "Vestido midi"


def test_get_enquete_detail_handles_pacote_without_produto(fake_client):
    """Enquete sem produto_id ou produto inexistente não deve quebrar."""
    client, fake = fake_client
    fake.tables["enquetes"].append({
        "id": "e1", "titulo": "Sem produto", "status": "closed",
        "created_at": "2026-05-10T12:00:00+00:00",
    })
    res = client.get("/api/dashboard/enquetes/e1")
    assert res.status_code == 200
    body = res.json()
    assert body["produto"] is None
    assert body["pacotes"] == []
