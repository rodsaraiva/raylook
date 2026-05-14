"""Testes unitários de app/services/portal_service.py.

Cobre funções puras, operações de DB via FakeSupabaseClient e
edge-cases de rate limiting. Funções que dependem de integrações
externas (AsaasClient, runtime_state_service) são mockadas no nível
de import interno ou puladas com justificativa explícita.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import bcrypt
import pytest

import app.services.portal_service as ps
from tests._helpers.fake_supabase import FakeSupabaseClient


# ---------------------------------------------------------------------------
# Helpers de fixture
# ---------------------------------------------------------------------------

def _future_iso(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _past_iso(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _hash(pw: str) -> str:
    """Gera hash bcrypt com custo 4 (rápido em teste)."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode()


def _make_cliente(
    id: str = "cli-1",
    celular: str = "5511999990001",
    password_hash: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    session_expires_at: str | None = None,
    reset_token: str | None = None,
    reset_token_expires_at: str | None = None,
) -> Dict[str, Any]:
    return {
        "id": id,
        "nome": "Test User",
        "celular": celular,
        "password_hash": password_hash,
        "email": email,
        "cpf_cnpj": None,
        "session_token": session_token,
        "session_expires_at": session_expires_at,
        "reset_token": reset_token,
        "reset_token_expires_at": reset_token_expires_at,
    }


def _install_fake(monkeypatch, fake: FakeSupabaseClient) -> None:
    monkeypatch.setattr(
        "app.services.portal_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake),
    )


# ---------------------------------------------------------------------------
# _normalize_phone
# ---------------------------------------------------------------------------

