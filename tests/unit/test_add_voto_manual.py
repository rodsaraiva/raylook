"""Testes para POST /api/dashboard/enquetes/{enquete_id}/votos."""
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import main as main_module
from tests._helpers.fake_supabase import FakeSupabaseClient, install_fake


def _client_strict():
    return TestClient(main_module.app)


def _client():
    return TestClient(main_module.app, raise_server_exceptions=False)


ENQ_ID = "enq-001"
CLI_ID = "cli-001"

_BASE_ENQUETE = {
    "id": ENQ_ID, "titulo": "Short Saia", "status": "open",
    "produto_id": None, "created_at": "2026-06-02T10:00:00+00:00",
}

_BASE_CLIENTE = {
    "id": CLI_ID, "nome": "Maria Silva", "celular": "62999991234",
}


def test_add_voto_manual_cliente_existente_por_nome(monkeypatch):
    """POST /enquetes/{id}/votos com cliente encontrado pelo nome cria voto synthetic=1."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [_BASE_CLIENTE],
        "votos": [],
        "enquete_alternativas": [],
        "pacotes": [],
        "pacote_clientes": [],
        "vendas": [],
        "pagamentos": [],
    })
    install_fake(monkeypatch, fake)

    with patch("app.services.whatsapp_domain_service.PackageService.rebuild_for_poll", return_value={"ok": True}):
        resp = _client_strict().post(
            f"/api/dashboard/enquetes/{ENQ_ID}/votos",
            json={"busca": "maria", "qty": 6},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cliente"]["id"] == CLI_ID

    votos = fake.tables["votos"]
    assert len(votos) == 1
    assert votos[0]["qty"] == 6
    assert votos[0]["status"] == "in"
    assert votos[0]["synthetic"] == 1
    assert votos[0]["enquete_id"] == ENQ_ID
    assert votos[0]["cliente_id"] == CLI_ID


def test_add_voto_manual_cria_cliente(monkeypatch):
    """POST /enquetes/{id}/votos com cliente inexistente e nome+celular cria cliente e voto."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [],
        "votos": [],
        "enquete_alternativas": [],
        "pacotes": [],
        "pacote_clientes": [],
        "vendas": [],
        "pagamentos": [],
    })
    install_fake(monkeypatch, fake)

    with patch("app.services.whatsapp_domain_service.PackageService.rebuild_for_poll", return_value={"ok": True}):
        resp = _client_strict().post(
            f"/api/dashboard/enquetes/{ENQ_ID}/votos",
            json={"busca": "Ana Paula", "qty": 6, "nome": "Ana Paula", "celular": "62988887777"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cliente"]["nome"] == "Ana Paula"

    clientes = fake.tables["clientes"]
    assert len(clientes) == 1
    assert clientes[0]["nome"] == "Ana Paula"

    votos = fake.tables["votos"]
    assert len(votos) == 1
    assert votos[0]["synthetic"] == 1
    assert votos[0]["status"] == "in"


def test_add_voto_manual_nao_encontrado_sem_dados(monkeypatch):
    """POST /enquetes/{id}/votos sem cliente e sem nome/celular retorna found=False."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [],
        "votos": [],
    })
    install_fake(monkeypatch, fake)

    resp = _client_strict().post(
        f"/api/dashboard/enquetes/{ENQ_ID}/votos",
        json={"busca": "nao existe", "qty": 6},
    )

    assert resp.status_code == 200
    assert resp.json() == {"found": False}
    assert fake.tables.get("votos", []) == []


def test_add_voto_manual_qty_invalida(monkeypatch):
    """qty fora dos valores permitidos retorna 400."""
    fake = FakeSupabaseClient({"enquetes": [_BASE_ENQUETE], "clientes": []})
    install_fake(monkeypatch, fake)

    resp = _client().post(
        f"/api/dashboard/enquetes/{ENQ_ID}/votos",
        json={"busca": "maria", "qty": 7},
    )
    assert resp.status_code == 400


def test_add_voto_manual_enquete_nao_encontrada(monkeypatch):
    """enquete_id inexistente retorna 404."""
    fake = FakeSupabaseClient({"enquetes": [], "clientes": []})
    install_fake(monkeypatch, fake)

    resp = _client().post(
        "/api/dashboard/enquetes/nao-existe/votos",
        json={"busca": "maria", "qty": 6},
    )
    assert resp.status_code == 404
