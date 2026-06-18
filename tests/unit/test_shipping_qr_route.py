# tests/unit/test_shipping_qr_route.py
import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake
from app.services.label_token import make_ship_token


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(
        "app.routers.shipping_qr.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake),
    )
    import main as main_module
    return TestClient(main_module.app), fake


def _seed_paid(fake, status="paid"):
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})
    fake.tables["clientes"].append({"id": "c1", "nome": "Maria"})
    fake.tables["vendas"].append({"id": "v1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({"id": "pg1", "venda_id": "v1", "status": status})


def test_qr_marks_shipped(fake_client):
    client, fake = fake_client
    _seed_paid(fake)
    res = client.get(f"/s/{make_ship_token('p1', 'c1')}")
    assert res.status_code == 200
    assert "Enviado" in res.text and "Maria" in res.text
    assert fake.tables["pacote_clientes"][0]["shipped_at"]
    assert fake.tables["pacotes"][0]["shipped_at"]


def test_qr_idempotent(fake_client):
    client, fake = fake_client
    _seed_paid(fake)
    tok = make_ship_token("p1", "c1")
    client.get(f"/s/{tok}")
    res = client.get(f"/s/{tok}")
    assert res.status_code == 200
    assert "Já enviado" in res.text


def test_qr_invalid_token(fake_client):
    client, _ = fake_client
    res = client.get("/s/garbage.sig")
    assert res.status_code == 400
    assert "inválido" in res.text.lower()


def test_qr_not_paid(fake_client):
    client, fake = fake_client
    _seed_paid(fake, status="created")
    res = client.get(f"/s/{make_ship_token('p1', 'c1')}")
    assert res.status_code == 400
    assert "pagou" in res.text.lower()


def test_qr_package_missing(fake_client):
    client, _ = fake_client
    res = client.get(f"/s/{make_ship_token('ghost', 'c1')}")
    assert res.status_code == 400
    assert "não" in res.text.lower()
