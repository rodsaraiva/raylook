"""Testes para o bloco intermediário de main.py (~linhas 1000–2580).

Cobre endpoints que NÃO tinham testes anteriormente:
  - GET /api/polls/recent
  - POST /api/packages/manual/preview
  - POST /api/packages/manual/confirm
  - GET /api/packages/{pkg_id}/pdf
  - GET /api/inventory/packages
  - POST /api/enquetes/{poll_id}/fornecedor
  - POST /api/packages/{pkg_id}/tag
  - POST /api/packages/{pkg_id}/revert  (comportamento 403)
  - POST /api/packages/{pkg_id}/cancel
  - POST /api/packages/{pkg_id}/retry_payments
  - POST /api/packages/backfill-routing
  - GET /api/packages/{pkg_id}/edit-data
  - PATCH /api/packages/{pkg_id}/edit
  - POST /api/packages/{pkg_id}/update-confirmed
  - GET /api/packages/{pkg_id}/edit-data-closed
  - POST /api/packages/{pkg_id}/update-closed

Endpoints já cobertos em outros arquivos (não duplicados):
  - GET /api/metrics → test_main_api_metrics.py
  - POST /api/refresh → test_main_api_refresh.py
  - POST /api/packages/{id}/confirm → test_main_api_packages.py
  - POST /api/packages/{id}/reject  → test_main_api_packages.py
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import main as main_module


# ---------------------------------------------------------------------------
# Helpers reutilizáveis
# ---------------------------------------------------------------------------

def _empty_metrics():
    return {
        "generated_at": "2026-01-01T00:00:00",
        "enquetes": {"today": 0},
        "votos": {
            "today": 0,
            "packages": {
                "open": [],
                "closed_today": [],
                "closed_week": [],
                "confirmed_today": [],
            },
        },
    }


def _metrics_with_confirmed(pkg_id: str):
    m = _empty_metrics()
    m["votos"]["packages"]["confirmed_today"] = [
        {
            "id": pkg_id,
            "poll_title": "Poll X",
            "qty": 24,
            "confirmed_at": "2026-01-01T12:00:00",
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 24}],
        }
    ]
    return m


# ---------------------------------------------------------------------------
# GET /api/polls/recent
# ---------------------------------------------------------------------------

class TestGetRecentPolls:
    """Testes para GET /api/polls/recent."""

    def test_retorna_lista_vazia_sem_baserow_configurado(self, monkeypatch):
        """Quando sem Supabase e sem tabela Baserow configurada, deve retornar vazio."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module.settings, "BASEROW_TABLE_ENQUETES", "")
        import os
        monkeypatch.delenv("BASEROW_TABLE_ENQUETES", raising=False)

        # stub fetch_rows_filtered para retornar lista vazia
        import metrics.clients as clients_mod
        monkeypatch.setattr(clients_mod, "fetch_rows_filtered", lambda *a, **kw: [])

        client = TestClient(main_module.app)
        response = client.get("/api/polls/recent")

        assert response.status_code == 200
        body = response.json()
        assert "polls" in body
        assert isinstance(body["polls"], list)
        assert body["total"] == 0

    def test_paginacao_limit_e_offset(self, monkeypatch):
        """Parâmetros limit e offset devem ser refletidos na resposta."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module.settings, "BASEROW_TABLE_ENQUETES", "")
        import os
        monkeypatch.delenv("BASEROW_TABLE_ENQUETES", raising=False)

        import metrics.clients as clients_mod
        monkeypatch.setattr(clients_mod, "fetch_rows_filtered", lambda *a, **kw: [])

        client = TestClient(main_module.app)
        response = client.get("/api/polls/recent?limit=5&offset=10")

        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 5
        assert body["offset"] == 10

    def test_erro_503_quando_supabase_falha(self, monkeypatch):
        """Se Supabase habilitado e falha na consulta, deve retornar 503."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        class _FakeSB:
            def select_all(self, *a, **kw):
                raise RuntimeError("conexão recusada")

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        client = TestClient(main_module.app, raise_server_exceptions=False)
        response = client.get("/api/polls/recent")

        assert response.status_code == 503

    def test_supabase_retorna_enquetes_recentes(self, monkeypatch):
        """Com Supabase ativo e enquetes retornadas, deve montar lista corretamente."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        from datetime import datetime, timedelta
        recente = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        class _FakeSB:
            def select_all(self, *a, **kw):
                return [
                    {
                        "id": "uuid-1",
                        "external_poll_id": "poll123",
                        "titulo": "Blusa Rosa",
                        "created_at_provider": recente,
                        "drive_file_id": None,
                        "produto": None,
                    }
                ]

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        # Mocka thumbnail para não tentar acessar Drive
        import main as m
        monkeypatch.setattr(m, "ensure_thumbnail_for_image_url", lambda url: None)

        client = TestClient(main_module.app)
        response = client.get("/api/polls/recent")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["polls"][0]["pollId"] == "poll123"
        assert body["polls"][0]["title"] == "Blusa Rosa"


# ---------------------------------------------------------------------------
# POST /api/packages/manual/preview
# ---------------------------------------------------------------------------

class TestManualPackagePreview:
    """Testes para POST /api/packages/manual/preview."""

    def _valid_body(self):
        # 24 peças distribuídas entre dois votos
        return {
            "pollId": "poll_abc",
            "votes": [
                {"qty": 12, "phone": "5511999990001"},
                {"qty": 12, "phone": "5511999990002"},
            ],
        }

    def test_preview_sucesso(self, monkeypatch):
        """Happy path: retorna preview quando total é 24 peças."""
        monkeypatch.setattr(
            main_module,
            "build_preview_payload",
            lambda poll_id, votes: {"poll_id": poll_id, "total": 24, "voters": []},
        )

        client = TestClient(main_module.app)
        response = client.post("/api/packages/manual/preview", json=self._valid_body())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "preview" in body

    def test_preview_rejeita_total_diferente_de_24(self, monkeypatch):
        """Retorna 400 quando soma das quantidades não é 24."""
        client = TestClient(main_module.app)
        payload = {
            "pollId": "poll_abc",
            "votes": [
                {"qty": 12, "phone": "5511999990001"},
            ],
        }
        response = client.post("/api/packages/manual/preview", json=payload)

        assert response.status_code == 400
        assert "24" in response.json()["detail"]

    def test_preview_retorna_404_quando_poll_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando build_preview_payload levanta ValueError (poll não existe)."""
        monkeypatch.setattr(
            main_module,
            "build_preview_payload",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("Poll não encontrado")),
        )

        client = TestClient(main_module.app)
        response = client.post("/api/packages/manual/preview", json=self._valid_body())

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/packages/manual/confirm
# ---------------------------------------------------------------------------

