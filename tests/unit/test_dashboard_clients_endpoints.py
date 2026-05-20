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
                      json={"cliente_id": "c1", "qty": 5})
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
def test_swap_candidates_returns_voters_with_qty_lte_target(fake_client):
    """Pacote tem c1 com qty=6. c2 votou qty=6 e c3 votou qty=3 — ambos
    são candidatos válidos (qty <= 6 permite composições por soma)."""
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
        {"id": "c4", "nome": "Dani", "celular": "5544"},
    ])
    fake.tables["votos"].extend([
        {"id": "v2", "enquete_id": "e1", "cliente_id": "c2", "qty": 6, "status": "in"},
        {"id": "v3", "enquete_id": "e1", "cliente_id": "c3", "qty": 3, "status": "in"},
        {"id": "v4", "enquete_id": "e1", "cliente_id": "c4", "qty": 12, "status": "in"},
    ])
    res = client.get("/api/dashboard/packages/p1/swap-candidates/c1")
    assert res.status_code == 200
    body = res.json()
    assert body["target_qty"] == 6
    by_id = {c["id"]: c for c in body["candidates"]}
    # c2 (qty=6) e c3 (qty=3) entram. c4 (qty=12) fica fora (> target).
    assert set(by_id.keys()) == {"c2", "c3"}
    assert by_id["c2"]["qty"] == 6
    assert by_id["c3"]["qty"] == 3


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
    assert res.json() == {"target_qty": 6, "candidates": []}


# ── PATCH /packages/{id}/clients/{cli} (swap) ──────────────────────────────
def _seed_swap_scenario(fake, *, exit_qty=6, replacements=(("c2", 6),)):
    """Cenário: c1 está num pacote aprovado com `exit_qty` peças. Para cada
    (cliente_id, qty) em `replacements`, semeia voto correspondente."""
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "enquete_id": "e1",
    })
    fake.tables["enquetes"].append({
        "id": "e1", "produto_id": "prod1",
    })
    fake.tables["produtos"].append({
        "id": "prod1", "valor_unitario": 10.0,
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "c1",
        "voto_id": "v1", "produto_id": "prod1",
        "qty": exit_qty, "unit_price": 10.0,
        "subtotal": 10.0 * exit_qty, "commission_percent": 0,
        "commission_amount": 5.0 * exit_qty,
        "total_amount": 10.0 * exit_qty + 5.0 * exit_qty,
        "status": "closed",
    })
    fake.tables["clientes"].extend([{"id": "c1", "nome": "Ana", "celular": "5511"}])
    fake.tables["votos"].append({
        "id": "v1", "enquete_id": "e1", "cliente_id": "c1",
        "qty": exit_qty, "status": "in",
    })
    for cid, qty in replacements:
        fake.tables["clientes"].append({"id": cid, "nome": cid.upper(), "celular": f"55{cid}"})
        fake.tables["votos"].append({
            "id": f"v_{cid}", "enquete_id": "e1", "cliente_id": cid,
            "qty": qty, "status": "in",
        })
    fake.tables["vendas"].append({
        "id": "v_a", "pacote_id": "p1", "pacote_cliente_id": "pc1",
        "cliente_id": "c1", "total_amount": 10.0 * exit_qty + 5.0 * exit_qty,
    })


def test_swap_blocks_when_pagamento_already_paid(fake_client):
    client, fake = fake_client
    _seed_swap_scenario(fake)
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v_a", "status": "paid",
        "provider_payment_id": "as_001",
    })
    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2"]})
    assert res.status_code == 409
    # estado preservado
    assert fake.tables["pacote_clientes"][0]["cliente_id"] == "c1"
    assert fake.tables["pagamentos"][0]["status"] == "paid"


