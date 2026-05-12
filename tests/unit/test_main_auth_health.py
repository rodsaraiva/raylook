"""Testes para o bloco inicial de main.py (linhas 1–950 aprox.).

Cobre middlewares, auth, login/logout, webhooks, health checks,
reconciliação, raízes HTML, histórico de métricas e helpers privados.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main as main_module


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------


def _make_client():
    """Cria TestClient padrão com exceções visíveis."""
    return TestClient(main_module.app, raise_server_exceptions=False)


def _make_client_strict():
    """Cria TestClient que propaga exceções do servidor (para assertions de 200)."""
    return TestClient(main_module.app)


# ---------------------------------------------------------------------------
# Middleware: add_request_id_middleware
# ---------------------------------------------------------------------------


def test_request_id_header_presente_em_resposta():
    """Verifica que X-Request-ID é adicionado a toda resposta."""
    client = _make_client()
    resp = client.get("/health")
    assert "X-Request-ID" in resp.headers
    assert resp.headers["X-Request-ID"]  # não vazio


def test_request_id_eh_uuid_valido():
    """X-Request-ID deve ser um UUID v4 no formato correto."""
    import re
    client = _make_client()
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID", "")
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert uuid_re.match(rid), f"X-Request-ID não é UUID: {rid!r}"


def test_request_ids_distintos_por_requisicao():
    """Cada requisição deve receber um X-Request-ID único."""
    client = _make_client()
    ids = {client.get("/health").headers["X-Request-ID"] for _ in range(5)}
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# Middleware: csp_middleware
# ---------------------------------------------------------------------------


def test_csp_header_presente_em_resposta():
    """Content-Security-Policy deve estar presente em toda resposta."""
    client = _make_client()
    resp = client.get("/health")
    assert "Content-Security-Policy" in resp.headers


def test_csp_contem_default_src_self():
    """CSP deve conter 'default-src 'self''."""
    client = _make_client()
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp


def test_csp_contem_restricoes_de_img():
    """CSP deve restringir img-src para drive.google.com e self."""
    client = _make_client()
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "img-src" in csp
    assert "drive.google.com" in csp


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_check_retorna_ok():
    """GET /health deve retornar {status: ok} com 200."""
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_nao_exige_autenticacao(monkeypatch):
    """GET /health deve ser público mesmo sem DASHBOARD_AUTH_DISABLED."""
    monkeypatch.delenv("DASHBOARD_AUTH_DISABLED", raising=False)
    # Seta auth ativo mas não configura cookie
    with patch.dict(os.environ, {"DASHBOARD_AUTH_DISABLED": "false"}):
        client = _make_client()
        resp = client.get("/health", follow_redirects=False)
    # Deve retornar 200 (rota pública)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/supabase/health
# ---------------------------------------------------------------------------


def test_supabase_health_sem_credenciais_retorna_sqlite_info(monkeypatch):
    """Em modo sqlite (DATA_BACKEND=sqlite), fetch_project_status usa SQLite."""
    def mock_fetch():
        return {"backend": "sqlite", "status": "ok"}

    monkeypatch.setattr(main_module, "fetch_project_status", mock_fetch)
    client = _make_client_strict()
    resp = client.get("/api/supabase/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_supabase_health_erro_runtime_retorna_400(monkeypatch):
    """RuntimeError em fetch_project_status deve resultar em 400."""
    def mock_fetch_fail():
        raise RuntimeError("token inválido")

    monkeypatch.setattr(main_module, "fetch_project_status", mock_fetch_fail)
    client = _make_client()
    resp = client.get("/api/supabase/health")
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"


def test_supabase_health_excecao_generica_retorna_502(monkeypatch):
    """Exceção genérica em fetch_project_status deve resultar em 502."""
    def mock_fetch_boom():
        raise ConnectionError("sem rede")

    monkeypatch.setattr(main_module, "fetch_project_status", mock_fetch_boom)
    client = _make_client()
    resp = client.get("/api/supabase/health")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


def test_login_page_retorna_html():
    """GET /login deve retornar 200 com HTML."""
    client = _make_client()
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_login_page_redireciona_se_ja_autenticado():
    """GET /login com cookie válido deve redirecionar para /."""
    # Calcula token igual ao main.py
    password = os.getenv("DASHBOARD_AUTH_PASS", "R@ylook")
    token = hmac.new(password.encode(), b"raylook-dash-session", "sha256").hexdigest()

    client = _make_client()
    resp = client.get("/login", cookies={"dash_session": token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location", "").endswith("/")


def test_login_page_com_param_error():
    """GET /login?error=1 deve retornar 200 (renderiza formulário com erro)."""
    client = _make_client()
    resp = client.get("/login?error=1", follow_redirects=False)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


def test_login_submit_senha_correta_seta_cookie(monkeypatch):
    """POST /login com senha correta deve redirecionar e setar dash_session."""
    # Garante senha conhecida via _DASH_PASSWORD do módulo
    monkeypatch.setattr(main_module, "_DASH_PASSWORD", "senha_teste")
    token = hmac.new(b"senha_teste", b"raylook-dash-session", "sha256").hexdigest()
    monkeypatch.setattr(main_module, "_DASH_TOKEN", token)

    client = _make_client()
    resp = client.post(
        "/login",
        data={"password": "senha_teste"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "dash_session" in resp.cookies


def test_login_submit_senha_errada_redireciona_com_erro(monkeypatch):
    """POST /login com senha errada deve redirecionar para /login?error=1."""
    monkeypatch.setattr(main_module, "_DASH_PASSWORD", "certa")
    monkeypatch.setattr(main_module, "_DASH_TOKEN", "irrelevante")

    client = _make_client()
    resp = client.post(
        "/login",
        data={"password": "errada"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "error" in location


def test_login_submit_campo_vazio_redireciona_com_erro(monkeypatch):
    """POST /login sem campo password deve tratar como senha errada."""
    monkeypatch.setattr(main_module, "_DASH_PASSWORD", "certa")
    monkeypatch.setattr(main_module, "_DASH_TOKEN", "irrelevante")

    client = _make_client()
    resp = client.post("/login", data={}, follow_redirects=False)
    assert resp.status_code == 302
    assert "error" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# GET /logout
# ---------------------------------------------------------------------------


def test_logout_apaga_cookie_e_redireciona():
    """GET /logout deve redirecionar para /login e deletar dash_session."""
    client = _make_client()
    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "/login" in location


# ---------------------------------------------------------------------------
# Middleware: dashboard_auth_middleware
# ---------------------------------------------------------------------------


def test_api_sem_auth_retorna_401_quando_auth_ativo():
    """Rota /api/* sem cookie deve retornar 401 quando auth não está desabilitado."""
    with patch.dict(os.environ, {"DASHBOARD_AUTH_DISABLED": "false"}):
        client = TestClient(main_module.app, raise_server_exceptions=False)
        resp = client.get("/api/metrics", follow_redirects=False)
    # 401 JSON ou redirecionamento — principal: não é 200
    assert resp.status_code in (401, 302)


def test_rota_webhook_publica_sem_cookie():
    """Rota /webhook/* deve ser acessível sem cookie mesmo com auth ativo."""
    with patch.dict(os.environ, {"DASHBOARD_AUTH_DISABLED": "false"}):
        client = TestClient(main_module.app, raise_server_exceptions=False)
        # POST sem payload válido deve falhar no handler, não no middleware de auth
        resp = client.post(
            "/webhook/whatsapp",
            json={},
            follow_redirects=False,
        )
    # Não deve ser 302 (redirect para login) — o middleware não barra esta rota
    assert resp.status_code != 302


def test_rota_files_publica_sem_cookie():
    """Rota /files/* deve ser pública (sem autenticação requerida)."""
    with patch.dict(os.environ, {"DASHBOARD_AUTH_DISABLED": "false"}):
        client = TestClient(main_module.app, raise_server_exceptions=False)
        resp = client.get("/files/qualquer-id", follow_redirects=False)
    assert resp.status_code != 302


def test_pagina_raiz_redireciona_sem_cookie_quando_auth_ativo():
    """GET / sem cookie deve redirecionar para /login com auth ativo."""
    with patch.dict(os.environ, {"DASHBOARD_AUTH_DISABLED": "false"}):
        client = TestClient(main_module.app, raise_server_exceptions=False)
        resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# GET /files/{file_id}
# ---------------------------------------------------------------------------


def test_serve_local_file_nao_encontrado(monkeypatch):
    """GET /files/inexistente deve retornar 404 JSON."""
    from integrations import local_storage

    class FakeStorage:
        def resolve_file_path(self, file_id):
            return None

    monkeypatch.setattr(local_storage, "LocalImageStorage", FakeStorage)
    client = _make_client()
    resp = client.get("/files/inexistente")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_serve_local_file_encontrado(monkeypatch, tmp_path):
    """GET /files/<id> deve retornar o arquivo quando existe."""
    fake_file = tmp_path / "img.png"
    fake_file.write_bytes(b"\x89PNG")

    from integrations import local_storage

    class FakeStorage:
        def resolve_file_path(self, file_id):
            return (str(fake_file), "image/png")

    monkeypatch.setattr(local_storage, "LocalImageStorage", FakeStorage)
    client = _make_client()
    resp = client.get("/files/some-id")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")


# ---------------------------------------------------------------------------
# POST /webhook/whatsapp
# ---------------------------------------------------------------------------


def test_webhook_desabilitado_retorna_503(monkeypatch):
    """POST /webhook/whatsapp deve retornar 503 quando webhook está desabilitado."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", False)
    client = _make_client()
    resp = client.post("/webhook/whatsapp", json={})
    assert resp.status_code == 503


def test_webhook_secret_required_sem_secret_configurado_retorna_503(monkeypatch):
    """Se WHATSAPP_WEBHOOK_SECRET_REQUIRED=true e secret vazio → 503."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "")
    with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_SECRET_REQUIRED": "true"}):
        client = _make_client()
        resp = client.post("/webhook/whatsapp", json={})
    assert resp.status_code == 503


def test_webhook_com_secret_errado_retorna_401(monkeypatch):
    """POST /webhook/whatsapp com secret incorreto deve retornar 401."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "segredo-certo")
    client = _make_client()
    resp = client.post(
        "/webhook/whatsapp",
        json={"event": "test"},
        headers={"Authorization": "Bearer segredo-errado"},
    )
    assert resp.status_code == 401


def test_webhook_sem_secret_configurado_aceita_sem_auth(monkeypatch):
    """Sem WHATSAPP_WEBHOOK_SECRET configurado, webhook deve aceitar sem auth."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "")
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_client = MagicMock()
    fake_client.ingest.return_value = {"processed": False}
    monkeypatch.setattr(main_module, "SupabaseRestClient", MagicMock(from_settings=lambda: fake_client))
    monkeypatch.setattr(
        main_module, "WebhookIngestionService",
        lambda client: MagicMock(ingest=lambda payload: {"processed": False}),
    )

    with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_SECRET_REQUIRED": "false"}):
        client = _make_client()
        resp = client.post("/webhook/whatsapp", json={"event": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_webhook_json_invalido_retorna_400(monkeypatch):
    """POST /webhook/whatsapp com body inválido deve retornar 400."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "")
    with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_SECRET_REQUIRED": "false"}):
        client = _make_client()
        resp = client.post(
            "/webhook/whatsapp",
            content=b"isto nao e json!!!",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400


def test_webhook_supabase_desabilitado_retorna_503(monkeypatch):
    """POST /webhook/whatsapp com supabase_domain_enabled=False deve retornar 503."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "")
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_SECRET_REQUIRED": "false"}):
        client = _make_client()
        resp = client.post("/webhook/whatsapp", json={"event": "test"})
    assert resp.status_code == 503


def test_webhook_aceita_bearer_token_correto(monkeypatch):
    """Webhook com Authorization: Bearer <secret-correto> deve ser aceito."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "token123")
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        main_module, "WebhookIngestionService",
        lambda client: MagicMock(ingest=lambda payload: {"processed": False}),
    )
    client = _make_client()
    resp = client.post(
        "/webhook/whatsapp",
        json={"event": "test"},
        headers={"Authorization": "Bearer token123"},
    )
    assert resp.status_code == 200


def test_webhook_aceita_x_webhook_secret_header(monkeypatch):
    """Webhook com x-webhook-secret correto deve ser aceito."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "meu-secret")
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        main_module, "WebhookIngestionService",
        lambda client: MagicMock(ingest=lambda payload: {"processed": False}),
    )
    client = _make_client()
    resp = client.post(
        "/webhook/whatsapp",
        json={"event": "test"},
        headers={"x-webhook-secret": "meu-secret"},
    )
    assert resp.status_code == 200


def test_webhook_aceita_query_param_secret(monkeypatch):
    """Webhook com ?secret=<correto> deve ser aceito."""
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(main_module.settings, "WHATSAPP_WEBHOOK_SECRET", "qp-secret")
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        main_module, "WebhookIngestionService",
        lambda client: MagicMock(ingest=lambda payload: {"processed": False}),
    )
    client = _make_client()
    resp = client.post(
        "/webhook/whatsapp?secret=qp-secret",
        json={"event": "test"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/reconcile/supabase-baserow
# ---------------------------------------------------------------------------


def test_reconcile_supabase_domain_desabilitado(monkeypatch):
    """GET /api/reconcile/supabase-baserow com supabase_domain=False → 503."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    client = _make_client()
    resp = client.get("/api/reconcile/supabase-baserow")
    assert resp.status_code == 503


def test_reconcile_retorna_contagens(monkeypatch):
    """GET /api/reconcile/supabase-baserow deve retornar contagens de cada tabela."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.side_effect = [
        [{"id": "e1"}, {"id": "e2"}],  # enquetes
        [{"id": "v1"}],                # votos
        [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],  # pacotes
        [],                            # vendas
        [{"id": "pg1"}],               # pagamentos
    ]
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/reconcile/supabase-baserow")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["supabase"]["enquetes"] == 2
    assert body["supabase"]["pacotes"] == 3
    assert body["supabase"]["votos"] == 1
    assert body["supabase"]["vendas"] == 0


def test_reconcile_erro_retorna_502(monkeypatch):
    """GET /api/reconcile/supabase-baserow com exceção deve retornar 502."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.side_effect = ConnectionError("timeout")
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client()
    resp = client.get("/api/reconcile/supabase-baserow")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET / e GET /v1
# ---------------------------------------------------------------------------


def test_read_root_retorna_html():
    """GET / deve retornar 200 com HTML (dashboard_v2.html)."""
    client = _make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_read_root_v1_retorna_html():
    """GET /v1 deve retornar 200 com HTML (index.html legacy)."""
    client = _make_client()
    resp = client.get("/v1")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /api/metrics/history
# ---------------------------------------------------------------------------


def test_metrics_history_sem_supabase_retorna_503(monkeypatch):
    """GET /api/metrics/history sem Supabase deve retornar 503."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    client = _make_client()
    resp = client.get("/api/metrics/history")
    assert resp.status_code == 503


def test_metrics_history_com_supabase_retorna_lista(monkeypatch):
    """GET /api/metrics/history com Supabase deve retornar itens."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.return_value = [
        {"id": "snap1", "hour_bucket": "2026-05-01T10:00:00+00:00"},
        {"id": "snap2", "hour_bucket": "2026-05-01T11:00:00+00:00"},
    ]
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/metrics/history?hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2


def test_metrics_history_from_ts_invalido_retorna_400(monkeypatch):
    """GET /api/metrics/history com from_ts inválido deve retornar 400."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    client = _make_client()
    resp = client.get("/api/metrics/history?from_ts=nao-e-data")
    assert resp.status_code == 400


def test_metrics_history_to_ts_invalido_retorna_400(monkeypatch):
    """GET /api/metrics/history com to_ts inválido deve retornar 400."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    client = _make_client()
    resp = client.get("/api/metrics/history?to_ts=invalido")
    assert resp.status_code == 400


def test_metrics_history_erro_no_supabase_retorna_500(monkeypatch):
    """GET /api/metrics/history com erro no Supabase deve retornar 500."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.side_effect = RuntimeError("falha")
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client()
    resp = client.get("/api/metrics/history")
    assert resp.status_code == 500


def test_metrics_history_resposta_vazia_retorna_zero_itens(monkeypatch):
    """GET /api/metrics/history sem snapshots deve retornar count=0."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.return_value = []
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/metrics/history")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/metrics/snapshot
# ---------------------------------------------------------------------------


def test_force_metrics_snapshot_sem_supabase_retorna_503(monkeypatch):
    """POST /api/metrics/snapshot sem Supabase deve retornar 503."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    client = _make_client()
    resp = client.post("/api/metrics/snapshot")
    assert resp.status_code == 503


def test_force_metrics_snapshot_com_supabase_retorna_id(monkeypatch):
    """POST /api/metrics/snapshot com Supabase deve retornar status success."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    async def fake_capture_once():
        return "snap-uuid-123"

    with patch.dict("sys.modules", {}):
        from unittest.mock import AsyncMock
        with patch("app.workers.metrics_snapshot_worker.capture_once", new=fake_capture_once):
            client = _make_client_strict()
            resp = client.post("/api/metrics/snapshot")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_force_metrics_snapshot_erro_retorna_500(monkeypatch):
    """POST /api/metrics/snapshot com exceção deve retornar 500."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    with patch("app.workers.metrics_snapshot_worker.capture_once", side_effect=RuntimeError("boom")):
        client = _make_client()
        resp = client.post("/api/metrics/snapshot")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/test-mode
# ---------------------------------------------------------------------------


def test_get_test_mode_retorna_status(monkeypatch):
    """GET /api/test-mode deve retornar {active, label}."""
    with patch("app.services.test_mode_service.get_test_mode_status", return_value={"active": False, "label": None}):
        client = _make_client_strict()
        resp = client.get("/api/test-mode")
    assert resp.status_code == 200
    assert "active" in resp.json()


def test_get_test_mode_fallback_em_excecao():
    """GET /api/test-mode com exceção deve retornar {active: False}."""
    with patch("app.services.test_mode_service.get_test_mode_status", side_effect=RuntimeError("boom")):
        client = _make_client_strict()
        resp = client.get("/api/test-mode")
    assert resp.status_code == 200
    assert resp.json()["active"] is False


# ---------------------------------------------------------------------------
# POST /api/test-mode/toggle
# ---------------------------------------------------------------------------


def test_toggle_test_mode_retorna_success(monkeypatch):
    """POST /api/test-mode/toggle deve retornar {status: success}."""
    with patch("app.services.test_mode_service.is_test_mode_active", return_value=False), \
         patch("app.services.test_mode_service.set_test_mode", return_value={"active": True, "label": "[ENQUETE DE TESTE]"}):
        client = _make_client_strict()
        resp = client.post("/api/test-mode/toggle")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_toggle_test_mode_excecao_retorna_500():
    """POST /api/test-mode/toggle com exceção deve retornar 500."""
    with patch("app.services.test_mode_service.is_test_mode_active", side_effect=RuntimeError("erro")):
        client = _make_client()
        resp = client.post("/api/test-mode/toggle")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/drive/cleanup
# ---------------------------------------------------------------------------


def test_force_drive_cleanup_retorna_success():
    """POST /api/drive/cleanup deve retornar {status: success, report: ...}."""
    fake_report = {"deleted_folders": 2, "protected": 5}
    with patch("app.workers.drive_cleanup_worker.cleanup_drive_once", return_value=fake_report):
        client = _make_client_strict()
        resp = client.post("/api/drive/cleanup")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["report"] == fake_report


def test_force_drive_cleanup_excecao_retorna_500():
    """POST /api/drive/cleanup com exceção deve retornar 500."""
    with patch("app.workers.drive_cleanup_worker.cleanup_drive_once", side_effect=RuntimeError("falha")):
        client = _make_client()
        resp = client.post("/api/drive/cleanup")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/metrics/temperature/refresh
# ---------------------------------------------------------------------------


def test_refresh_sales_temperature_retorna_success():
    """POST /api/metrics/temperature/refresh deve retornar {status: success}."""
    fake_temp = {"score": 72, "label": "quente"}
    with patch("app.services.sales_temperature_service.get_temperature", return_value=fake_temp):
        client = _make_client_strict()
        resp = client.post("/api/metrics/temperature/refresh")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["temperature"] == fake_temp


def test_refresh_sales_temperature_excecao_retorna_500():
    """POST /api/metrics/temperature/refresh com exceção deve retornar 500."""
    with patch("app.services.sales_temperature_service.get_temperature", side_effect=ValueError("erro")):
        client = _make_client()
        resp = client.post("/api/metrics/temperature/refresh")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/metrics/health
# ---------------------------------------------------------------------------


def test_metrics_snapshot_health_sem_supabase_retorna_503(monkeypatch):
    """GET /api/metrics/health sem Supabase deve retornar 503."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    client = _make_client()
    resp = client.get("/api/metrics/health")
    assert resp.status_code == 503


def test_metrics_snapshot_health_sem_snapshots_retorna_critical(monkeypatch):
    """GET /api/metrics/health sem snapshots deve retornar status=critical."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.return_value = []
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/metrics/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "critical"
    assert resp.json()["snapshot_count"] == 0


def test_metrics_snapshot_health_com_snapshots_recentes_retorna_ok(monkeypatch):
    """GET /api/metrics/health com snapshot recente deve retornar status=ok."""
    from datetime import datetime, timedelta, timezone as tz

    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    now = datetime.now(tz.utc)
    buckets = [
        {"hour_bucket": (now - timedelta(hours=i)).isoformat(), "captured_at": (now - timedelta(hours=i)).isoformat()}
        for i in range(23, -1, -1)
    ]
    fake_sb = MagicMock()
    fake_sb.select.return_value = buckets
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/metrics/health?window_hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")  # fresco → ok ou degraded se pequeno gap


def test_metrics_snapshot_health_com_gap_retorna_degraded(monkeypatch):
    """GET /api/metrics/health com gap deve retornar status=degraded (ou critical)."""
    from datetime import datetime, timedelta, timezone as tz

    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    now = datetime.now(tz.utc)
    # Buckets em ordem crescente (como o Supabase retorna com order=asc).
    # Gap de 3h entre hora -6 e hora -3 — ambos "antigos" para que o
    # último bucket seja recente (minutes_since_last < 130) → degraded.
    recent = now - timedelta(minutes=30)
    buckets = [
        {"hour_bucket": (recent - timedelta(hours=6)).isoformat()},
        {"hour_bucket": (recent - timedelta(hours=5)).isoformat()},
        # gap de 3h aqui (horas -5 a -2 ausentes)
        {"hour_bucket": (recent - timedelta(hours=2)).isoformat()},
        {"hour_bucket": (recent - timedelta(hours=1)).isoformat()},
        {"hour_bucket": recent.isoformat()},
    ]
    fake_sb = MagicMock()
    fake_sb.select.return_value = buckets
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client_strict()
    resp = client.get("/api/metrics/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("degraded", "critical")
    assert len(resp.json()["gaps"]) >= 1


def test_metrics_snapshot_health_erro_retorna_500(monkeypatch):
    """GET /api/metrics/health com erro no Supabase deve retornar 500."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    fake_sb = MagicMock()
    fake_sb.select.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", lambda: fake_sb)

    client = _make_client()
    resp = client.get("/api/metrics/health")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


def test_is_supabase_metrics_mode_quando_source_supabase(monkeypatch):
    """_is_supabase_metrics_mode deve retornar True quando METRICS_SOURCE=supabase."""
    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "supabase")
    assert main_module._is_supabase_metrics_mode() is True


def test_is_supabase_metrics_mode_quando_source_baserow(monkeypatch):
    """_is_supabase_metrics_mode deve retornar False quando METRICS_SOURCE=baserow."""
    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "baserow")
    assert main_module._is_supabase_metrics_mode() is False


def test_should_async_webhook_postprocess_depende_de_test_mode(monkeypatch):
    """_should_async_webhook_postprocess requer test_mode + supabase_metrics + supabase_domain."""
    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "supabase")
    monkeypatch.setattr(main_module.settings, "TEST_MODE", True)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    assert main_module._should_async_webhook_postprocess() is True


def test_should_async_webhook_postprocess_false_sem_test_mode(monkeypatch):
    """_should_async_webhook_postprocess retorna False quando TEST_MODE=False."""
    monkeypatch.setattr(main_module.settings, "METRICS_SOURCE", "supabase")
    monkeypatch.setattr(main_module.settings, "TEST_MODE", False)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    assert main_module._should_async_webhook_postprocess() is False


def test_metrics_snapshot_version_arquivo_existente(tmp_path, monkeypatch):
    """_metrics_snapshot_version deve retornar string não-vazia quando arquivo existe."""
    f = tmp_path / "dashboard_metrics.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module, "METRICS_FILE", str(f))
    version = main_module._metrics_snapshot_version()
    assert version  # não vazio


def test_metrics_snapshot_version_arquivo_ausente(tmp_path, monkeypatch):
    """_metrics_snapshot_version deve retornar string vazia quando arquivo não existe."""
    monkeypatch.setattr(main_module, "METRICS_FILE", str(tmp_path / "nao_existe.json"))
    version = main_module._metrics_snapshot_version()
    assert version == ""


def test_encode_sse_formato_correto():
    """_encode_sse deve gerar payload no formato SSE correto."""
    result = main_module._encode_sse("update", {"key": "val"})
    assert result.startswith("event: update\n")
    assert "data: " in result
    assert result.endswith("\n\n")
    payload = json.loads(result.split("data: ", 1)[1].strip())
    assert payload["key"] == "val"


def test_is_uuid_com_uuid_valido():
    """_is_uuid deve retornar True para UUID válido."""
    assert main_module._is_uuid("550e8400-e29b-41d4-a716-446655440000") is True


def test_is_uuid_com_string_invalida():
    """_is_uuid deve retornar False para string não-UUID."""
    assert main_module._is_uuid("nao_e_uuid") is False
    assert main_module._is_uuid("poll_42") is False
    assert main_module._is_uuid("") is False


def test_resolve_supabase_package_id_or_none_supabase_desabilitado(monkeypatch):
    """_resolve_supabase_package_id_or_none deve retornar None sem Supabase."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: False)
    result = main_module._resolve_supabase_package_id_or_none("poll_1")
    assert result is None


def test_resolve_supabase_package_id_or_none_uuid_retorna_direto(monkeypatch):
    """_resolve_supabase_package_id_or_none com UUID deve retornar o UUID sem lookup."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    uid = "550e8400-e29b-41d4-a716-446655440000"
    result = main_module._resolve_supabase_package_id_or_none(uid)
    assert result == uid


def test_resolve_supabase_package_id_or_none_lookup_excecao_retorna_none(monkeypatch):
    """_resolve_supabase_package_id_or_none deve retornar None se lookup falhar."""
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(main_module, "resolve_supabase_package_id", lambda pkg_id: (_ for _ in ()).throw(RuntimeError("not found")))

    result = main_module._resolve_supabase_package_id_or_none("poll_1")
    assert result is None


def test_find_package_in_metrics_acha_por_id_legacy():
    """_find_package_in_metrics deve achar pacote por id legacy."""
    data = {
        "votos": {
            "packages": {
                "closed_today": [{"id": "poll_1", "qty": 6}],
                "confirmed_today": [],
                "closed_week": [],
                "open": [],
            }
        }
    }
    pkg = main_module._find_package_in_metrics(data, "poll_1")
    assert pkg is not None
    assert pkg["qty"] == 6


def test_find_package_in_metrics_acha_por_source_package_id():
    """_find_package_in_metrics deve preferir match por source_package_id (UUID)."""
    uid = "550e8400-e29b-41d4-a716-446655440000"
    data = {
        "votos": {
            "packages": {
                "closed_today": [{"id": "poll_1", "source_package_id": uid, "qty": 9}],
                "confirmed_today": [],
                "closed_week": [],
                "open": [],
            }
        }
    }
    pkg = main_module._find_package_in_metrics(data, uid)
    assert pkg is not None
    assert pkg["qty"] == 9


def test_find_package_in_metrics_retorna_none_quando_nao_acha():
    """_find_package_in_metrics deve retornar None se id não encontrado."""
    data = {"votos": {"packages": {"closed_today": [], "confirmed_today": [], "closed_week": [], "open": []}}}
    pkg = main_module._find_package_in_metrics(data, "inexistente")
    assert pkg is None


def test_find_package_in_metrics_sem_candidates_retorna_none():
    """_find_package_in_metrics sem candidatos deve retornar None."""
    data = {"votos": {"packages": {}}}
    assert main_module._find_package_in_metrics(data) is None


def test_find_package_in_metrics_prioriza_confirmed_sobre_open():
    """_find_package_in_metrics deve retornar confirmed antes de open (F-034)."""
    data = {
        "votos": {
            "packages": {
                "confirmed_today": [{"id": "poll_0", "secao": "confirmed", "qty": 24}],
                "open": [{"id": "poll_0", "secao": "open", "qty": 0}],
                "closed_today": [],
                "closed_week": [],
            }
        }
    }
    pkg = main_module._find_package_in_metrics(data, "poll_0")
    assert pkg["secao"] == "confirmed"


def test_dashboard_stream_versions_retorna_chaves_esperadas(monkeypatch, tmp_path):
    """_dashboard_stream_versions deve retornar chaves dashboard, finance, customers."""
    f = tmp_path / "metrics.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module, "METRICS_FILE", str(f))
    monkeypatch.setattr(main_module, "load_runtime_state_metadata", lambda keys: {})

    versions = main_module._dashboard_stream_versions()
    assert "dashboard" in versions
    assert "finance" in versions
    assert "customers" in versions