class TestManualPackageConfirm:
    """Testes para POST /api/packages/manual/confirm."""

    def _valid_body(self):
        return {
            "pollId": "poll_abc",
            "votes": [
                {"qty": 12, "phone": "5511999990001"},
                {"qty": 12, "phone": "5511999990002"},
            ],
        }

    def test_confirm_rejeita_total_diferente_de_24(self, monkeypatch):
        """Retorna 400 quando soma das quantidades não é 24."""
        client = TestClient(main_module.app)
        payload = {
            "pollId": "poll_abc",
            "votes": [{"qty": 6, "phone": "5511999990001"}],
        }
        response = client.post("/api/packages/manual/confirm", json=payload)

        assert response.status_code == 400

    def test_confirm_sucesso_legacy_path(self, monkeypatch):
        """Happy path no modo legacy (sem supabase, sem staging): retorna confirmed_packages."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        fake_pkg = {
            "id": "poll_abc_1",
            "poll_title": "Blusa Rosa",
            "qty": 24,
            "votes": [],
        }
        monkeypatch.setattr(main_module, "build_manual_confirmed_package", lambda *a: fake_pkg)

        async def _fake_effects(pkg, pkg_id, metrics_data_to_save):
            pass

        monkeypatch.setattr(main_module, "run_post_confirmation_effects", _fake_effects)

        from app.services import metrics_service
        monkeypatch.setattr(metrics_service, "load_metrics", lambda: _empty_metrics())

        client = TestClient(main_module.app)
        response = client.post("/api/packages/manual/confirm", json=self._valid_body())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["moved"]["to"] == "confirmed_packages"

    def test_confirm_retorna_404_quando_poll_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando build_manual_confirmed_package levanta ValueError."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        def _raise(*a, **kw):
            raise ValueError("Poll não encontrado")

        monkeypatch.setattr(main_module, "build_manual_confirmed_package", _raise)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/manual/confirm", json=self._valid_body())

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/packages/{pkg_id}/pdf
# ---------------------------------------------------------------------------

class TestDownloadPackagePdf:
    """Testes para GET /api/packages/{pkg_id}/pdf."""

    def _setup_pdf_mocks(self, monkeypatch):
        """Configura sys.modules para mockar módulos com dependências ausentes."""
        import sys
        import types

        # Mocka xhtml2pdf pra que estoque.pdf_builder possa ser importado
        fake_xhtml2pdf = types.ModuleType("xhtml2pdf")
        fake_pisa = types.ModuleType("xhtml2pdf.pisa")
        monkeypatch.setitem(sys.modules, "xhtml2pdf", fake_xhtml2pdf)
        monkeypatch.setitem(sys.modules, "xhtml2pdf.pisa", fake_pisa)

        # Remove cached import pra forçar reimport com mock
        monkeypatch.delitem(sys.modules, "estoque.pdf_builder", raising=False)

        # Cria módulo fake de estoque.pdf_builder
        fake_pdf_builder = types.ModuleType("estoque.pdf_builder")
        fake_pdf_builder.build_pdf = lambda pkg, commission: b"%PDF-1.4 fake"
        monkeypatch.setitem(sys.modules, "estoque.pdf_builder", fake_pdf_builder)

    def test_retorna_pdf_bytes_quando_pacote_encontrado(self, monkeypatch):
        """Happy path: pacote em confirmed_packages → PDF gerado e retornado."""
        self._setup_pdf_mocks(monkeypatch)

        pkg_id = "poll_abc_1"
        fake_pkg = {
            "id": pkg_id,
            "poll_title": "Calça Jeans",
            "qty": 24,
            "votes": [{"name": "Ana", "phone": "5511999990001", "qty": 24}],
        }

        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)

        from app.services import package_state_service as pss
        monkeypatch.setattr(pss, "load_package_states", lambda: {})

        import finance.utils as fu
        monkeypatch.setattr(fu, "get_pdf_filename_by_id", lambda pid: f"{pid}.pdf")

        client = TestClient(main_module.app)
        response = client.get(f"/api/packages/{pkg_id}/pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert b"%PDF" in response.content

    def test_retorna_404_quando_pacote_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote não está em confirmed_packages nem em Supabase."""
        self._setup_pdf_mocks(monkeypatch)

        pkg_id = "poll_nao_existe_1"

        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: None)

        client = TestClient(main_module.app, raise_server_exceptions=False)
        response = client.get(f"/api/packages/{pkg_id}/pdf")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/inventory/packages
