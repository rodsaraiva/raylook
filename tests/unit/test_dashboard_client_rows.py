"""Testes da granularidade por cliente em Separado/Enviado.

As seções Separado e Enviado do dashboard devolvem cliente-rows (uma linha
por pacote_cliente), não mais o pacote agregado. Um pacote parcialmente
enviado aparece em ambas as seções com linhas distintas.
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


def _setup_approved_pkg_with_two_clients(fake):
    """Pacote aprovado, 2 clientes pagaram, pdf gerado (estado base 'separado')."""
    fake.tables["pacotes"].append({
        "id": "pkg-1", "status": "approved", "enquete_id": "enq-1",
        "sequence_no": 7, "friendly_id": "PAC007/2505",
        "capacidade_total": 24, "total_qty": 24,
        "approved_at": "2026-05-20T10:00:00+00:00",
        "payment_validated_at": "2026-05-20T11:00:00+00:00",
        "pdf_sent_at": "2026-05-20T12:00:00+00:00",
        "updated_at": "2026-05-20T12:00:00+00:00",
    })
    fake.tables["enquetes"].append({
        "id": "enq-1", "produto_id": "prod-1", "titulo": "Camiseta",
        "external_poll_id": "wa-1",
    })
    fake.tables["produtos"].append({
        "id": "prod-1", "nome": "Camiseta", "valor_unitario": 80.0,
    })
    fake.tables["clientes"].extend([
        {"id": "cli-A", "nome": "Ana", "celular": "11999999991"},
        {"id": "cli-B", "nome": "Bia", "celular": "11999999992"},
    ])
    fake.tables["pacote_clientes"].extend([
        {"id": "pc-A", "pacote_id": "pkg-1", "cliente_id": "cli-A",
         "qty": 12, "subtotal": 960.0, "total_amount": 1084.80,
         "pdf_sent_at": "2026-05-20T12:00:00+00:00", "shipped_at": None},
        {"id": "pc-B", "pacote_id": "pkg-1", "cliente_id": "cli-B",
         "qty": 12, "subtotal": 960.0, "total_amount": 1084.80,
         "pdf_sent_at": "2026-05-20T12:00:00+00:00", "shipped_at": None},
    ])
    fake.tables["vendas"].extend([
        {"id": "v-A", "pacote_id": "pkg-1", "pacote_cliente_id": "pc-A",
         "cliente_id": "cli-A", "qty": 12, "unit_price": 80.0,
         "subtotal": 960.0, "total_amount": 1084.80,
         "commission_percent": 13.0, "commission_amount": 124.80,
         "status": "approved"},
        {"id": "v-B", "pacote_id": "pkg-1", "pacote_cliente_id": "pc-B",
         "cliente_id": "cli-B", "qty": 12, "unit_price": 80.0,
         "subtotal": 960.0, "total_amount": 1084.80,
         "commission_percent": 13.0, "commission_amount": 124.80,
         "status": "approved"},
    ])
    fake.tables["pagamentos"].extend([
        {"id": "pg-A", "venda_id": "v-A", "status": "paid", "paid_at": "2026-05-20T09:00:00+00:00"},
        {"id": "pg-B", "venda_id": "v-B", "status": "paid", "paid_at": "2026-05-20T09:00:00+00:00"},
    ])


def test_separado_returns_one_row_per_client(fake_client):
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)

    body = client.get("/api/dashboard/packages").json()
    sep = body["packages_by_state"]["separado"]
    assert body["counts"]["separado"] == 2
    assert {r["cliente_id"] for r in sep} == {"cli-A", "cli-B"}
    for row in sep:
        assert row["type"] == "client_row"
        assert row["pacote_id"] == "pkg-1"
        assert row["pacote_friendly_id"] == "PAC007/2505"
        assert row["state"] == "separado"
        assert row["qty"] == 12


def test_partially_shipped_appears_in_both_sections(fake_client):
    """Cliente A enviado, B ainda separado → linha em cada seção."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    # cli-A foi enviado; pkg.shipped_at AINDA não setado (não é o último).
    for pc in fake.tables["pacote_clientes"]:
        if pc["id"] == "pc-A":
            pc["shipped_at"] = "2026-05-21T10:00:00+00:00"

    body = client.get("/api/dashboard/packages").json()
    sep = body["packages_by_state"]["separado"]
    env = body["packages_by_state"]["enviado"]
    assert [r["cliente_id"] for r in sep] == ["cli-B"]
    assert [r["cliente_id"] for r in env] == ["cli-A"]


