import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


def _seed_separado(fake):
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "pdf_sent_at": "2026-01-01T00:00:00Z",
        "enquete_id": "e1", "friendly_id": "R-9",
    })
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Blusas"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 3})
    fake.tables["clientes"].append({"id": "c1", "nome": "Maria", "celular": "5562999990000"})


def test_etiqueta_termica_returns_pdf(fake_client):
    client, fake = fake_client
    _seed_separado(fake)
    res = client.get("/api/dashboard/packages/p1/etiqueta.pdf?fmt=termica&w=60&h=40")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:4] == b"%PDF"


def test_etiqueta_a4_default_still_works(fake_client):
    client, fake = fake_client
    _seed_separado(fake)
    res = client.get("/api/dashboard/packages/p1/etiqueta.pdf")
    assert res.status_code == 200
    assert res.content[:4] == b"%PDF"