# ---------------------------------------------------------------------------

class TestInventoryPackages:
    """Testes para GET /api/inventory/packages."""

    def test_retorna_503_quando_supabase_desabilitado(self, monkeypatch):
        """Retorna 503 quando Supabase está desabilitado."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)

        client = TestClient(main_module.app)
        response = client.get("/api/inventory/packages")

        assert response.status_code == 503

    def test_retorna_400_quando_data_invalida(self, monkeypatch):
        """Retorna 400 quando start tem formato inválido."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        class _FakeSB:
            def select_all(self, *a, **kw):
                return []

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        client = TestClient(main_module.app)
        response = client.get("/api/inventory/packages?start=nao-e-data")

        assert response.status_code == 400

    def test_retorna_lista_vazia_sem_pacotes(self, monkeypatch):
        """Happy path: sem pacotes no período retorna items vazio."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        class _FakeSB:
            def select_all(self, *a, **kw):
                return []

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        client = TestClient(main_module.app)
        response = client.get("/api/inventory/packages?start=2026-01-01&end=2026-01-31")

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert "summary" in body

    def test_filtra_por_status_approved(self, monkeypatch):
        """Com status=approved, retorna só pacotes aprovados."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        fake_pkg = {
            "id": "uuid-1",
            "status": "approved",
            "approved_at": "2026-01-15T10:00:00Z",
            "cancelled_at": None,
            "total_qty": 24,
            "participants_count": 3,
            "tag": "blusa",
            "fornecedor": "Forn A",
            "custom_title": None,
            "pdf_status": None,
            "pdf_file_name": None,
            "pdf_sent_at": None,
            "confirmed_by": None,
            "cancelled_by": None,
            "sequence_no": 1,
            "opened_at": None,
            "closed_at": None,
            "updated_at": None,
            "enquete": {
                "id": "enq-1",
                "external_poll_id": "poll_abc",
                "titulo": "Calça PMG",
                "chat_id": "chat_1",
                "drive_file_id": None,
                "produto": {"nome": "Calça", "drive_file_id": None},
            },
        }

        class _FakeSB:
            def select_all(self, table, columns="", filters=None, order=None):
                # retorna o pacote apenas quando buscando approved
                if filters and any(f[2] == "approved" for f in (filters or [])):
                    return [fake_pkg]
                return []

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        client = TestClient(main_module.app)
        response = client.get("/api/inventory/packages?status=approved&start=2026-01-01&end=2026-01-31")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "approved"


