"""Testes das rotas de finanças e admin em main.py (linhas 2579-fim).

Cobre:
  GET  /api/finance/charges
  GET  /api/finance/stats
  POST /api/finance/sync-asaas
  GET  /api/admin/polls/{enquete_id}/whapi-compare
  POST /api/admin/polls/{enquete_id}/resync
  POST /api/admin/polls/resync-all-open
  GET  /api/finance/extract
  GET  /api/finance/queue  (já coberto em test_main_api_finance_queue.py — não duplicar)
  DELETE /api/finance/charges/{charge_id}
  POST /api/finance/charges/{charge_id}/resend  (retorna 410 — coberto em test_main_api_finance_queue.py)
  PATCH /api/finance/charges/{charge_id}/status
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main as main_module


# ---------------------------------------------------------------------------
# Fixture base
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


# ---------------------------------------------------------------------------
# GET /api/finance/charges
# ---------------------------------------------------------------------------

FAKE_CHARGES_PAGE = {
    "items": [{"id": "c1", "status": "paid", "total_amount": 100.0}],
    "total": 1,
    "page": 1,
    "page_size": 50,
    "has_prev": False,
    "has_next": False,
}


def test_get_charges_retorna_envelope_paginado(monkeypatch):
    """Rota deve retornar envelope com 'items', 'total', etc. (F-028 fix)."""
    # main.py importa list_finance_charges_page diretamente — precisa patchar o alias no módulo main
    monkeypatch.setattr(main_module, "list_finance_charges_page", lambda **kw: FAKE_CHARGES_PAGE)
    c = TestClient(main_module.app)
    res = c.get("/api/finance/charges")
    assert res.status_code == 200
    body = res.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] == 1


def test_get_charges_com_filtro_status(monkeypatch):
    """Parâmetros page, page_size e status são repassados à função de serviço."""
    captured: Dict[str, Any] = {}

    def fake_page(**kw):
        captured.update(kw)
        return FAKE_CHARGES_PAGE

    monkeypatch.setattr(main_module, "list_finance_charges_page", fake_page)
    c = TestClient(main_module.app)
    c.get("/api/finance/charges?page=2&page_size=10&status=paid")
    assert captured.get("page") == 2
    assert captured.get("page_size") == 10
    assert captured.get("status") == "paid"


def test_get_charges_page_minimo_1(monkeypatch):
    """page <= 0 deve ser normalizado para 1."""
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(main_module, "list_finance_charges_page", lambda **kw: (captured.update(kw), FAKE_CHARGES_PAGE)[1])
    c = TestClient(main_module.app)
    c.get("/api/finance/charges?page=0")
    assert captured.get("page") == 1


def test_get_charges_page_size_maximo_200(monkeypatch):
    """page_size > 200 deve ser clampado para 200."""
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(main_module, "list_finance_charges_page", lambda **kw: (captured.update(kw), FAKE_CHARGES_PAGE)[1])
    c = TestClient(main_module.app)
    c.get("/api/finance/charges?page_size=999")
    assert captured.get("page_size") == 200


# ---------------------------------------------------------------------------
# GET /api/finance/stats
# ---------------------------------------------------------------------------

FAKE_STATS = {
    "total_paid": 500.0,
    "total_pending": 100.0,
    "total_charges": 5,
    "timeline": {},
}


def test_get_finance_stats_retorna_200(monkeypatch):
    """Endpoint /api/finance/stats delega para get_finance_dashboard_stats."""
    import app.services.finance_service as fs
    monkeypatch.setattr(fs, "get_dashboard_stats", lambda: FAKE_STATS)
    # precisa remontar o alias importado em main
    monkeypatch.setattr(main_module, "get_finance_dashboard_stats", lambda: FAKE_STATS)
    c = TestClient(main_module.app)
    res = c.get("/api/finance/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_paid"] == 500.0
    assert body["total_charges"] == 5


# ---------------------------------------------------------------------------
# POST /api/finance/sync-asaas
# ---------------------------------------------------------------------------

def test_sync_asaas_retorna_success(monkeypatch):
    """Disparo manual do sync Asaas deve retornar {status: 'success', updated: N}."""
    async def fake_sync():
        return 3

    import app.services.asaas_sync_service as svc
    monkeypatch.setattr(svc, "sync_asaas_payments", fake_sync)
    c = TestClient(main_module.app)
    res = c.post("/api/finance/sync-asaas")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["updated"] == 3


def test_sync_asaas_zero_atualizacoes(monkeypatch):
    """Sync sem nada a atualizar deve retornar updated=0."""
    async def fake_sync():
        return 0

    import app.services.asaas_sync_service as svc
    monkeypatch.setattr(svc, "sync_asaas_payments", fake_sync)
    c = TestClient(main_module.app)
    res = c.post("/api/finance/sync-asaas")
    assert res.status_code == 200
    assert res.json()["updated"] == 0


# ---------------------------------------------------------------------------
# GET /api/admin/polls/{enquete_id}/whapi-compare
# ---------------------------------------------------------------------------

def test_whapi_compare_supabase_desabilitado(monkeypatch):
    """Deve retornar 503 quando supabase_domain_enabled() retorna False."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    c = TestClient(main_module.app)
    res = c.get("/api/admin/polls/enq-1/whapi-compare")
    assert res.status_code == 503
    assert "Supabase" in res.json()["detail"]


