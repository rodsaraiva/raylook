"""Testes do GET /api/dashboard/packages — incluindo filtro since/until."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers.fake_supabase import FakeSupabaseClient, install_fake


@pytest.fixture
def fake_data():
    """3 pacotes em datas distintas (BRT):
       - pkg-old  (2026-04-01) status=open
       - pkg-mid  (2026-05-01) status=closed
       - pkg-new  (2026-05-11) status=open
    """
    return {
        "pacotes": [
            {"id": "pkg-old", "status": "open", "sequence_no": 1,
             "enquete_id": "enq-1", "capacidade_total": 24, "total_qty": 0,
             "opened_at": "2026-04-01T10:00:00+00:00",
             "created_at": "2026-04-01T13:00:00+00:00",
             "updated_at": "2026-04-01T13:00:00+00:00"},
            {"id": "pkg-mid", "status": "closed", "sequence_no": 2,
             "enquete_id": "enq-1", "capacidade_total": 24, "total_qty": 24,
             "opened_at": "2026-05-01T10:00:00+00:00",
             "closed_at": "2026-05-01T14:00:00+00:00",
             "created_at": "2026-05-01T13:00:00+00:00",
             "updated_at": "2026-05-01T14:00:00+00:00"},
            {"id": "pkg-new", "status": "open", "sequence_no": 3,
             "enquete_id": "enq-1", "capacidade_total": 24, "total_qty": 0,
             "opened_at": "2026-05-11T10:00:00+00:00",
             "created_at": "2026-05-11T13:00:00+00:00",
             "updated_at": "2026-05-11T13:00:00+00:00"},
        ],
        "enquetes": [
            {"id": "enq-1", "produto_id": "prod-1", "titulo": "Camiseta Preta · M/G",
             "external_poll_id": "wa-123", "drive_file_id": "drv-1"},
        ],
        "produtos": [
            {"id": "prod-1", "nome": "Camiseta Preta", "valor_unitario": 80.0,
             "drive_file_id": "drv-1"},
        ],
        "clientes": [
            {"id": "cli-1", "nome": "Ana", "celular": "5511999999999"},
        ],
        "pacote_clientes": [
            {"id": "pc-1", "pacote_id": "pkg-mid", "cliente_id": "cli-1",
             "qty": 24, "total_amount": 1920.0},
        ],
        "vendas": [
            {"id": "v-1", "pacote_id": "pkg-mid", "pacote_cliente_id": "pc-1"},
        ],
        "pagamentos": [],
        "votos": [],
    }


@pytest.fixture
def client_with_fake(fake_data, monkeypatch):
    fake = FakeSupabaseClient(fake_data)
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


def test_list_packages_no_filter_returns_all(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["aberto"] == 2
    assert body["counts"]["fechado"] == 1
    assert body["counts"]["cancelled"] == 0


def test_filter_today_returns_only_new(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages?since=2026-05-11&until=2026-05-11")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["aberto"] == 1
    assert body["counts"]["fechado"] == 0
    ids = [p["id"] for p in body["packages_by_state"]["aberto"]]
    assert ids == ["pkg-new"]


def test_filter_month_returns_mid_and_new(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages?since=2026-05-01&until=2026-05-31")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["aberto"] == 1
    assert body["counts"]["fechado"] == 1


def test_filter_old_window_returns_nothing(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages?since=2026-01-01&until=2026-03-31")
    assert res.status_code == 200
    body = res.json()
    assert sum(body["counts"].values()) == 0


def test_filter_invalid_since_returns_400(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages?since=foo")
    assert res.status_code == 400


def test_filter_since_greater_than_until_returns_400(client_with_fake):
    client, _ = client_with_fake
    res = client.get("/api/dashboard/packages?since=2026-05-11&until=2026-05-01")
    assert res.status_code == 400


def test_response_shape_has_expected_keys(client_with_fake):
    client, _ = client_with_fake
    body = client.get("/api/dashboard/packages").json()
    for key in ("states", "counts", "packages_by_state", "cancelled", "generated_at"):
        assert key in body
    assert body["states"] == [
        "aberto", "fechado", "confirmado", "pago", "pendente", "separado", "enviado",
    ]