# ---------------------------------------------------------------------------
# POST /api/enquetes/{poll_id}/fornecedor
# ---------------------------------------------------------------------------

class TestSetEnqueteFornecedor:
    """Testes para POST /api/enquetes/{poll_id}/fornecedor."""

    def test_retorna_503_quando_supabase_desabilitado(self, monkeypatch):
        """Retorna 503 quando Supabase está desabilitado."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)

        client = TestClient(main_module.app)
        response = client.post("/api/enquetes/poll_abc/fornecedor", json={"tag": "Fornecedor X"})

        assert response.status_code == 503

    def test_retorna_404_quando_enquete_nao_encontrada(self, monkeypatch):
        """Retorna 404 quando enquete não existe."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        class _FakeSB:
            def select(self, table, columns=None, filters=None, limit=None):
                return []
            def update(self, *a, **kw):
                pass

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))
        monkeypatch.setattr(main_module, "_is_uuid", lambda v: False)

        client = TestClient(main_module.app)
        response = client.post("/api/enquetes/poll_nao_existe/fornecedor", json={"tag": "Forn"})

        assert response.status_code == 404

    def test_sucesso_atualiza_fornecedor(self, monkeypatch):
        """Happy path: retorna sucesso com poll_id e fornecedor definido."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

        updated = {}

        class _FakeSB:
            def select(self, table, columns=None, filters=None, limit=None):
                return [{"id": "enq-uuid-1"}]
            def update(self, table, data, filters=None, returning=None):
                updated[table] = data
            def select_all(self, *a, **kw):
                return []
            @staticmethod
            def now_iso():
                return "2026-01-01T00:00:00Z"

        monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: _FakeSB()))

        async def _fake_gen():
            return _empty_metrics()

        monkeypatch.setattr(main_module, "generate_and_persist_metrics", _fake_gen)

        client = TestClient(main_module.app)
        response = client.post("/api/enquetes/poll_abc/fornecedor", json={"tag": "Fornecedor XYZ"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["fornecedor"] == "Fornecedor XYZ"
        assert body["poll_id"] == "poll_abc"


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/tag
# ---------------------------------------------------------------------------

class TestSetPackageTag:
    """Testes para POST /api/packages/{pkg_id}/tag."""

    def test_sucesso_define_tag(self, monkeypatch):
        """Happy path: define tag e retorna status success."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        from app.services import package_state_service as pss
        monkeypatch.setattr(pss, "update_package_state", lambda *a, **kw: None)
        monkeypatch.setattr(pss, "_resolve_package_uuid", lambda pid: None)

        from app.services import metrics_service as ms
        monkeypatch.setattr(ms, "load_metrics", lambda: _empty_metrics())

        client = TestClient(main_module.app)
        response = client.post("/api/packages/poll_abc_1/tag", json={"tag": "Blusa"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["tag"] == "Blusa"

    def test_sucesso_remove_tag_com_none(self, monkeypatch):
        """Tag pode ser removida passando null."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        from app.services import package_state_service as pss
        monkeypatch.setattr(pss, "update_package_state", lambda *a, **kw: None)
        monkeypatch.setattr(pss, "_resolve_package_uuid", lambda pid: None)

        from app.services import metrics_service as ms
        monkeypatch.setattr(ms, "load_metrics", lambda: _empty_metrics())

        client = TestClient(main_module.app)
        response = client.post("/api/packages/poll_abc_1/tag", json={"tag": None})

        assert response.status_code == 200
        body = response.json()
        assert body["tag"] is None


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/revert
# ---------------------------------------------------------------------------

class TestRevertPackage:
    """Testes para POST /api/packages/{pkg_id}/revert."""

    def test_retorna_403(self):
        """Revert foi removido — deve retornar 403 sempre."""
        client = TestClient(main_module.app)
        response = client.post("/api/packages/qualquer_id/revert")

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/cancel
# ---------------------------------------------------------------------------

class TestCancelConfirmedPackage:
    """Testes para POST /api/packages/{pkg_id}/cancel."""

    def test_retorna_404_quando_pacote_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote não existe em confirmed_packages."""
        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: None)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_nao_existe/cancel")

        assert response.status_code == 404

    def test_retorna_409_quando_cancelamento_bloqueado(self, monkeypatch):
        """Retorna 409 quando há clientes pagos no pacote (sem force)."""
        fake_pkg = {"id": "pkg_1", "source_package_id": "uuid-1", "votes": []}
        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)

        from app.services import package_cancellation_service as pcs
        paid_info = [{"name": "Ana", "phone": "5511999990001"}]
        exc = pcs.PackageCancelBlocked("bloqueado")
        exc.paid_info = paid_info

        def _raise(pkg_id, force, cancelled_by):
            raise exc

        monkeypatch.setattr(pcs, "cancel_package", _raise)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_1/cancel")

        assert response.status_code == 409
        body = response.json()
        assert body["status"] == "blocked_paid"

    def test_sucesso_cancela_pacote(self, monkeypatch):
        """Happy path: cancela pacote e retorna status success."""
        fake_pkg = {"id": "pkg_1", "source_package_id": "uuid-1", "votes": []}
        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)

        from app.services import package_cancellation_service as pcs

        def _ok_cancel(pkg_id, force, cancelled_by):
            return {"cancelled": True, "pkg_id": pkg_id}

        monkeypatch.setattr(pcs, "cancel_package", _ok_cancel)

        # Mocka refreshes financeiros
        from app.services import finance_service as fs, customer_service as cs
        monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
        monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
        monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_1/cancel")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/retry_payments