def test_whapi_compare_retorna_resultado(monkeypatch):
    """Com supabase habilitado, retorna resultado do compare."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_result = {"enquete_id": "enq-1", "diffs": []}

    class FakeService:
        def compare(self, enquete_id: str):
            return fake_result

    import app.services.poll_reconcile_service as prs
    monkeypatch.setattr(prs, "PollReconcileService", FakeService)
    c = TestClient(main_module.app)
    res = c.get("/api/admin/polls/enq-1/whapi-compare")
    assert res.status_code == 200
    assert res.json()["enquete_id"] == "enq-1"


# ---------------------------------------------------------------------------
# POST /api/admin/polls/{enquete_id}/resync
# ---------------------------------------------------------------------------

def test_poll_resync_supabase_desabilitado(monkeypatch):
    """Deve retornar 503 quando Supabase desabilitado."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    c = TestClient(main_module.app)
    res = c.post("/api/admin/polls/enq-1/resync")
    assert res.status_code == 503


def test_poll_resync_retorna_resultado(monkeypatch):
    """Deve chamar PollReconcileService.sync e retornar o resultado."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_result = {"synced": 2, "errors": []}

    class FakeService:
        def sync(self, enquete_id: str):
            return fake_result

    import app.services.poll_reconcile_service as prs
    monkeypatch.setattr(prs, "PollReconcileService", FakeService)
    c = TestClient(main_module.app)
    res = c.post("/api/admin/polls/enq-1/resync")
    assert res.status_code == 200
    assert res.json()["synced"] == 2


# ---------------------------------------------------------------------------
# POST /api/admin/polls/resync-all-open
# ---------------------------------------------------------------------------

def test_poll_resync_all_open_supabase_desabilitado(monkeypatch):
    """Deve retornar 503 quando Supabase desabilitado."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    c = TestClient(main_module.app)
    res = c.post("/api/admin/polls/resync-all-open")
    assert res.status_code == 503


def test_poll_resync_all_open_retorna_resultado(monkeypatch):
    """Deve chamar PollReconcileService.sync_all_open e retornar o resultado."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_result = {"total": 3, "synced": 3}

    class FakeService:
        def sync_all_open(self):
            return fake_result

    import app.services.poll_reconcile_service as prs
    monkeypatch.setattr(prs, "PollReconcileService", FakeService)
    c = TestClient(main_module.app)
    res = c.post("/api/admin/polls/resync-all-open")
    assert res.status_code == 200
    assert res.json()["total"] == 3


# ---------------------------------------------------------------------------
# GET /api/finance/extract
# ---------------------------------------------------------------------------

def test_extract_supabase_desabilitado(monkeypatch):
    """Deve retornar 503 quando Supabase desabilitado."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract")
    assert res.status_code == 503


def test_extract_kind_invalido(monkeypatch):
    """kind diferente de 'paid'/'pending' deve retornar 400."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?kind=invalido")
    assert res.status_code == 400
    assert "kind" in res.json()["detail"].lower()


def test_extract_date_from_invalido(monkeypatch):
    """date_from com formato errado deve retornar 400."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    fake_client.select_all.return_value = []

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?date_from=nao-e-data")
    assert res.status_code == 400
    assert "date_from" in res.json()["detail"].lower()