def test_all_shipped_then_pkg_is_enviado(fake_client):
    """Todos pc.shipped_at setados → pacote no estado agregado 'enviado',
    todas as linhas aparecem na seção Enviado."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    for pc in fake.tables["pacote_clientes"]:
        pc["shipped_at"] = "2026-05-21T10:00:00+00:00"

    body = client.get("/api/dashboard/packages").json()
    assert body["counts"]["separado"] == 0
    assert body["counts"]["enviado"] == 2
    env_ids = {r["cliente_id"] for r in body["packages_by_state"]["enviado"]}
    assert env_ids == {"cli-A", "cli-B"}


def test_advance_client_to_enviado_propagates_to_pkg_on_last(fake_client):
    """Avançar último cliente sem shipped_at → seta pkg.shipped_at também."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    # cli-A já enviado
    for pc in fake.tables["pacote_clientes"]:
        if pc["id"] == "pc-A":
            pc["shipped_at"] = "2026-05-21T10:00:00+00:00"

    res = client.post("/api/dashboard/packages/pkg-1/clients/cli-B/advance?to=enviado")
    assert res.status_code == 200, res.json()
    # pkg.shipped_at agora deve estar setado
    pkg = fake.tables["pacotes"][0]
    assert pkg["shipped_at"] is not None
    # pc-B também
    pc_b = next(pc for pc in fake.tables["pacote_clientes"] if pc["id"] == "pc-B")
    assert pc_b["shipped_at"] is not None


def test_advance_client_to_enviado_no_propagation_when_not_last(fake_client):
    """Avançar A com B ainda separado → pkg.shipped_at fica NULL."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)

    res = client.post("/api/dashboard/packages/pkg-1/clients/cli-A/advance?to=enviado")
    assert res.status_code == 200, res.json()
    pkg = fake.tables["pacotes"][0]
    assert pkg.get("shipped_at") in (None, "")


def test_advance_pkg_separado_to_enviado_marks_all_pcs(fake_client):
    """Atalho: advance no pacote inteiro (separado→enviado) marca todos pcs."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)

    res = client.post("/api/dashboard/packages/pkg-1/advance?to=enviado")
    assert res.status_code == 200, res.json()
    pkg = fake.tables["pacotes"][0]
    assert pkg["shipped_at"] is not None
    for pc in fake.tables["pacote_clientes"]:
        assert pc["shipped_at"] is not None


def test_regress_enviado_zeroes_pcs(fake_client):
    """Regress pacote enviado→separado zera pkg.shipped_at e todos pc.shipped_at."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    fake.tables["pacotes"][0]["shipped_at"] = "2026-05-21T10:00:00+00:00"
    for pc in fake.tables["pacote_clientes"]:
        pc["shipped_at"] = "2026-05-21T10:00:00+00:00"

    res = client.post("/api/dashboard/packages/pkg-1/regress")
    assert res.status_code == 200, res.json()
    assert res.json()["new_state"] == "separado"
    assert fake.tables["pacotes"][0].get("shipped_at") in (None, "")
    for pc in fake.tables["pacote_clientes"]:
        assert pc.get("shipped_at") in (None, "")


def test_backfill_fallback_pkg_shipped_at_without_pc_shipped_at(fake_client):
    """Pacote legado: pkg.shipped_at setado, pc.shipped_at NULL.
    Linhas devem aparecer em 'enviado' graças ao fallback."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    fake.tables["pacotes"][0]["shipped_at"] = "2026-05-21T10:00:00+00:00"
    # pc.shipped_at continua NULL — backfill ainda não rodou
    body = client.get("/api/dashboard/packages").json()
    assert body["counts"]["enviado"] == 2
    assert body["counts"]["separado"] == 0