# ---------------------------------------------------------------------------

class TestRetryPayments:
    """Testes para POST /api/packages/{pkg_id}/retry_payments."""

    def test_retorna_410_gone(self):
        """Endpoint foi removido — deve retornar 410 Gone."""
        client = TestClient(main_module.app)
        response = client.post("/api/packages/qualquer_id/retry_payments")

        assert response.status_code == 410


# ---------------------------------------------------------------------------
# POST /api/packages/backfill-routing
# ---------------------------------------------------------------------------

class TestBackfillRouting:
    """Testes para POST /api/packages/backfill-routing."""

    def test_sucesso_sem_atualizacoes(self, monkeypatch):
        """Retorna status success quando backfill não encontra pacotes pra rotear."""
        monkeypatch.setattr(main_module, "_load_metrics", lambda: _empty_metrics())
        monkeypatch.setattr(main_module, "_save_metrics", lambda d: None)

        # As funções são importadas no topo de main.py
        monkeypatch.setattr(main_module, "load_poll_chat_map", lambda: {})
        monkeypatch.setattr(main_module, "backfill_metrics_routing", lambda data, chat_map: {"updated": 0})

        client = TestClient(main_module.app)
        response = client.post("/api/packages/backfill-routing")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["result"]["updated"] == 0

    def test_persiste_metricas_quando_ha_atualizacoes(self, monkeypatch):
        """Quando updated > 0, deve persistir métricas."""
        monkeypatch.setattr(main_module, "_load_metrics", lambda: _empty_metrics())

        saved = {}

        def _fake_save(data):
            saved["called"] = True

        monkeypatch.setattr(main_module, "_save_metrics", _fake_save)

        # As funções são importadas no topo de main.py, então monkeypatchar direto no módulo
        monkeypatch.setattr(main_module, "load_poll_chat_map", lambda: {"poll_abc": "chat_1"})
        monkeypatch.setattr(main_module, "backfill_metrics_routing", lambda data, chat_map: {"updated": 3})

        client = TestClient(main_module.app)
        response = client.post("/api/packages/backfill-routing")

        assert response.status_code == 200
        assert saved.get("called") is True


