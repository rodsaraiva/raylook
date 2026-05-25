"""Testes do gate de fornecedor obrigatório no fechado→confirmado.

Comportamento:
- Sem PACOTE_REQUER_FORNECEDOR_DESDE → comportamento legado (não exige).
- Com cutoff e closed_at < cutoff → não exige (pacotes anteriores ao deploy).
- Com cutoff e closed_at >= cutoff sem body.fornecedor → 400 fornecedor_required.
- Com fornecedor no body → grava em pacotes.fornecedor + enquetes.fornecedor.
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


def _setup_closed_pkg(fake, closed_at="2026-05-25T10:00:00+00:00", fornecedor=None):
    fake.tables["pacotes"].append({
        "id": "p1", "status": "closed", "enquete_id": "e1",
        "closed_at": closed_at, "fornecedor": fornecedor,
    })
    fake.tables["enquetes"].append({
        "id": "e1", "produto_id": "prod-1", "fornecedor": None,
    })
    fake.tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "p1", "cliente_id": "cli-1",
        "qty": 24, "unit_price": 80.0, "subtotal": 1920.0,
        "commission_percent": 13.0, "commission_amount": 249.60,
        "total_amount": 2169.60,
    })


def test_advance_sem_cutoff_nao_exige_fornecedor(fake_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.PACOTE_REQUER_FORNECEDOR_DESDE", None, raising=False)
    client, fake = fake_client
    _setup_closed_pkg(fake)
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "confirmado"


def test_advance_pacote_antigo_passa_sem_fornecedor(fake_client, monkeypatch):
    """closed_at < cutoff → legado, não exige fornecedor."""
    monkeypatch.setattr(
        "app.config.settings.PACOTE_REQUER_FORNECEDOR_DESDE",
        "2026-06-01T00:00:00+00:00",
        raising=False,
    )
    client, fake = fake_client
    _setup_closed_pkg(fake, closed_at="2026-05-10T10:00:00+00:00")  # antes do cutoff
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200
    assert res.json()["new_state"] == "confirmado"


def test_advance_pacote_novo_sem_fornecedor_retorna_400(fake_client, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.PACOTE_REQUER_FORNECEDOR_DESDE",
        "2026-05-01T00:00:00+00:00",
        raising=False,
    )
    client, fake = fake_client
    _setup_closed_pkg(fake, closed_at="2026-05-25T10:00:00+00:00")
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 400
    detail = res.json()["detail"]
    # detail vem como dict (FastAPI serializa)
    assert isinstance(detail, dict)
    assert detail.get("code") == "fornecedor_required"
    # Pacote NÃO avançou
    assert fake.tables["pacotes"][0]["status"] == "closed"


def test_advance_pacote_novo_com_fornecedor_avanca(fake_client, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.PACOTE_REQUER_FORNECEDOR_DESDE",
        "2026-05-01T00:00:00+00:00",
        raising=False,
    )
    client, fake = fake_client
    _setup_closed_pkg(fake, closed_at="2026-05-25T10:00:00+00:00")
    res = client.post("/api/dashboard/packages/p1/advance",
                      json={"fornecedor": "Fornecedor X"})
    assert res.status_code == 200, res.json()
    assert res.json()["new_state"] == "confirmado"
    pkg = fake.tables["pacotes"][0]
    assert pkg["fornecedor"] == "Fornecedor X"
    assert pkg["status"] == "approved"
    # Enquete também recebe (estava NULL)
    assert fake.tables["enquetes"][0]["fornecedor"] == "Fornecedor X"


def test_advance_pkg_ja_tem_fornecedor_dispensa_body(fake_client, monkeypatch):
    """Idempotência: se pkg.fornecedor já está setado (herdado da enquete),
    body vazio passa sem 400."""
    monkeypatch.setattr(
        "app.config.settings.PACOTE_REQUER_FORNECEDOR_DESDE",
        "2026-05-01T00:00:00+00:00",
        raising=False,
    )
    client, fake = fake_client
    _setup_closed_pkg(fake, closed_at="2026-05-25T10:00:00+00:00", fornecedor="Já Setado")
    res = client.post("/api/dashboard/packages/p1/advance")
    assert res.status_code == 200, res.json()
    assert res.json()["new_state"] == "confirmado"


def test_endpoint_fornecedores_retorna_distinct_ordenado(fake_client, monkeypatch):
    """GET /api/enquetes/fornecedores devolve lista única ordenada (case-insensitive)."""
    # Endpoint depende de supabase_domain_enabled; em sandbox a flag fica False
    # por padrão, então o endpoint retorna vazio. Pra teste, força True.
    monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled",
                        lambda: True)
    import main as main_module
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    client, fake = fake_client
    fake.tables["enquetes"].extend([
        {"id": "e1", "fornecedor": "Beta"},
        {"id": "e2", "fornecedor": "alpha"},
        {"id": "e3", "fornecedor": "Beta"},        # duplicado (case)
        {"id": "e4", "fornecedor": "BETA"},        # duplicado (case)
        {"id": "e5", "fornecedor": ""},            # vazio ignorado
        {"id": "e6", "fornecedor": None},          # null ignorado
        {"id": "e7", "fornecedor": "  charlie  "}, # trim
    ])
    res = client.get("/api/enquetes/fornecedores")
    assert res.status_code == 200
    items = res.json()["items"]
    lowered = [i.lower() for i in items]
    assert lowered == ["alpha", "beta", "charlie"]
