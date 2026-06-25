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


def test_get_session_lists_matching_enquetes_with_accumulation(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Lote Bernardo", "status": "open",
                                    "produto_id": "p1", "fornecedor": None})
    fake.tables["enquetes"].append({"id": "e2", "titulo": "Camisa lisa", "status": "open",
                                    "produto_id": "p2", "fornecedor": None})
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana"})
    fake.tables["votos"].append({"id": "v1", "enquete_id": "e1", "cliente_id": "c1",
                                 "qty": 16, "voted_at": "2026-06-24T10:00:00Z", "status": "in"})
    res = client.get("/api/bernardo/sessions/Bernardo")
    assert res.status_code == 200
    body = res.json()
    assert body["session"] == "Bernardo"
    assert len(body["enquetes"]) == 1            # só a e1 casa
    item = body["enquetes"][0]
    assert item["enquete_id"] == "e1"
    assert item["total_qty"] == 16
    assert item["participants"] == [{"nome": "Ana", "qty": 16}]


def test_get_session_404_when_unknown(fake_client):
    client, _ = fake_client
    assert client.get("/api/bernardo/sessions/Inexistente").status_code == 404


def test_post_close_rejects_non_session_enquete(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e2", "titulo": "Camisa lisa", "status": "open"})
    res = client.post("/api/bernardo/sessions/Bernardo/close", json={"enquete_id": "e2"})
    assert res.status_code == 400


def test_post_close_calls_service(fake_client, monkeypatch):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Bernardo", "status": "open"})
    import app.routers.bernardo as bern
    monkeypatch.setattr(bern.PackageService, "close_accumulated",
                        lambda self, eid: {"status": "ok", "pacote_id": "x", "total_qty": 16, "participants": 1})
    res = client.post("/api/bernardo/sessions/Bernardo/close", json={"enquete_id": "e1"})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "pacote_id": "x", "total_qty": 16, "participants": 1}


def test_post_close_400_without_enquete_id(fake_client):
    client, _ = fake_client
    assert client.post("/api/bernardo/sessions/Bernardo/close", json={}).status_code == 400


def test_page_route_serves_html(fake_client):
    client, _ = fake_client
    res = client.get("/bernardo")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


import pytest
from types import SimpleNamespace
from fastapi import HTTPException
from app.routers.bernardo import require_bernardo_access


def _req(role):
    return SimpleNamespace(state=SimpleNamespace(role=role))


def test_guard_allows_admin():
    assert require_bernardo_access(_req("admin")) == "admin"


def test_guard_allows_bernardo():
    assert require_bernardo_access(_req("bernardo")) == "bernardo"


def test_guard_blocks_estoque():
    with pytest.raises(HTTPException) as exc:
        require_bernardo_access(_req("estoque"))
    assert exc.value.status_code == 403