# ---------------------------------------------------------------------------
# GET /api/packages/{pkg_id}/edit-data
# ---------------------------------------------------------------------------

class TestGetConfirmedPackageEditData:
    """Testes para GET /api/packages/{pkg_id}/edit-data."""

    def test_retorna_404_quando_pacote_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote não está em confirmed_packages."""
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: None)

        client = TestClient(main_module.app)
        response = client.get("/api/packages/pkg_inexistente/edit-data")

        assert response.status_code == 404

    def test_sucesso_retorna_colunas_de_edicao(self, monkeypatch):
        """Happy path: retorna available_votes e selected_votes."""
        pkg_id = "poll_abc_1"
        fake_pkg = {
            "id": pkg_id,
            "poll_id": "poll_abc",
            "poll_title": "Blusa",
            "qty": 24,
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 24}],
        }
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)
        monkeypatch.setattr(cps, "load_confirmed_packages", lambda: [fake_pkg])

        monkeypatch.setattr(main_module, "_fetch_active_votes_for_poll", lambda poll_id: [
            {"phone": "5511999990001", "name": "Ana", "qty": 24},
            {"phone": "5511999990002", "name": "Bruno", "qty": 6},
        ])

        from app.services.confirmed_package_edit_service import build_edit_columns
        monkeypatch.setattr(
            "app.services.confirmed_package_edit_service.build_edit_columns",
            lambda pkg, active, confirmed: (
                [{"phone": "5511999990002", "name": "Bruno", "qty": 6}],
                [{"phone": "5511999990001", "name": "Ana", "qty": 24}],
            ),
        )

        client = TestClient(main_module.app)
        response = client.get(f"/api/packages/{pkg_id}/edit-data")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "available_votes" in body["data"]
        assert "selected_votes" in body["data"]


# ---------------------------------------------------------------------------
# PATCH /api/packages/{pkg_id}/edit
# ---------------------------------------------------------------------------

class TestEditPackage:
    """Testes para PATCH /api/packages/{pkg_id}/edit."""

    def test_retorna_400_sem_titulo(self, monkeypatch):
        """Retorna 400 quando poll_title não é fornecido."""
        client = TestClient(main_module.app)
        response = client.patch("/api/packages/pkg_1/edit", json={})

        assert response.status_code == 400
        assert "poll_title" in response.json()["detail"]

    def test_retorna_400_com_titulo_vazio(self, monkeypatch):
        """Retorna 400 quando poll_title é string vazia."""
        client = TestClient(main_module.app)
        response = client.patch("/api/packages/pkg_1/edit", json={"poll_title": ""})

        assert response.status_code == 400

    def test_sucesso_atualiza_titulo(self, monkeypatch):
        """Happy path: atualiza título e retorna status success."""
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())
        monkeypatch.setattr(main_module, "_is_supabase_metrics_mode", lambda: False)
        monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import package_state_service as pss
        monkeypatch.setattr(pss, "update_package_state", lambda *a, **kw: None)

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: None)

        monkeypatch.setattr(main_module, "_load_metrics", lambda: _empty_metrics())

        client = TestClient(main_module.app)
        response = client.patch("/api/packages/pkg_1/edit", json={"poll_title": "Novo Título"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["poll_title"] == "Novo Título"
        assert body["persisted"] is True


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/update-confirmed
# ---------------------------------------------------------------------------

class TestUpdateConfirmedPackageVotes:
    """Testes para POST /api/packages/{pkg_id}/update-confirmed."""

    def _votes_24(self):
        return [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
            {"phone": "5511999990002", "name": "Bruno", "qty": 12},
        ]

    def test_retorna_404_quando_pacote_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote não está em confirmed_packages."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: None)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_inexistente/update-confirmed", json={"votes": self._votes_24()})

        assert response.status_code == 404

    def test_retorna_400_quando_total_nao_e_24(self, monkeypatch):
        """Retorna 400 quando o total de peças não é exatamente 24."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())

        fake_pkg = {
            "id": "pkg_1",
            "poll_title": "Blusa",
            "qty": 24,
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 24}],
        }

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_1/update-confirmed", json={
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 6}]
        })

        assert response.status_code == 400

    def test_sucesso_atualiza_votos(self, monkeypatch):
        """Happy path: atualiza votos do pacote confirmado."""
        monkeypatch.setattr(main_module, "is_staging_dry_run", lambda: False)
        monkeypatch.setattr(main_module, "packages_lock", asyncio.Lock())
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)
        monkeypatch.setattr(main_module, "FinanceManager", None)

        fake_pkg = {
            "id": "pkg_1",
            "poll_title": "Blusa Rosa",
            "qty": 24,
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 24}],
        }

        from app.services import confirmed_packages_service as cps
        monkeypatch.setattr(cps, "get_confirmed_package", lambda pid: fake_pkg)
        monkeypatch.setattr(cps, "add_confirmed_package", lambda pkg: None)

        from app.services import metrics_service as ms
        monkeypatch.setattr(ms, "load_metrics", lambda: _empty_metrics())

        import app.workers.background as bg
        async def _fake_pdf_worker(pkg):
            pass
        monkeypatch.setattr(bg, "pdf_worker", _fake_pdf_worker)

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_1/update-confirmed", json={"votes": self._votes_24()})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "summary" in body