def test_swap_single_replaces_pc_and_recreates_venda_pagamento(fake_client, monkeypatch):
    """Swap 1:1 com qty igual — fluxo simples antigo, agora deletando e
    recriando venda+pagamento."""
    client, fake = fake_client
    _seed_swap_scenario(fake)
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v_a", "status": "sent",
        "provider_payment_id": "as_001",
        "provider_customer_id": "cus_001",
        "payment_link": "https://asaas/pay/x",
        "pix_payload": "00020126...",
    })

    cancel_calls = []
    from integrations.asaas.client import AsaasClient
    monkeypatch.setattr(
        AsaasClient, "cancel_payment",
        lambda self, pid: cancel_calls.append(pid) or {"id": pid, "deleted": True},
    )

    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["action"] == "swapped"
    assert body["pagamentos_removidos"] == 1
    assert body["to"] == [{"cliente_id": "c2", "qty": 6}]
    assert cancel_calls == ["as_001"]

    # Pagamento antigo foi deletado; novo criado status=created sem provider id.
    assert len(fake.tables["pagamentos"]) == 1
    pag = fake.tables["pagamentos"][0]
    assert pag["status"] == "created"
    assert pag.get("provider_payment_id") in (None, "")

    # Venda antiga deletada; nova vinculada ao novo pacote_cliente do c2.
    assert len(fake.tables["vendas"]) == 1
    venda = fake.tables["vendas"][0]
    assert venda["cliente_id"] == "c2"
    assert venda["qty"] == 6

    # Pacote_clientes: pc1 (c1) sumiu; um novo para c2.
    pcs = fake.tables["pacote_clientes"]
    assert len(pcs) == 1
    assert pcs[0]["cliente_id"] == "c2"
    assert pcs[0]["qty"] == 6
    assert pcs[0]["voto_id"] == "v_c2"


def test_swap_split_one_into_two(fake_client, monkeypatch):
    """Trocar 1 de 12 por 2 de 6 — caso novo, núcleo da feature."""
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=12, replacements=(("c2", 6), ("c3", 6), ("c4", 3)))
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v_a", "status": "sent",
        "provider_payment_id": "as_001",
    })
    from integrations.asaas.client import AsaasClient
    monkeypatch.setattr(AsaasClient, "cancel_payment", lambda self, pid: None)

    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2", "c3"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["to"] == [{"cliente_id": "c2", "qty": 6}, {"cliente_id": "c3", "qty": 6}]

    pcs = sorted(fake.tables["pacote_clientes"], key=lambda r: r["cliente_id"])
    assert [p["cliente_id"] for p in pcs] == ["c2", "c3"]
    assert all(p["qty"] == 6 for p in pcs)
    assert pcs[0]["voto_id"] == "v_c2"
    assert pcs[1]["voto_id"] == "v_c3"

    vendas = sorted(fake.tables["vendas"], key=lambda r: r["cliente_id"])
    assert [v["cliente_id"] for v in vendas] == ["c2", "c3"]
    assert all(v["qty"] == 6 for v in vendas)

    # Um pagamento por nova venda, todos status='created'.
    assert len(fake.tables["pagamentos"]) == 2
    assert all(p["status"] == "created" for p in fake.tables["pagamentos"])


def test_swap_split_six_into_three_plus_three(fake_client, monkeypatch):
    """1 de 6 → 2 de 3 — caso pedido pelo usuário."""
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=6, replacements=(("c2", 3), ("c3", 3), ("c4", 6)))
    from integrations.asaas.client import AsaasClient
    monkeypatch.setattr(AsaasClient, "cancel_payment", lambda self, pid: None)

    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2", "c3"]})
    assert res.status_code == 200, res.text
    assert res.json()["to"] == [{"cliente_id": "c2", "qty": 3}, {"cliente_id": "c3", "qty": 3}]
    pcs = sorted(fake.tables["pacote_clientes"], key=lambda r: r["cliente_id"])
    assert [p["qty"] for p in pcs] == [3, 3]


def test_swap_rejects_when_sum_doesnt_match(fake_client):
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=12, replacements=(("c2", 6), ("c3", 3)))
    # Soma = 9, alvo = 12 → 400
    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2", "c3"]})
    assert res.status_code == 400
    assert "Soma das quantidades" in res.json()["detail"]
    # Nada mexido.
    assert fake.tables["pacote_clientes"][0]["cliente_id"] == "c1"


def test_swap_rejects_duplicates(fake_client):
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=6, replacements=(("c2", 3),))
    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2", "c2"]})
    assert res.status_code == 400
    assert "duplicatas" in res.json()["detail"]


def test_swap_rejects_when_candidate_already_in_package(fake_client):
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=6, replacements=(("c2", 6), ("c3", 6)))
    # c3 já está no pacote
    fake.tables["pacote_clientes"].append({
        "id": "pc2", "pacote_id": "p1", "cliente_id": "c3",
        "voto_id": "v_c3", "qty": 6,
    })
    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c3"]})
    assert res.status_code == 400


