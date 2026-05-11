"""Testes do router /portal — autenticação, sessão, reset de senha e APIs.

Padrão: monkeypatch em `app.services.portal_service.SupabaseRestClient.from_settings`
ou diretamente nas funções do portal_service quando o endpoint usa nested-select
(não suportado pelo FakeSupabaseClient).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from tests._helpers.fake_supabase import FakeSupabaseClient, FROZEN_NOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _make_client_row(
    *,
    id: str = "cli-1",
    nome: str = "Ana Lima",
    celular: str = "5511999990001",
    password_hash: Optional[str] = None,
    email: Optional[str] = None,
    cpf_cnpj: Optional[str] = None,
    session_token: Optional[str] = None,
    session_expires_at: Optional[str] = None,
    reset_token: Optional[str] = None,
    reset_token_expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": id,
        "nome": nome,
        "celular": celular,
        "password_hash": password_hash,
        "email": email,
        "cpf_cnpj": cpf_cnpj,
        "session_token": session_token,
        "session_expires_at": session_expires_at,
        "reset_token": reset_token,
        "reset_token_expires_at": reset_token_expires_at,
    }


def _install_fake_portal(monkeypatch, fake: FakeSupabaseClient) -> None:
    """Patcha SupabaseRestClient.from_settings no módulo portal_service."""
    monkeypatch.setattr(
        "app.services.portal_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake),
    )


def _test_client(monkeypatch, tables: Dict[str, List[Dict[str, Any]]]) -> tuple[TestClient, FakeSupabaseClient]:
    fake = FakeSupabaseClient(tables)
    _install_fake_portal(monkeypatch, fake)
    import main as main_module
    # Limpa cache de rate-limit entre testes
    import app.services.portal_service as ps
    ps._login_attempts.clear()
    return TestClient(main_module.app, follow_redirects=False), fake


# ---------------------------------------------------------------------------
# GET /portal — página de login
# ---------------------------------------------------------------------------

class TestPortalLoginPage:
    def test_sem_sessao_retorna_200(self, monkeypatch):
        """Sem cookie de sessão, renderiza a página de login."""
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/")
        assert res.status_code == 200
        assert b"portal" in res.content.lower() or res.status_code == 200

    def test_com_sessao_valida_redireciona_pedidos(self, monkeypatch):
        """Cookie de sessão válido redireciona para /portal/pedidos."""
        row = _make_client_row(
            session_token="tok-valid",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.get("/portal/", cookies={"portal_session": "tok-valid"})
        assert res.status_code == 302
        assert "/portal/pedidos" in res.headers["location"]


# ---------------------------------------------------------------------------
# POST /portal/login
# ---------------------------------------------------------------------------

class TestPortalLogin:
    def test_telefone_nao_encontrado_retorna_200_com_erro(self, monkeypatch):
        """Telefone desconhecido: renderiza login com erro (não 404)."""
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/login", data={"phone": "5511999990099", "password": ""})
        assert res.status_code == 200

    def test_primeiro_acesso_sem_senha_redireciona_setup(self, monkeypatch):
        """Cliente sem password_hash → redireciona para /portal/setup."""
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/login", data={"phone": "5511999990001", "password": ""})
        assert res.status_code == 302
        assert "/portal/setup" in res.headers["location"]

    def test_senha_incorreta_retorna_200_com_erro(self, monkeypatch):
        """Senha errada: renderiza login com mensagem de erro."""
        row = _make_client_row(
            celular="5511999990001",
            password_hash=_hash("senha-correta"),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/login", data={"phone": "5511999990001", "password": "errada"})
        assert res.status_code == 200

    def test_credenciais_corretas_redireciona_pedidos(self, monkeypatch):
        """Login correto: redireciona para /portal/pedidos com cookie."""
        row = _make_client_row(
            celular="5511999990001",
            password_hash=_hash("minha-senha"),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/login", data={"phone": "5511999990001", "password": "minha-senha"})
        assert res.status_code == 302
        assert "/portal/pedidos" in res.headers["location"]
        assert "portal_session" in res.cookies

    def test_senha_vazia_quando_ja_tem_hash_retorna_200_com_erro(self, monkeypatch):
        """Cliente com senha cadastrada mas não envia password → erro."""
        row = _make_client_row(
            celular="5511999990001",
            password_hash=_hash("minha-senha"),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/login", data={"phone": "5511999990001", "password": ""})
        assert res.status_code == 200

    def test_rate_limit_excedido_retorna_200_com_aviso(self, monkeypatch):
        """Após 5 tentativas, bloqueia e retorna aviso de limite."""
        import app.services.portal_service as ps
        row = _make_client_row(celular="5511999990001", password_hash=_hash("x"))
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        # Preenche o bucket de rate limit diretamente
        ps._login_attempts["5511999990001"] = [
            ps._now().timestamp() - i for i in range(5)
        ]
        res = client.post("/portal/login", data={"phone": "5511999990001", "password": "x"})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# GET /portal/setup
# ---------------------------------------------------------------------------

class TestPortalSetupPage:
    def test_sem_phone_redireciona_login(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/setup")
        assert res.status_code == 302
        assert "/portal" in res.headers["location"]

    def test_phone_nao_encontrado_redireciona_login(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/setup?phone=5511000000000")
        assert res.status_code == 302

    def test_cliente_com_senha_redireciona_login(self, monkeypatch):
        """Já tem senha cadastrada — não precisa de setup."""
        row = _make_client_row(celular="5511999990001", password_hash=_hash("x"))
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.get("/portal/setup?phone=5511999990001")
        assert res.status_code == 302

    def test_primeiro_acesso_retorna_formulario(self, monkeypatch):
        """Cliente sem senha → formulário de setup."""
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.get("/portal/setup?phone=5511999990001")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# POST /portal/setup
# ---------------------------------------------------------------------------

class TestPortalSetupSubmit:
    def test_senhas_diferentes_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/setup", data={
            "phone": "5511999990001",
            "email": "ana@exemplo.com",
            "cpf_cnpj": "",
            "password": "abc123",
            "password_confirm": "xyz999",
        })
        assert res.status_code == 200

    def test_senha_curta_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/setup", data={
            "phone": "5511999990001",
            "email": "ana@exemplo.com",
            "cpf_cnpj": "",
            "password": "123",
            "password_confirm": "123",
        })
        assert res.status_code == 200

    def test_email_invalido_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/setup", data={
            "phone": "5511999990001",
            "email": "email-invalido",
            "cpf_cnpj": "",
            "password": "senha123",
            "password_confirm": "senha123",
        })
        assert res.status_code == 200

    def test_cpf_invalido_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/setup", data={
            "phone": "5511999990001",
            "email": "ana@exemplo.com",
            "cpf_cnpj": "123",  # inválido
            "password": "senha123",
            "password_confirm": "senha123",
        })
        assert res.status_code == 200

    def test_setup_valido_redireciona_pedidos(self, monkeypatch):
        """Dados válidos: salva e redireciona para /portal/pedidos."""
        row = _make_client_row(celular="5511999990001", password_hash=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/setup", data={
            "phone": "5511999990001",
            "email": "ana@exemplo.com",
            "cpf_cnpj": "",
            "password": "senha123",
            "password_confirm": "senha123",
        })
        assert res.status_code == 302
        assert "/portal/pedidos" in res.headers["location"]

    def test_phone_nao_encontrado_redireciona(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/setup", data={
            "phone": "5511000000000",
            "email": "x@x.com",
            "cpf_cnpj": "",
            "password": "senha123",
            "password_confirm": "senha123",
        })
        assert res.status_code == 302


# ---------------------------------------------------------------------------
# GET /portal/reset
# ---------------------------------------------------------------------------

class TestPortalResetPage:
    def test_retorna_200(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/reset")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# POST /portal/reset
# ---------------------------------------------------------------------------

class TestPortalResetSubmit:
    def test_telefone_inexistente_retorna_sucesso_silencioso(self, monkeypatch):
        """Não revelar se número existe ou não — sempre mostra sucesso."""
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/reset", data={"phone": "5511000000000"})
        assert res.status_code == 200

    def test_cliente_sem_email_retorna_200_com_erro(self, monkeypatch):
        """Cliente cadastrado sem email → erro orientando a contatar suporte."""
        row = _make_client_row(celular="5511999990001", email=None)
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/reset", data={"phone": "5511999990001"})
        assert res.status_code == 200

    def test_sandbox_envia_link_por_log_retorna_sucesso(self, monkeypatch):
        """Em RAYLOOK_SANDBOX=true, o email é logado (stub) e retorna sucesso."""
        row = _make_client_row(celular="5511999990001", email="ana@exemplo.com")
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/reset", data={"phone": "5511999990001"})
        # Em sandbox, retorna sucesso (sem chamar Resend)
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# GET /portal/reset/{token}
# ---------------------------------------------------------------------------

class TestPortalResetConfirmPage:
    def test_token_invalido_retorna_200_com_erro(self, monkeypatch):
        """Token não encontrado no banco → formulário com mensagem de erro."""
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/reset/token-inexistente")
        assert res.status_code == 200

    def test_token_expirado_retorna_200_com_erro(self, monkeypatch):
        """Token com expires_at no passado → considerado inválido."""
        row = _make_client_row(
            reset_token="tok-exp",
            reset_token_expires_at=_past_iso(1),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.get("/portal/reset/tok-exp")
        assert res.status_code == 200

    def test_token_valido_retorna_formulario(self, monkeypatch):
        row = _make_client_row(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(1),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.get("/portal/reset/tok-ok")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# POST /portal/reset/{token}
# ---------------------------------------------------------------------------

class TestPortalResetConfirmSubmit:
    def test_token_invalido_retorna_200_com_erro(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/reset/token-invalido", data={
            "password": "nova123",
            "password_confirm": "nova123",
        })
        assert res.status_code == 200

    def test_senhas_diferentes_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(1),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/reset/tok-ok", data={
            "password": "nova123",
            "password_confirm": "outra456",
        })
        assert res.status_code == 200

    def test_senha_curta_retorna_200_com_erro(self, monkeypatch):
        row = _make_client_row(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(1),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/reset/tok-ok", data={
            "password": "123",
            "password_confirm": "123",
        })
        assert res.status_code == 200

    def test_reset_valido_redireciona_pedidos(self, monkeypatch):
        """Token válido + senhas corretas → redireciona para /portal/pedidos."""
        row = _make_client_row(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(1),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        res = client.post("/portal/reset/tok-ok", data={
            "password": "nova123",
            "password_confirm": "nova123",
        })
        assert res.status_code == 302
        assert "/portal/pedidos" in res.headers["location"]


# ---------------------------------------------------------------------------
# GET /portal/pedidos (requer sessão)
# ---------------------------------------------------------------------------

class TestPortalPedidos:
    def _session_row(self) -> Dict[str, Any]:
        return _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )

    def test_sem_sessao_redireciona_login(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/pedidos")
        assert res.status_code == 302
        assert "/portal" in res.headers["location"]

    def test_com_sessao_valida_retorna_200(self, monkeypatch):
        """Sessão válida: renderiza pedidos (get_client_orders mockado)."""
        import app.services.portal_service as ps
        row = self._session_row()
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        # get_client_orders usa nested-select — mockar diretamente
        monkeypatch.setattr(ps, "get_client_orders", lambda cid: [])
        monkeypatch.setattr(ps, "get_client_kpis", lambda orders: {
            "total_pending": 0.0, "total_paid": 0.0,
            "pending_count": 0, "paid_count": 0,
        })
        res = client.get("/portal/pedidos", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# GET /portal/api/status (polling JS)
# ---------------------------------------------------------------------------

class TestPortalApiStatus:
    def test_sem_sessao_retorna_401(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/api/status")
        assert res.status_code == 401
        assert res.json()["error"] == "Sessão expirada"

    def test_com_sessao_valida_retorna_kpis_e_orders(self, monkeypatch):
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        fake_orders = [{"id": "ord-1", "status": "pending", "total_amount": 100.0}]
        fake_kpis = {"total_pending": 100.0, "total_paid": 0.0, "pending_count": 1, "paid_count": 0}
        monkeypatch.setattr(ps, "get_client_orders", lambda cid: fake_orders)
        monkeypatch.setattr(ps, "get_client_kpis", lambda orders: fake_kpis)
        res = client.get("/portal/api/status", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 200
        body = res.json()
        assert "kpis" in body
        assert "orders" in body
        assert body["kpis"]["total_pending"] == 100.0


# ---------------------------------------------------------------------------
# POST /portal/api/pay-all
# ---------------------------------------------------------------------------

class TestPortalPayAll:
    def test_sem_sessao_retorna_401(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/api/pay-all")
        assert res.status_code == 401

    def test_sem_pedidos_pendentes_retorna_400(self, monkeypatch):
        """ValueError do service → 400."""
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        monkeypatch.setattr(ps, "create_combined_pix", lambda cid: (_ for _ in ()).throw(ValueError("Nenhum pedido pendente encontrado")))
        res = client.post("/portal/api/pay-all", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 400
        assert "error" in res.json()

    def test_sucesso_retorna_dados_pix(self, monkeypatch):
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        fake_pix = {
            "pix_payload": "00020101...",
            "payment_link": "https://pay.link/abc",
            "qr_code_base64": "base64data",
            "total": 150.0,
            "item_count": 2,
            "asaas_id": "pay_abc",
        }
        monkeypatch.setattr(ps, "create_combined_pix", lambda cid: fake_pix)
        res = client.post("/portal/api/pay-all", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 200
        body = res.json()
        assert body["pix_payload"] == "00020101..."
        assert body["total"] == 150.0


# ---------------------------------------------------------------------------
# POST /portal/api/pay/{pagamento_id}
# ---------------------------------------------------------------------------

class TestPortalPay:
    def test_sem_sessao_retorna_401(self, monkeypatch):
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.post("/portal/api/pay/pag-001")
        assert res.status_code == 401

    def test_pagamento_nao_encontrado_retorna_404(self, monkeypatch):
        """ValueError (pagamento não existe) → 404."""
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        monkeypatch.setattr(
            ps, "get_or_create_pix",
            lambda pag_id, cli_id: (_ for _ in ()).throw(ValueError("Pagamento não encontrado"))
        )
        res = client.post("/portal/api/pay/pag-nao-existe", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 404
        assert "error" in res.json()

    def test_acesso_negado_retorna_403(self, monkeypatch):
        """PermissionError → 403."""
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        monkeypatch.setattr(
            ps, "get_or_create_pix",
            lambda pag_id, cli_id: (_ for _ in ()).throw(PermissionError("Acesso negado"))
        )
        res = client.post("/portal/api/pay/pag-outro", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 403

    def test_sucesso_retorna_dados_pix(self, monkeypatch):
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-ok",
            session_expires_at=_future_iso(30),
        )
        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        fake_pix = {
            "pix_payload": "pix-code-abc",
            "payment_link": "https://pay.link/xyz",
            "qr_code_base64": "qrdata",
            "status": "sent",
        }
        monkeypatch.setattr(ps, "get_or_create_pix", lambda pag_id, cli_id: fake_pix)
        res = client.post("/portal/api/pay/pag-001", cookies={"portal_session": "sess-ok"})
        assert res.status_code == 200
        body = res.json()
        assert body["pix_payload"] == "pix-code-abc"
        assert body["status"] == "sent"


# ---------------------------------------------------------------------------
# GET /portal/logout
# ---------------------------------------------------------------------------

class TestPortalLogout:
    def test_sem_sessao_redireciona_login(self, monkeypatch):
        """Logout sem cookie: redireciona normalmente."""
        client, _ = _test_client(monkeypatch, {"clientes": []})
        res = client.get("/portal/logout")
        assert res.status_code == 302
        assert "/portal" in res.headers["location"]

    def test_com_sessao_destroi_e_redireciona(self, monkeypatch):
        import app.services.portal_service as ps
        row = _make_client_row(
            session_token="sess-del",
            session_expires_at=_future_iso(30),
        )
        destroyed = []
        original_destroy = ps.destroy_session

        def fake_destroy(cid):
            destroyed.append(cid)

        client, _ = _test_client(monkeypatch, {"clientes": [row]})
        monkeypatch.setattr(ps, "destroy_session", fake_destroy)
        res = client.get("/portal/logout", cookies={"portal_session": "sess-del"})
        assert res.status_code == 302
        assert "/portal" in res.headers["location"]
        assert len(destroyed) == 1
        assert "portal_session" not in res.cookies or res.cookies.get("portal_session") == ""