# ---------------------------------------------------------------------------
# GET /api/packages/{pkg_id}/edit-data-closed
# ---------------------------------------------------------------------------

class TestGetClosedPackageEditData:
    """Testes para GET /api/packages/{pkg_id}/edit-data-closed."""

    def test_retorna_404_quando_pacote_fechado_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote fechado não existe."""
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import closed_package_edit_service as cpes
        monkeypatch.setattr(cpes, "get_edit_data", lambda pkg_id: (_ for _ in ()).throw(cpes.ClosedPackageNotFound("não encontrado")))

        client = TestClient(main_module.app)
        response = client.get("/api/packages/pkg_fechado/edit-data-closed")

        assert response.status_code == 404

    def test_sucesso_retorna_dados_de_edicao(self, monkeypatch):
        """Happy path: retorna dados de edição do pacote fechado."""
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: "uuid-closed-1")

        from app.services import closed_package_edit_service as cpes
        monkeypatch.setattr(cpes, "get_edit_data", lambda pkg_id: {
            "package_id": pkg_id,
            "available_votes": [],
            "selected_votes": [],
        })

        client = TestClient(main_module.app)
        response = client.get("/api/packages/pkg_fechado/edit-data-closed")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"


# ---------------------------------------------------------------------------
# POST /api/packages/{pkg_id}/update-closed
# ---------------------------------------------------------------------------

class TestUpdateClosedPackageVotes:
    """Testes para POST /api/packages/{pkg_id}/update-closed."""

    def test_retorna_400_sem_json_body(self, monkeypatch):
        """Retorna 400 quando body não é JSON válido."""
        client = TestClient(main_module.app)
        response = client.post(
            "/api/packages/pkg_fechado/update-closed",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400

    def test_retorna_404_quando_pacote_nao_encontrado(self, monkeypatch):
        """Retorna 404 quando pacote fechado não existe."""
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: None)

        from app.services import closed_package_edit_service as cpes
        monkeypatch.setattr(cpes, "apply_edit", lambda pkg_id, votes: (_ for _ in ()).throw(cpes.ClosedPackageNotFound("não encontrado")))

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_fechado/update-closed", json={"votes": []})

        assert response.status_code == 404

    def test_sucesso_aplica_edicao(self, monkeypatch):
        """Happy path: aplica edição de votos no pacote fechado."""
        monkeypatch.setattr(main_module, "_resolve_supabase_package_id_or_none", lambda _: "uuid-closed-1")

        from app.services import closed_package_edit_service as cpes
        monkeypatch.setattr(cpes, "apply_edit", lambda pkg_id, votes: {"added": 1, "removed": 0})

        client = TestClient(main_module.app)
        response = client.post("/api/packages/pkg_fechado/update-closed", json={
            "votes": [{"phone": "5511999990001", "name": "Ana", "qty": 24}]
        })

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "summary" in body