def test_swap_rejects_when_candidate_in_other_active_package(fake_client):
    client, fake = fake_client
    _seed_swap_scenario(fake, exit_qty=6, replacements=(("c2", 6),))
    # c2 também está num outro pacote aprovado
    fake.tables["pacotes"].append({"id": "p2", "status": "approved", "enquete_id": "e1"})
    fake.tables["pacote_clientes"].append({
        "id": "pc2", "pacote_id": "p2", "cliente_id": "c2", "qty": 6,
    })
    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2"]})
    assert res.status_code == 400


def test_swap_proceeds_even_if_asaas_cancel_fails(fake_client, monkeypatch):
    """Falha no cancel do Asaas é best-effort: loga warn e segue."""
    client, fake = fake_client
    _seed_swap_scenario(fake)
    fake.tables["pagamentos"].append({
        "id": "pag1", "venda_id": "v_a", "status": "sent",
        "provider_payment_id": "as_001",
    })

    def boom(self, pid):
        raise RuntimeError("asaas 400")
    from integrations.asaas.client import AsaasClient
    monkeypatch.setattr(AsaasClient, "cancel_payment", boom)

    res = client.patch("/api/dashboard/packages/p1/clients/c1",
                       json={"new_cliente_ids": ["c2"]})
    assert res.status_code == 200
    # Venda antiga deletada e nova criada com c2.
    assert len(fake.tables["vendas"]) == 1
    assert fake.tables["vendas"][0]["cliente_id"] == "c2"
    assert fake.tables["pacote_clientes"][0]["cliente_id"] == "c2"


# ── GET /clientes/list + /clientes/stats ───────────────────────────────────
def test_clientes_list_envelope_e_paginacao(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": f"c{i}", "nome": f"Cliente Nome {i}", "celular": f"55119900{i:04d}",
         "cpf_cnpj": "12345678901", "created_at": f"2026-01-{i:02d}T00:00:00Z"}
        for i in range(1, 6)
    ])
    res = client.get("/api/dashboard/clientes/list?page=1&page_size=2")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


def test_clientes_list_pending_filtra_nome_cliente(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511", "cpf_cnpj": "111"},
        {"id": "c2", "nome": "Cliente", "celular": "5522", "cpf_cnpj": "222"},
        {"id": "c3", "nome": "Bia", "celular": "5533", "cpf_cnpj": ""},
        {"id": "c4", "nome": "cliente", "celular": "5544"},  # lower case também conta
    ])
    res = client.get("/api/dashboard/clientes/list?status=pending")
    assert res.status_code == 200
    ids = {c["id"] for c in res.json()["items"]}
    assert ids == {"c2", "c4"}


def test_clientes_list_complete_filtra_nome_real(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511"},
        {"id": "c2", "nome": "Cliente", "celular": "5522"},
        {"id": "c3", "nome": "Bia", "celular": "5533"},
    ])
    res = client.get("/api/dashboard/clientes/list?status=complete")
    assert res.status_code == 200
    ids = {c["id"] for c in res.json()["items"]}
    assert ids == {"c1", "c3"}


def test_clientes_stats(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana"},
        {"id": "c2", "nome": "Cliente"},
        {"id": "c3", "nome": "Bia"},
    ])
    res = client.get("/api/dashboard/clientes/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert body["complete"] == 2
    assert body["pending"] == 1


# ── PATCH /clientes/{id} (rename) ──────────────────────────────────────────
def test_patch_cliente_renomeia(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].append(
        {"id": "c1", "nome": "Cliente", "celular": "5511"}
    )
    res = client.patch("/api/dashboard/clientes/c1", json={"nome": "  Ana Silva  "})
    assert res.status_code == 200
    assert res.json()["nome"] == "Ana Silva"
    assert fake.tables["clientes"][0]["nome"] == "Ana Silva"


def test_patch_cliente_404_quando_inexistente(fake_client):
    client, _ = fake_client
    res = client.patch("/api/dashboard/clientes/ghost", json={"nome": "Ana"})
    assert res.status_code == 404


def test_patch_cliente_400_quando_nome_vazio(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana"})
    res = client.patch("/api/dashboard/clientes/c1", json={"nome": "   "})
    assert res.status_code == 400
    # nome preservado
    assert fake.tables["clientes"][0]["nome"] == "Ana"


def test_patch_cliente_400_quando_nome_ausente(fake_client):
    client, fake = fake_client
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana"})
    res = client.patch("/api/dashboard/clientes/c1", json={})
    assert res.status_code == 400