def test_extract_date_to_invalido(monkeypatch):
    """date_to com formato errado deve retornar 400."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    fake_client.select_all.return_value = []

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?date_to=31-12-2026")
    assert res.status_code == 400
    assert "date_to" in res.json()["detail"].lower()


def test_extract_retorna_envelope_padrao(monkeypatch):
    """Com supabase habilitado e banco vazio, retorna envelope com keys esperadas."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    fake_client.select_all.return_value = []

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?kind=paid")
    assert res.status_code == 200
    body = res.json()
    for key in ("items", "count", "total", "date_from", "date_to", "kind"):
        assert key in body
    assert body["kind"] == "paid"
    assert body["count"] == 0


def test_extract_kind_pending(monkeypatch):
    """kind=pending deve funcionar sem erros."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    fake_client.select_all.return_value = []

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?kind=pending")
    assert res.status_code == 200
    assert res.json()["kind"] == "pending"


def test_extract_combina_pagamentos_e_legacy(monkeypatch):
    """Deve mesclar pagamentos Asaas + legacy_charges na resposta."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_pagamentos = [
        {
            "id": "pag-1",
            "venda_id": "venda-1",
            "paid_at": "2026-01-10T12:00:00+00:00",
            "created_at": "2026-01-01T12:00:00+00:00",
            "status": "paid",
        }
    ]
    fake_vendas = [
        {
            "id": "venda-1",
            "qty": 3,
            "total_amount": 120.0,
            "cliente": {"nome": "João", "celular": "11999990000"},
            "produto": {"nome": "Produto X"},
            "pacote": {"enquete": {"titulo": "Enquete Y"}},
        }
    ]
    fake_legacy = [
        {
            "id": "leg-1",
            "paid_at": "2026-01-05T12:00:00+00:00",
            "created_at": "2026-01-01T12:00:00+00:00",
            "status": "paid",
            "customer_name": "Maria",
            "customer_phone": "11888880000",
            "poll_title": "Enquete Antiga",
            "quantity": 6,
            "total_amount": 200.0,
        }
    ]

    call_count = 0

    def fake_select_all(table, **kwargs):
        nonlocal call_count
        call_count += 1
        if table == "pagamentos":
            return fake_pagamentos
        if table == "vendas":
            return fake_vendas
        if table == "legacy_charges":
            return fake_legacy
        return []

    fake_client = MagicMock()
    fake_client.select_all.side_effect = fake_select_all

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.get("/api/finance/extract?kind=paid&date_from=2026-01-01&date_to=2026-01-31")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    assert body["total"] == pytest.approx(320.0)
    sources = {item["source"] for item in body["items"]}
    assert sources == {"asaas", "legacy"}


# ---------------------------------------------------------------------------
# DELETE /api/finance/charges/{charge_id}
# ---------------------------------------------------------------------------

def test_delete_charge_staging_dry_run(monkeypatch):
    """Em staging dry run, retorna simulação sem deletar do banco."""
    import app.services.staging_dry_run_service as stg
    monkeypatch.setattr(stg, "is_staging_dry_run", lambda: True)
    monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: True)
    monkeypatch.setattr(main_module, "simulate_delete_charge", lambda charges, cid: [])
    monkeypatch.setattr(main_module, "list_finance_charges", lambda: [])
    c = TestClient(main_module.app)
    res = c.delete("/api/finance/charges/c-1")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["simulated"] is True


def test_delete_charge_supabase_nao_encontrado(monkeypatch):
    """Quando cobrança não existe em nenhuma tabela, retorna 404."""
    import app.services.staging_dry_run_service as stg
    monkeypatch.setattr(stg, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    # Nem pagamentos nem legacy_charges encontram o id
    fake_client.select.return_value = []

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: fake_client))
    c = TestClient(main_module.app)
    res = c.delete("/api/finance/charges/nao-existe")
    assert res.status_code == 404


def test_delete_charge_supabase_pagamentos_sucesso(monkeypatch):
    """Cobrança em pagamentos é deletada e cache é invalidado."""
    import app.services.staging_dry_run_service as stg
    monkeypatch.setattr(stg, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    deleted_tables: List[str] = []

    class FakeClient:
        def select(self, table, **kw):
            if table == "pagamentos":
                return [{"id": "pag-1"}]
            return []

        def delete(self, table, **kw):
            deleted_tables.append(table)

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: FakeClient()))

    # Stuba os refreshes pra não falhar
    import app.services.finance_service as fs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    import app.services.customer_service as cs
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)

    c = TestClient(main_module.app)
    res = c.delete("/api/finance/charges/pag-1")
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert "pagamentos" in deleted_tables