class TestNormalizePhone:
    def test_remove_mascara(self):
        assert ps._normalize_phone("(11) 99999-0001") == "11999990001"

    def test_string_vazia(self):
        assert ps._normalize_phone("") == ""

    def test_so_digitos(self):
        assert ps._normalize_phone("5511999990001") == "5511999990001"

    def test_none_equivalente(self):
        """None passado como str vira string vazia."""
        assert ps._normalize_phone(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _phone_variants
# ---------------------------------------------------------------------------

class TestPhoneVariants:
    def test_13_digitos_gera_sem_nono(self):
        """5511999990001 (13 dígitos) deve gerar variante sem o nono dígito (55 + DD + 8 dígitos)."""
        variants = ps._phone_variants("5511999990001")
        # rest = "999990001" (9 dígitos), começa com 9 → gera 55 + "11" + "99990001"
        assert "551199990001" in variants

    def test_sem_55_adiciona_com_55(self):
        """Número sem 55 deve ter variante com 55."""
        variants = ps._phone_variants("11999990001")
        assert "5511999990001" in variants

    def test_12_digitos_gera_com_nono(self):
        """5511990001 (12 dígitos) deve gerar variante com nono dígito inserido."""
        # 5511990001 tem 10 dígitos — não atinge o ramo de 12 dígitos
        # Usamos um número de 12 dígitos: 55 + DD(2) + 8 dígitos = "551190000001"
        variants = ps._phone_variants("551190000001")
        assert "5511990000001" in variants

    def test_sem_duplicatas(self):
        variants = ps._phone_variants("5511999990001")
        assert len(variants) == len(set(variants))

    def test_numero_curto_nao_adiciona_55(self):
        """Números < 10 dígitos não devem ganhar prefixo 55."""
        variants = ps._phone_variants("11999")
        assert "5511999" not in variants


# ---------------------------------------------------------------------------
# get_client_by_phone
# ---------------------------------------------------------------------------

class TestGetClientByPhone:
    def test_retorna_cliente_quando_encontrado(self, monkeypatch):
        row = _make_cliente(celular="5511999990001")
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_phone("5511999990001")
        assert result is not None
        assert result["id"] == "cli-1"

    def test_retorna_none_quando_nao_encontrado(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.get_client_by_phone("5511000000000") is None

    def test_phone_vazio_retorna_none(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.get_client_by_phone("") is None

    def test_encontra_por_variante_sem_55(self, monkeypatch):
        """Banco armazena sem 55; cliente digita com 55."""
        row = _make_cliente(celular="11999990001")
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_phone("5511999990001")
        assert result is not None

    def test_encontra_por_variante_com_55(self, monkeypatch):
        """Banco armazena com 55; cliente digita sem 55."""
        row = _make_cliente(celular="5511999990001")
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_phone("11999990001")
        assert result is not None


# ---------------------------------------------------------------------------
# get_client_by_session
# ---------------------------------------------------------------------------

class TestGetClientBySession:
    def test_token_vazio_retorna_none(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.get_client_by_session("") is None

    def test_token_nao_encontrado_retorna_none(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.get_client_by_session("tok-inexistente") is None

    def test_sessao_valida_retorna_cliente(self, monkeypatch):
        row = _make_cliente(
            session_token="tok-ok",
            session_expires_at=_future_iso(60),
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_session("tok-ok")
        assert result is not None
        assert result["id"] == "cli-1"

    def test_sessao_expirada_retorna_none_e_limpa(self, monkeypatch):
        row = _make_cliente(
            session_token="tok-exp",
            session_expires_at=_past_iso(60),
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_session("tok-exp")
        assert result is None
        # Verifica que o token foi limpo no banco
        stored = fake.tables["clientes"][0]
        assert stored.get("session_token") is None

    def test_expires_str_iso_invalida_retorna_none(self, monkeypatch):
        """String de data inválida não deve levantar exceção."""
        row = _make_cliente(
            session_token="tok-bad",
            session_expires_at="not-a-date",
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_session("tok-bad")
        assert result is None

    def test_expires_sem_tzinfo_tratado_como_utc(self, monkeypatch):
        """Datetime sem timezone deve ser tratado como UTC."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        row = _make_cliente(
            session_token="tok-naive",
            session_expires_at=future,
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_session("tok-naive")
        assert result is not None

    def test_sem_campo_expires_retorna_cliente(self, monkeypatch):
        """Linha sem session_expires_at deve retornar o cliente normalmente."""
        row = _make_cliente(session_token="tok-no-exp")
        row["session_expires_at"] = None
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_by_session("tok-no-exp")
        assert result is not None


# ---------------------------------------------------------------------------
# setup_client
# ---------------------------------------------------------------------------

class TestSetupClient:
    def test_salva_senha_email_cpf_retorna_token(self, monkeypatch):
        row = _make_cliente()
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        token = ps.setup_client("cli-1", "nova-senha", "ana@ex.com", "39053344705")
        assert isinstance(token, str) and len(token) > 20
        stored = fake.tables["clientes"][0]
        assert stored["email"] == "ana@ex.com"
        assert stored["cpf_cnpj"] == "39053344705"
        assert stored["session_token"] == token
        # senha deve estar hasheada
        assert bcrypt.checkpw(b"nova-senha", stored["password_hash"].encode())

    def test_cpf_mascarado_e_normalizado(self, monkeypatch):
        row = _make_cliente()
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        ps.setup_client("cli-1", "senha", "x@x.com", "390.533.447-05")
        stored = fake.tables["clientes"][0]
        assert stored["cpf_cnpj"] == "39053344705"


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------

class TestVerifyPassword:
    def test_senha_correta_retorna_true(self, monkeypatch):
        pw_hash = _hash("correta")
        row = _make_cliente(password_hash=pw_hash)
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        assert ps.verify_password("cli-1", "correta") is True

    def test_senha_incorreta_retorna_false(self, monkeypatch):
        pw_hash = _hash("correta")
        row = _make_cliente(password_hash=pw_hash)
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        assert ps.verify_password("cli-1", "errada") is False

    def test_cliente_sem_hash_retorna_false(self, monkeypatch):
        row = _make_cliente(password_hash=None)
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        assert ps.verify_password("cli-1", "qualquer") is False

    def test_cliente_nao_encontrado_retorna_false(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.verify_password("nao-existe", "x") is False

    def test_master_password_aceito(self, monkeypatch):
        """PORTAL_MASTER_PASSWORD deve permitir acesso independente do hash."""
        row = _make_cliente(password_hash=_hash("outro"))
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        monkeypatch.setenv("PORTAL_MASTER_PASSWORD", "master-secret")
        assert ps.verify_password("cli-1", "master-secret") is True

    def test_master_password_errado_nao_aceito(self, monkeypatch):
        pw_hash = _hash("correta")
        row = _make_cliente(password_hash=pw_hash)
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        monkeypatch.setenv("PORTAL_MASTER_PASSWORD", "master-secret")
        assert ps.verify_password("cli-1", "nao-master") is False


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_retorna_token_e_persiste_no_banco(self, monkeypatch):
        row = _make_cliente()
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        token = ps.create_session("cli-1")
        assert isinstance(token, str) and len(token) > 20
        stored = fake.tables["clientes"][0]
        assert stored["session_token"] == token
        assert stored["session_expires_at"] is not None

    def test_tokens_diferentes_a_cada_chamada(self, monkeypatch):
        row = _make_cliente()
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        t1 = ps.create_session("cli-1")
        t2 = ps.create_session("cli-1")
        assert t1 != t2


# ---------------------------------------------------------------------------
# destroy_session
# ---------------------------------------------------------------------------

class TestDestroySession:
    def test_limpa_token_do_banco(self, monkeypatch):
        row = _make_cliente(session_token="tok-a", session_expires_at=_future_iso())
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        ps.destroy_session("cli-1")
        stored = fake.tables["clientes"][0]
        assert stored["session_token"] is None
        assert stored["session_expires_at"] is None


# ---------------------------------------------------------------------------
# create_reset_token
# ---------------------------------------------------------------------------

class TestCreateResetToken:
    def test_persiste_token_e_retorna(self, monkeypatch):
        row = _make_cliente()
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        token = ps.create_reset_token("cli-1")
        assert isinstance(token, str) and len(token) > 20
        stored = fake.tables["clientes"][0]
        assert stored["reset_token"] == token
        assert stored["reset_token_expires_at"] is not None


# ---------------------------------------------------------------------------
# validate_reset_token
# ---------------------------------------------------------------------------

class TestValidateResetToken:
    def test_token_vazio_retorna_none(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.validate_reset_token("") is None

    def test_token_nao_encontrado_retorna_none(self, monkeypatch):
        fake = FakeSupabaseClient({"clientes": []})
        _install_fake(monkeypatch, fake)
        assert ps.validate_reset_token("tok-fantasma") is None

    def test_token_valido_retorna_cliente(self, monkeypatch):
        row = _make_cliente(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(30),
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.validate_reset_token("tok-ok")
        assert result is not None
        assert result["id"] == "cli-1"

    def test_token_expirado_retorna_none(self, monkeypatch):
        row = _make_cliente(
            reset_token="tok-exp",
            reset_token_expires_at=_past_iso(5),
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        assert ps.validate_reset_token("tok-exp") is None

    def test_expires_str_invalida_retorna_none(self, monkeypatch):
        row = _make_cliente(
            reset_token="tok-bad",
            reset_token_expires_at="data-invalida",
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        assert ps.validate_reset_token("tok-bad") is None

    def test_expires_sem_tzinfo_tratado_como_utc(self, monkeypatch):
        future_naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        row = _make_cliente(
            reset_token="tok-naive",
            reset_token_expires_at=future_naive,
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        result = ps.validate_reset_token("tok-naive")
        assert result is not None


# ---------------------------------------------------------------------------
# reset_password
# ---------------------------------------------------------------------------

class TestResetPassword:
    def test_atualiza_hash_e_retorna_token_sessao(self, monkeypatch):
        row = _make_cliente(
            reset_token="tok-ok",
            reset_token_expires_at=_future_iso(30),
        )
        fake = FakeSupabaseClient({"clientes": [row]})
        _install_fake(monkeypatch, fake)
        session_token = ps.reset_password("cli-1", "nova-senha")
        assert isinstance(session_token, str) and len(session_token) > 20
        stored = fake.tables["clientes"][0]
        assert bcrypt.checkpw(b"nova-senha", stored["password_hash"].encode())
        assert stored["reset_token"] is None
        assert stored["session_token"] == session_token


# ---------------------------------------------------------------------------
# get_client_kpis
# ---------------------------------------------------------------------------

class TestGetClientKpis:
    def _orders(self) -> List[Dict[str, Any]]:
        return [
            {"id": "o1", "status": "pending", "total_amount": 100.0},
            {"id": "o2", "status": "paid", "total_amount": 200.0},
            {"id": "o3", "status": "cancelled", "total_amount": 50.0},
        ]

    def test_soma_correta_por_status(self):
        kpis = ps.get_client_kpis(self._orders())
        assert kpis["total_pending"] == 100.0
        assert kpis["total_paid"] == 200.0
        assert kpis["pending_count"] == 1
        assert kpis["paid_count"] == 1

    def test_lista_vazia(self):
        kpis = ps.get_client_kpis([])
        assert kpis == {
            "total_pending": 0.0,
            "total_paid": 0.0,
            "pending_count": 0,
            "paid_count": 0,
        }

    def test_arredondamento(self):
        orders = [
            {"id": "x", "status": "pending", "total_amount": 0.1},
            {"id": "y", "status": "pending", "total_amount": 0.2},
        ]
        kpis = ps.get_client_kpis(orders)
        assert kpis["total_pending"] == round(0.3, 2)

    def test_total_amount_none_tratado_como_zero(self):
        orders = [{"id": "x", "status": "pending", "total_amount": None}]
        kpis = ps.get_client_kpis(orders)
        assert kpis["total_pending"] == 0.0


# ---------------------------------------------------------------------------
# get_client_orders
# ---------------------------------------------------------------------------

class TestGetClientOrders:
    def test_sem_vendas_retorna_lista_vazia(self, monkeypatch):
        fake = FakeSupabaseClient({"vendas": [], "pagamentos": []})
        _install_fake(monkeypatch, fake)
        # select_all retorna [] para vendas → retorna []
        result = ps.get_client_orders("cli-1")
        assert result == []

    def test_venda_cancelada_excluida(self, monkeypatch):
        """Vendas com status 'cancelled' não aparecem no resultado."""
        vendas = [{
            "id": "v1",
            "cliente_id": "cli-1",
            "pacote_id": "p1",
            "produto_id": "pr1",
            "qty": 1,
            "unit_price": 50.0,
            "subtotal": 50.0,
            "commission_percent": 0,
            "commission_amount": 0,
            "total_amount": 50.0,
            "status": "cancelled",
            "created_at": "2026-01-01T00:00:00Z",
            "produto": {"nome": "Foto", "descricao": None, "tamanho": None, "drive_file_id": None},
            "pacote": {"id": "p1", "enquete": {"titulo": "Sess", "created_at_provider": None, "drive_file_id": None}},
        }]
        pagamentos = [{
            "id": "pag1",
            "venda_id": "v1",
            "provider": "asaas",
            "provider_payment_id": None,
            "payment_link": "https://pay.link",
            "pix_payload": "pix123",
            "status": "pending",
            "due_date": None,
            "paid_at": None,
            "created_at": "2026-01-01T00:00:00Z",
        }]
        fake = FakeSupabaseClient({"vendas": vendas, "pagamentos": pagamentos})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_orders("cli-1")
        assert result == []

    def test_venda_sem_pagamento_excluida(self, monkeypatch):
        """Venda sem pagamento associado não aparece."""
        vendas = [{
            "id": "v2",
            "cliente_id": "cli-1",
            "pacote_id": "p1",
            "produto_id": "pr1",
            "qty": 1,
            "unit_price": 50.0,
            "subtotal": 50.0,
            "commission_percent": 0,
            "commission_amount": 0,
            "total_amount": 50.0,
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "produto": {"nome": "Foto", "descricao": None, "tamanho": None, "drive_file_id": None},
            "pacote": {"id": "p1", "enquete": {"titulo": "Sess", "created_at_provider": None, "drive_file_id": None}},
        }]
        fake = FakeSupabaseClient({"vendas": vendas, "pagamentos": []})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_orders("cli-1")
        assert result == []

    def test_venda_com_pagamento_retorna_order(self, monkeypatch):
        vendas = [{
            "id": "v3",
            "cliente_id": "cli-1",
            "pacote_id": "p1",
            "produto_id": "pr1",
            "qty": 2,
            "unit_price": 75.0,
            "subtotal": 150.0,
            "commission_percent": 10,
            "commission_amount": 15.0,
            "total_amount": 165.0,
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "produto": {"nome": "Camiseta", "descricao": None, "tamanho": "M", "drive_file_id": "drv-1"},
            "pacote": {"id": "p1", "enquete": {"titulo": "Enquete Jan", "created_at_provider": None, "drive_file_id": None}},
        }]
        pagamentos = [{
            "id": "pag3",
            "venda_id": "v3",
            "provider": "asaas",
            "provider_payment_id": "pay_xyz",
            "payment_link": "https://pay.link/xyz",
            "pix_payload": "pix-code",
            "status": "pending",
            "due_date": "2026-01-10",
            "paid_at": None,
            "created_at": "2026-01-01T00:00:00Z",
        }]
        fake = FakeSupabaseClient({"vendas": vendas, "pagamentos": pagamentos})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_orders("cli-1")
        assert len(result) == 1
        o = result[0]
        assert o["id"] == "v3"
        assert o["qty"] == 2
        assert o["total_amount"] == 165.0
        assert o["status"] == "pending"
        assert o["image_url"] == "/files/drv-1"
        assert o["produto_tamanho"] == "M"

    def test_pagamento_paid_status(self, monkeypatch):
        """Pagamento com status paid deve refletir como 'paid' no order."""
        vendas = [{
            "id": "v4",
            "cliente_id": "cli-1",
            "pacote_id": None,
            "produto_id": "pr1",
            "qty": 1,
            "unit_price": 100.0,
            "subtotal": 100.0,
            "commission_percent": 0,
            "commission_amount": 0,
            "total_amount": 100.0,
            "status": "paid",
            "created_at": "2026-01-01T00:00:00Z",
            "produto": {"nome": "Foto", "descricao": None, "tamanho": None, "drive_file_id": None},
            "pacote": None,
        }]
        pagamentos = [{
            "id": "pag4",
            "venda_id": "v4",
            "provider": "asaas",
            "provider_payment_id": "pay_abc",
            "payment_link": "",
            "pix_payload": "",
            "status": "paid",
            "due_date": None,
            "paid_at": "2026-01-05T12:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        }]
        fake = FakeSupabaseClient({"vendas": vendas, "pagamentos": pagamentos})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_orders("cli-1")
        assert len(result) == 1
        assert result[0]["status"] == "paid"

    def test_enquete_drive_id_tem_prioridade_sobre_produto(self, monkeypatch):
        """drive_file_id da enquete tem prioridade sobre o do produto (F-061)."""
        vendas = [{
            "id": "v5",
            "cliente_id": "cli-1",
            "pacote_id": "p5",
            "produto_id": "pr1",
            "qty": 1,
            "unit_price": 50.0,
            "subtotal": 50.0,
            "commission_percent": 0,
            "commission_amount": 0,
            "total_amount": 50.0,
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "produto": {"nome": "Foto", "descricao": None, "tamanho": None, "drive_file_id": "prod-drive"},
            "pacote": {"id": "p5", "enquete": {"titulo": "Sess", "created_at_provider": None, "drive_file_id": "enq-drive"}},
        }]
        pagamentos = [{
            "id": "pag5",
            "venda_id": "v5",
            "provider": "asaas",
            "provider_payment_id": None,
            "payment_link": "",
            "pix_payload": "pix",
            "status": "pending",
            "due_date": None,
            "paid_at": None,
            "created_at": "2026-01-01T00:00:00Z",
        }]
        fake = FakeSupabaseClient({"vendas": vendas, "pagamentos": pagamentos})
        _install_fake(monkeypatch, fake)
        result = ps.get_client_orders("cli-1")
        assert result[0]["image_url"] == "/files/enq-drive"


# ---------------------------------------------------------------------------
# _build_pix_response
# ---------------------------------------------------------------------------

class TestBuildPixResponse:
    def test_retorna_estrutura_correta_sem_payload(self):
        pag = {"pix_payload": "", "payment_link": "", "status": "pending"}
        result = ps._build_pix_response(pag)
        assert result["qr_code_base64"] == ""
        assert result["status"] == "pending"

    def test_retorna_qr_quando_ha_payload(self):
        pag = {"pix_payload": "pix-code-xyz", "payment_link": "https://link", "status": "sent"}
        result = ps._build_pix_response(pag)
        # QR Code deve ser base64 não vazio
        assert len(result["qr_code_base64"]) > 10
        assert result["pix_payload"] == "pix-code-xyz"


# ---------------------------------------------------------------------------
# _generate_qr_base64
# ---------------------------------------------------------------------------

class TestGenerateQrBase64:
    def test_retorna_string_base64_valida(self):
        import base64
        result = ps._generate_qr_base64("00020126360014br.gov.bcb.pix")
        # Deve decodificar sem erro
        decoded = base64.b64decode(result)
        # PNG magic bytes
        assert decoded[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# get_or_create_pix
# ---------------------------------------------------------------------------

class TestGetOrCreatePix:
    def test_pagamento_nao_encontrado_levanta_value_error(self, monkeypatch):
        fake = FakeSupabaseClient({"pagamentos": [], "vendas": []})
        _install_fake(monkeypatch, fake)
        with pytest.raises(ValueError, match="Pagamento não encontrado"):
            ps.get_or_create_pix("pag-inexistente", "cli-1")

    def test_venda_nao_encontrada_levanta_value_error(self, monkeypatch):
        pagamentos = [{"id": "pag1", "venda_id": "v-nao-existe",
                       "provider_payment_id": None, "payment_link": None,
                       "pix_payload": None, "status": "pending"}]
        fake = FakeSupabaseClient({"pagamentos": pagamentos, "vendas": []})
        _install_fake(monkeypatch, fake)
        with pytest.raises(ValueError, match="Venda não encontrada"):
            ps.get_or_create_pix("pag1", "cli-1")

    def test_ownership_errada_levanta_permission_error(self, monkeypatch):
        pagamentos = [{"id": "pag1", "venda_id": "v1",
                       "provider_payment_id": None, "payment_link": None,
                       "pix_payload": None, "status": "pending"}]
        vendas = [{"id": "v1", "cliente_id": "outro-cli",
                   "total_amount": 100.0, "qty": 1,
                   "produto": {"nome": "Foto"}}]
        fake = FakeSupabaseClient({"pagamentos": pagamentos, "vendas": vendas})
        _install_fake(monkeypatch, fake)
        with pytest.raises(PermissionError):
            ps.get_or_create_pix("pag1", "cli-1")

    def test_pix_payload_existente_retorna_sem_criar(self, monkeypatch):
        """Quando pix_payload e payment_link já existem, não chama Asaas."""
        pagamentos = [{"id": "pag2", "venda_id": "v2",
                       "provider_payment_id": "pay_existing",
                       "payment_link": "https://link",
                       "pix_payload": "pix-existente",
                       "status": "sent"}]
        vendas = [{"id": "v2", "cliente_id": "cli-1",
                   "total_amount": 100.0, "qty": 1,
                   "produto": {"nome": "Foto"}}]
        fake = FakeSupabaseClient({"pagamentos": pagamentos, "vendas": vendas})
        _install_fake(monkeypatch, fake)
        result = ps.get_or_create_pix("pag2", "cli-1")
        assert result["pix_payload"] == "pix-existente"
        assert result["payment_link"] == "https://link"


# ---------------------------------------------------------------------------
# check_rate_limit / record_login_attempt
# ---------------------------------------------------------------------------

class TestRateLimit:
    def setup_method(self):
        ps._login_attempts.clear()

    def test_permite_primeiras_tentativas(self):
        for _ in range(ps.LOGIN_MAX_ATTEMPTS - 1):
            assert ps.check_rate_limit("5511999990001") is True
            ps.record_login_attempt("5511999990001")

    def test_bloqueia_apos_limite(self):
        phone = "5511111111111"
        now = ps._now().timestamp()
        ps._login_attempts[ps._normalize_phone(phone)] = [now - i for i in range(ps.LOGIN_MAX_ATTEMPTS)]
        assert ps.check_rate_limit(phone) is False

    def test_tentativas_antigas_sao_ignoradas(self):
        """Tentativas fora da janela não contam para o limite."""
        phone = "5511222222222"
        normalized = ps._normalize_phone(phone)
        old_ts = ps._now().timestamp() - (ps.LOGIN_WINDOW_SECONDS + 60)
        ps._login_attempts[normalized] = [old_ts] * ps.LOGIN_MAX_ATTEMPTS
        assert ps.check_rate_limit(phone) is True

    def test_record_adiciona_tentativa(self):
        phone = "5511333333333"
        normalized = ps._normalize_phone(phone)
        ps.record_login_attempt(phone)
        assert len(ps._login_attempts[normalized]) == 1

    def test_phone_com_mascara_normalizado(self):
        """rate limit usa número normalizado."""
        ps.record_login_attempt("(55) 11 99999-0001")
        assert "5511999990001" in ps._login_attempts

    def test_check_limpa_tentativas_antigas(self):
        """check_rate_limit deve remover timestamps velhos do dict."""
        phone = "5511444444444"
        normalized = ps._normalize_phone(phone)
        old_ts = ps._now().timestamp() - (ps.LOGIN_WINDOW_SECONDS + 10)
        ps._login_attempts[normalized] = [old_ts, old_ts]
        ps.check_rate_limit(phone)
        assert len(ps._login_attempts[normalized]) == 0


# ---------------------------------------------------------------------------
# _update_pix_data (helper interno)
# ---------------------------------------------------------------------------

class TestUpdatePixData:
    def test_atualiza_payload_e_link(self, monkeypatch):
        pagamentos = [{"id": "pag1", "pix_payload": "", "payment_link": ""}]
        fake = FakeSupabaseClient({"pagamentos": pagamentos})
        pix_data = {"pix_payload": "novo-pix", "paymentLink": "https://novo.link"}
        ps._update_pix_data(fake, "pag1", pix_data)
        stored = fake.tables["pagamentos"][0]
        assert stored["pix_payload"] == "novo-pix"
        assert stored["payment_link"] == "https://novo.link"

    def test_sem_campos_apenas_updated_at(self, monkeypatch):
        pagamentos = [{"id": "pag1", "pix_payload": "old", "payment_link": "old-link"}]
        fake = FakeSupabaseClient({"pagamentos": pagamentos})
        ps._update_pix_data(fake, "pag1", {})
        stored = fake.tables["pagamentos"][0]
        # campos originais preservados
        assert stored["pix_payload"] == "old"
        assert stored["payment_link"] == "old-link"
        assert "updated_at" in stored


# ---------------------------------------------------------------------------
# resolve_combined_payment
# ---------------------------------------------------------------------------

class TestResolveCombinedPayment:
    def test_retorna_none_quando_runtime_desabilitado(self, monkeypatch):
        monkeypatch.setattr("app.services.portal_service.ps_runtime_state_enabled_import", None, raising=False)
        with patch("app.services.runtime_state_service.runtime_state_enabled", return_value=False):
            result = ps.resolve_combined_payment("pay_xyz")
        assert result is None

    def test_retorna_lista_de_ids_quando_encontrado(self, monkeypatch):
        ids = ["pag1", "pag2"]
        with patch("app.services.runtime_state_service.runtime_state_enabled", return_value=True), \
             patch("app.services.runtime_state_service.load_runtime_state",
                   return_value={"pagamento_ids": ids, "cliente_id": "cli-1"}):
            result = ps.resolve_combined_payment("pay_xyz")
        assert result == ids

    def test_retorna_none_quando_estado_nao_encontrado(self, monkeypatch):
        with patch("app.services.runtime_state_service.runtime_state_enabled", return_value=True), \
             patch("app.services.runtime_state_service.load_runtime_state", return_value=None):
            result = ps.resolve_combined_payment("pay_xyz")
        assert result is None
