"""Testes dos endpoints /api/finance/* novos."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.services.finance_service as finance_service


@pytest.fixture
def client_fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    import main as main_module
    return TestClient(main_module.app), f


def test_get_receivables_returns_list(client_fake):
    client, fake = client_fake
    fake.tables["clientes"].append({"id": "c1", "nome": "A", "celular": "5511999990001"})
    fake.tables["vendas"].append({"id": "v1", "cliente_id": "c1", "pacote_id": "p1",
                                   "total_amount": 100.0})
    fake.tables["pacotes"].append({"id": "p1", "enquete_id": "e1"})
    fake.tables["enquetes"].append({"id": "e1", "titulo": "E"})
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
    })
    res = client.get("/api/finance/receivables")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["cliente_id"] == "c1"


def test_get_aging_summary(client_fake):
    client, fake = client_fake
    res = client.get("/api/finance/aging-summary")
    assert res.status_code == 200
    body = res.json()
    assert "total_receivable" in body
    assert "buckets" in body
    assert set(body["buckets"].keys()) == {"0-7", "8-15", "16-30", "30+"}


def test_write_off_marks_payment(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({"id": "pg1", "status": "sent", "venda_id": "v1"})
    res = client.post(
        "/api/finance/pagamentos/pg1/write-off",
        json={"reason": "Cliente sumiu"},
    )
    assert res.status_code == 200
    assert fake.tables["pagamentos"][0]["status"] == "written_off"


def test_write_off_404_for_unknown(client_fake):
    client, _ = client_fake
    res = client.post(
        "/api/finance/pagamentos/ghost/write-off",
        json={"reason": "x"},
    )
    assert res.status_code == 404


def test_write_off_400_when_reason_empty(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({"id": "pg1", "status": "sent", "venda_id": "v1"})
    res = client.post(
        "/api/finance/pagamentos/pg1/write-off",
        json={"reason": "  "},
    )
    assert res.status_code == 400


def test_history_returns_timeline(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-01T10:00:00+00:00",
    })
    res = client.get("/api/finance/pagamentos/pg1/history")
    assert res.status_code == 200
    events = res.json()
    assert any(e["kind"] == "package_confirmed" for e in events)