def test_delete_charge_supabase_legacy_charges_sucesso(monkeypatch):
    """Cobrança não encontrada em pagamentos mas encontrada em legacy_charges."""
    import app.services.staging_dry_run_service as stg
    monkeypatch.setattr(stg, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    deleted_tables: List[str] = []

    class FakeClient:
        def select(self, table, **kw):
            if table == "legacy_charges":
                return [{"id": "leg-1"}]
            return []

        def delete(self, table, **kw):
            deleted_tables.append(table)

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: FakeClient()))

    import app.services.finance_service as fs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    import app.services.customer_service as cs
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)

    c = TestClient(main_module.app)
    res = c.delete("/api/finance/charges/leg-1")
    assert res.status_code == 200
    assert "legacy_charges" in deleted_tables


# ---------------------------------------------------------------------------
# PATCH /api/finance/charges/{charge_id}/status
# ---------------------------------------------------------------------------

def test_patch_status_sem_body_retorna_400():
    """Sem JSON body deve retornar 400."""
    c = TestClient(main_module.app)
    res = c.patch(
        "/api/finance/charges/c-1/status",
        content=b"nao-e-json",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400
    assert "JSON" in res.json()["detail"]


def test_patch_status_invalido():
    """Status inválido deve retornar 400."""
    c = TestClient(main_module.app)
    res = c.patch("/api/finance/charges/c-1/status", json={"status": "invalido"})
    assert res.status_code == 400


@pytest.mark.parametrize("alias,expected_db", [
    ("paid", "paid"),
    ("pago", "paid"),
    ("pending", "created"),
    ("pendente", "created"),
    ("cancelled", "cancelled"),
    ("cancelado", "cancelled"),
])
def test_patch_status_alias_supabase(monkeypatch, alias, expected_db):
    """Todos os aliases de status devem ser mapeados corretamente para o banco."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    updated_statuses: List[str] = []

    class FakeClient:
        def select(self, table, **kw):
            if table == "pagamentos":
                return [{"id": "c-1"}]
            return []

        def _request(self, method, path, *, payload=None, prefer=None, **kw):
            if payload:
                updated_statuses.append(payload.get("status", ""))
            resp = MagicMock()
            resp.status_code = 204
            return resp

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: FakeClient()))

    import app.services.finance_service as fs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    import app.services.customer_service as cs
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)

    c = TestClient(main_module.app)
    res = c.patch("/api/finance/charges/c-1/status", json={"status": alias})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["new_status"] == expected_db


def test_patch_status_nao_encontrado(monkeypatch):
    """404 quando charge_id não existe em nenhuma tabela."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    class FakeClient:
        def select(self, table, **kw):
            return []

        def _request(self, *args, **kw):
            resp = MagicMock()
            resp.status_code = 200
            return resp

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: FakeClient()))
    c = TestClient(main_module.app)
    res = c.patch("/api/finance/charges/nao-existe/status", json={"status": "paid"})
    assert res.status_code == 404


def test_patch_status_sem_supabase_retorna_501(monkeypatch):
    """Sem Supabase habilitado, endpoint retorna 501 Not Implemented."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    c = TestClient(main_module.app)
    res = c.patch("/api/finance/charges/c-1/status", json={"status": "paid"})
    assert res.status_code == 501


def test_patch_status_legacy_charges_fallback(monkeypatch):
    """Se não encontrar em pagamentos, tenta em legacy_charges."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    updated_in: List[str] = []

    class FakeClient:
        def select(self, table, **kw):
            # Não encontra em pagamentos, encontra em legacy_charges
            if table == "legacy_charges":
                return [{"id": "leg-2"}]
            return []

        def _request(self, method, path, *, payload=None, prefer=None, **kw):
            updated_in.append(path)
            resp = MagicMock()
            resp.status_code = 200
            return resp

    import app.services.supabase_service as ss
    monkeypatch.setattr(ss.SupabaseRestClient, "from_settings", staticmethod(lambda: FakeClient()))

    import app.services.finance_service as fs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    import app.services.customer_service as cs
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)

    c = TestClient(main_module.app)
    res = c.patch("/api/finance/charges/leg-2/status", json={"status": "paid"})
    assert res.status_code == 200
    assert any("legacy_charges" in p for p in updated_in)
