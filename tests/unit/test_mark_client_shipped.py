from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.routers.dashboard as dash


def test_mark_client_shipped_sets_and_propagates():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})

    changed = dash._mark_client_shipped(
        fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr"
    )
    assert changed is True
    pc = fake.tables["pacote_clientes"][0]
    assert pc["shipped_at"] and pc["pdf_sent_at"] and pc["payment_validated_at"]
    # único cliente do pacote → pkg vira enviado
    assert fake.tables["pacotes"][0]["shipped_at"]
    assert fake.tables["pacotes"][0]["shipped_by"] == "qr"


def test_mark_client_shipped_idempotent():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})
    dash._mark_client_shipped(fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr")
    changed = dash._mark_client_shipped(
        fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr"
    )
    assert changed is False


def test_mark_client_shipped_partial_keeps_pkg_unshipped():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].extend([
        {"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"},
        {"id": "pc2", "pacote_id": "p1", "cliente_id": "c2"},
    ])
    dash._mark_client_shipped(fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr")
    # só 1 de 2 enviado → pkg NÃO vira enviado
    assert not fake.tables["pacotes"][0].get("shipped_at")
