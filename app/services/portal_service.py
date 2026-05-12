"""Portal do Cliente — serviço de autenticação e dados.

Gerencia login por telefone+senha, sessão persistente via cookie,
reset de senha, e agregação de pedidos/pagamentos do cliente.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
import qrcode  # type: ignore
from PIL import Image  # type: ignore

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.portal")

SESSION_DURATION_DAYS = 30
RESET_TOKEN_MINUTES = 30
BCRYPT_ROUNDS = 12

PENDING_STATUSES = {"created", "sent", "pending", "enviando", "erro no envio"}
PAID_STATUSES = {"paid"}
CANCELLED_STATUSES = {"cancelled", "cancelado"}


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client() -> SupabaseRestClient:
    return SupabaseRestClient.from_settings()


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _phone_variants(normalized: str) -> List[str]:
    """Gera variações do telefone para busca flexível.

    Celulares brasileiros: 55 + DDD(2) + 9 + 8 dígitos = 13 dígitos
    Alguns números no banco estão sem o nono dígito (12 dígitos).
    O cliente pode digitar com ou sem o 55, com ou sem o 9 extra.
    """
    variants = [normalized]

    # Se não começa com 55, adicionar
    if not normalized.startswith("55") and len(normalized) >= 10:
        variants.append("55" + normalized)

    for v in list(variants):
        if len(v) == 13 and v.startswith("55"):
            # 55 + DD + 9XXXX XXXX → tentar sem o nono dígito: 55 + DD + XXXX XXXX
            ddd = v[2:4]
            rest = v[4:]  # 9 dígitos
            if rest[0] == "9" and len(rest) == 9:
                variants.append("55" + ddd + rest[1:])
        elif len(v) == 12 and v.startswith("55"):
            # 55 + DD + XXXX XXXX → tentar com nono dígito: 55 + DD + 9 + XXXX XXXX
            ddd = v[2:4]
            rest = v[4:]  # 8 dígitos
            if len(rest) == 8:
                variants.append("55" + ddd + "9" + rest)

    # Sem o 55 na frente
    for v in list(variants):
        if v.startswith("55") and len(v) >= 12:
            without_55 = v[2:]
            if without_55 not in variants:
                variants.append(without_55)

    return list(dict.fromkeys(variants))  # preservar ordem, remover duplicatas


def get_client_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None

    variants = _phone_variants(normalized)
    client = _client()
    for variant in variants:
        rows = client.select(
            "clientes",
            columns="id,nome,celular,password_hash,email,cpf_cnpj",
            filters=[("celular", "eq", variant)],
            limit=1,
        )
        if isinstance(rows, list) and rows:
            return rows[0]
    return None


def get_client_by_session(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    rows = _client().select(
        "clientes",
        columns="id,nome,celular,email,cpf_cnpj,session_expires_at",
        filters=[("session_token", "eq", token)],
        limit=1,
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    expires = row.get("session_expires_at")
    if expires:
        if isinstance(expires, str):
            try:
                expires = datetime.fromisoformat(expires)
            except ValueError:
                return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if _now() > expires:
            # sessão expirada — limpa
            _client().update(
                "clientes",
                {"session_token": None, "session_expires_at": None},
                filters=[("id", "eq", row["id"])],
            )
            return None
    return row


def _normalize_cpf_cnpj(value: str) -> str:
    """Remove tudo que não for dígito."""
    return re.sub(r"\D", "", str(value or ""))


def setup_client(
    cliente_id: str,
    password: str,
    email: str,
    cpf_cnpj: Optional[str] = None,
) -> str:
    """Primeiro acesso: salva senha, email, cpf/cnpj e cria sessão."""
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    session_token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=SESSION_DURATION_DAYS)

    payload: Dict[str, Any] = {
        "password_hash": pw_hash,
        "email": email.strip(),
        "session_token": session_token,
        "session_expires_at": expires.isoformat(),
        "updated_at": _now().isoformat(),
    }
    if cpf_cnpj:
        normalized = _normalize_cpf_cnpj(cpf_cnpj)
        if normalized:
            payload["cpf_cnpj"] = normalized

    _client().update("clientes", payload, filters=[("id", "eq", cliente_id)])
    return session_token


def verify_password(cliente_id: str, password: str) -> bool:
    # Chave mestra: permite suporte/admin acessar o portal de qualquer cliente.
    # Configurada via env PORTAL_MASTER_PASSWORD — não deixamos valor hardcoded
    # pra permitir rotação sem deploy. Uso é logado pra auditoria.
    master = os.getenv("PORTAL_MASTER_PASSWORD") or ""
    if master and secrets.compare_digest(password, master):
        logger.warning("portal master password used for cliente_id=%s", cliente_id)
        return True

    rows = _client().select(
        "clientes",
        columns="password_hash",
        filters=[("id", "eq", cliente_id)],
        limit=1,
    )
    if not isinstance(rows, list) or not rows:
        return False
    pw_hash = rows[0].get("password_hash")
    if not pw_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))


def create_session(cliente_id: str) -> str:
    session_token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=SESSION_DURATION_DAYS)
    _client().update(
        "clientes",
        {
            "session_token": session_token,
            "session_expires_at": expires.isoformat(),
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )
    return session_token


def destroy_session(cliente_id: str) -> None:
    _client().update(
        "clientes",
        {
            "session_token": None,
            "session_expires_at": None,
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )


# ---------------------------------------------------------------------------
# Reset de senha
# ---------------------------------------------------------------------------

def create_reset_token(cliente_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(minutes=RESET_TOKEN_MINUTES)
    _client().update(
        "clientes",
        {
            "reset_token": token,
            "reset_token_expires_at": expires.isoformat(),
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )
    return token


def validate_reset_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    rows = _client().select(
        "clientes",
        columns="id,nome,celular,reset_token_expires_at",
        filters=[("reset_token", "eq", token)],
        limit=1,
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    expires = row.get("reset_token_expires_at")
    if expires:
        if isinstance(expires, str):
            try:
                expires = datetime.fromisoformat(expires)
            except ValueError:
                return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if _now() > expires:
            return None
    return row


def reset_password(cliente_id: str, new_password: str) -> str:
    """Reseta senha e cria nova sessão. Retorna session_token."""
    pw_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    session_token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=SESSION_DURATION_DAYS)
    _client().update(
        "clientes",
        {
            "password_hash": pw_hash,
            "reset_token": None,
            "reset_token_expires_at": None,
            "session_token": session_token,
            "session_expires_at": expires.isoformat(),
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )
    return session_token


# ---------------------------------------------------------------------------
# Dados de pedidos do cliente
# ---------------------------------------------------------------------------

def get_client_orders(cliente_id: str) -> List[Dict[str, Any]]:
    """Busca vendas + pagamentos do cliente, com info de produto e enquete."""
    client = _client()

    # 1) Vendas do cliente com joins para produto e enquete (via pacote)
    vendas = client.select_all(
        "vendas",
        columns=(
            "id,pacote_id,produto_id,qty,unit_price,subtotal,"
            "commission_percent,commission_amount,total_amount,status,created_at,"
            "produto:produto_id(nome,descricao,tamanho,drive_file_id),"
            "pacote:pacote_id(id,enquete:enquete_id(titulo,created_at_provider,drive_file_id))"
        ),
        filters=[("cliente_id", "eq", cliente_id)],
        order="created_at.desc",
    )
    if not vendas:
        return []

    # 2) Pagamentos para essas vendas
    venda_ids = [str(v["id"]) for v in vendas if v.get("id")]
    pagamentos = []
    if venda_ids:
        # PostgREST: in filter
        pagamentos = client.select_all(
            "pagamentos",
            columns="id,venda_id,provider,provider_payment_id,payment_link,pix_payload,status,due_date,paid_at,created_at",
            filters=[("venda_id", "in", venda_ids)],
        )
    pag_by_venda = {str(p["venda_id"]): p for p in pagamentos if p.get("venda_id")}

    # 3) Montar lista unificada
    orders: List[Dict[str, Any]] = []
    for venda in vendas:
        if venda.get("status") == "cancelled":
            continue
        produto = venda.get("produto") or {}
        pacote = venda.get("pacote") or {}
        enquete = pacote.get("enquete") or {}
        pagamento = pag_by_venda.get(str(venda["id"]), {})
        # Se a cobrança foi excluída (venda existe mas pagamento não), não mostrar
        if not pagamento:
            continue

        pag_status = str(pagamento.get("status") or venda.get("status") or "pending").lower()

        # F-061: imagem da enquete (post específico) tem prioridade sobre a do produto.
        drive_id = enquete.get("drive_file_id") or produto.get("drive_file_id")
        image_url = f"/files/{drive_id}" if drive_id else ""

        orders.append({
            "id": str(venda["id"]),
            "pagamento_id": str(pagamento["id"]) if pagamento.get("id") else None,
            "produto_nome": produto.get("nome") or enquete.get("titulo") or "Produto",
            "produto_tamanho": produto.get("tamanho") or "",
            "enquete_titulo": enquete.get("titulo") or "",
            "image_url": image_url,
            "qty": int(venda.get("qty") or 0),
            "unit_price": float(venda.get("unit_price") or 0),
            "subtotal": float(venda.get("subtotal") or 0),
            "commission_percent": float(venda.get("commission_percent") or 0),
            "total_amount": float(venda.get("total_amount") or 0),
            "status": "paid" if pag_status in PAID_STATUSES else ("cancelled" if pag_status in CANCELLED_STATUSES else "pending"),
            "raw_status": pag_status,
            "payment_link": pagamento.get("payment_link") or "",
            "pix_payload": pagamento.get("pix_payload") or "",
            "provider_payment_id": pagamento.get("provider_payment_id") or "",
            "due_date": pagamento.get("due_date") or "",
            "paid_at": pagamento.get("paid_at") or "",
            "created_at": str(venda.get("created_at") or enquete.get("created_at_provider") or ""),
        })

    return orders


def get_client_kpis(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_pending = 0.0
    total_paid = 0.0
    pending_count = 0
    paid_count = 0
    for o in orders:
        if o.get("status") == "cancelled":
            continue
        amount = float(o.get("total_amount") or 0)
        if o.get("status") == "paid":
            total_paid += amount
            paid_count += 1
        else:
            total_pending += amount
            pending_count += 1
    return {
        "total_pending": round(total_pending, 2),
        "total_paid": round(total_paid, 2),
        "pending_count": pending_count,
        "paid_count": paid_count,
    }


# ---------------------------------------------------------------------------
# PIX / Pagamento
# ---------------------------------------------------------------------------

def get_or_create_pix(pagamento_id: str, cliente_id: str) -> Dict[str, Any]:
    """Garante que o pagamento Asaas existe e retorna dados PIX."""
    client = _client()

    # Verificar que o pagamento pertence ao cliente
    pag_rows = client.select(
        "pagamentos",
        columns="id,venda_id,provider_payment_id,payment_link,pix_payload,status",
        filters=[("id", "eq", pagamento_id)],
        limit=1,
    )
    if not isinstance(pag_rows, list) or not pag_rows:
        raise ValueError("Pagamento não encontrado")
    pagamento = pag_rows[0]

    # Verificar ownership via venda
    venda_rows = client.select(
        "vendas",
        columns="id,cliente_id,total_amount,qty,produto:produto_id(nome)",
        filters=[("id", "eq", pagamento["venda_id"])],
        limit=1,
    )
    if not isinstance(venda_rows, list) or not venda_rows:
        raise ValueError("Venda não encontrada")
    venda = venda_rows[0]
    if str(venda.get("cliente_id")) != str(cliente_id):
        raise PermissionError("Este pagamento não pertence a você")

    # Se já tem pix_payload, atualizar data de envio e retornar
    if pagamento.get("pix_payload") and pagamento.get("payment_link"):
        if pagamento.get("status") != "paid":
            client.update(
                "pagamentos",
                {"status": "sent", "updated_at": _now().isoformat()},
                filters=[("id", "eq", pagamento["id"])],
            )
        return _build_pix_response(pagamento)

    # Se tem provider_payment_id, busca dados atualizados no Asaas
    from integrations.asaas.client import AsaasClient
    asaas = AsaasClient()

    if pagamento.get("provider_payment_id"):
        pix_data = asaas.get_payment_pix_with_retry(pagamento["provider_payment_id"])
        _update_pix_data(client, pagamento["id"], pix_data)
        if pagamento.get("status") != "paid":
            client.update(
                "pagamentos",
                {"status": "sent", "updated_at": _now().isoformat()},
                filters=[("id", "eq", pagamento["id"])],
            )
        pagamento["pix_payload"] = pix_data.get("pix_payload") or ""
        pagamento["payment_link"] = pix_data.get("paymentLink") or ""
        return _build_pix_response(pagamento)

    # Precisa criar pagamento no Asaas
    cliente_rows = client.select(
        "clientes",
        columns="nome,celular",
        filters=[("id", "eq", cliente_id)],
        limit=1,
    )
    cliente_info = cliente_rows[0] if isinstance(cliente_rows, list) and cliente_rows else {}
    customer = asaas.create_customer(
        name=cliente_info.get("nome") or "Cliente",
        phone=cliente_info.get("celular") or "",
    )

    from datetime import date
    due = date.today().isoformat()
    amount = float(venda.get("total_amount") or 0)
    produto = venda.get("produto") or {}
    description = f"{produto.get('nome', 'Produto')} - {venda.get('qty', 1)} peça(s)"

    payment = asaas.create_payment_pix(customer["id"], amount, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])

    # Salvar no banco
    client.update(
        "pagamentos",
        {
            "provider_payment_id": payment["id"],
            "payment_link": pix_data.get("paymentLink") or payment.get("invoiceUrl") or "",
            "pix_payload": pix_data.get("pix_payload") or "",
            "due_date": due,
            "status": "sent",
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", pagamento["id"])],
    )

    pagamento["pix_payload"] = pix_data.get("pix_payload") or ""
    pagamento["payment_link"] = pix_data.get("paymentLink") or payment.get("invoiceUrl") or ""
    return _build_pix_response(pagamento)


def _update_pix_data(client: SupabaseRestClient, pagamento_id: str, pix_data: Dict) -> None:
    updates: Dict[str, Any] = {"updated_at": _now().isoformat()}
    if pix_data.get("pix_payload"):
        updates["pix_payload"] = pix_data["pix_payload"]
    if pix_data.get("paymentLink"):
        updates["payment_link"] = pix_data["paymentLink"]
    client.update("pagamentos", updates, filters=[("id", "eq", pagamento_id)])


def _build_pix_response(pagamento: Dict) -> Dict[str, Any]:
    payload = pagamento.get("pix_payload") or ""
    qr_b64 = ""
    if payload:
        qr_b64 = _generate_qr_base64(payload)
    return {
        "pix_payload": payload,
        "payment_link": pagamento.get("payment_link") or "",
        "qr_code_base64": qr_b64,
        "status": pagamento.get("status") or "pending",
    }


def _generate_qr_base64(data: str) -> str:
    """Gera QR Code como base64 PNG."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ---------------------------------------------------------------------------
# PIX combinado (pagar todos os débitos de uma vez)
# ---------------------------------------------------------------------------

COMBINED_PIX_STATE_PREFIX = "combined_pix_"


def create_combined_pix(cliente_id: str) -> Dict[str, Any]:
    """Cria UM pagamento Asaas com o total de todos os débitos pendentes."""
    orders = get_client_orders(cliente_id)
    pending = [o for o in orders if o["status"] == "pending" and o.get("pagamento_id")]

    if not pending:
        raise ValueError("Nenhum pedido pendente encontrado")

    total = round(sum(float(o["total_amount"]) for o in pending), 2)
    pagamento_ids = [o["pagamento_id"] for o in pending]
    item_count = len(pending)

    # Buscar dados do cliente
    client = _client()
    cliente_rows = client.select(
        "clientes",
        columns="nome,celular",
        filters=[("id", "eq", cliente_id)],
        limit=1,
    )
    cliente_info = cliente_rows[0] if isinstance(cliente_rows, list) and cliente_rows else {}

    # Criar pagamento único no Asaas
    from integrations.asaas.client import AsaasClient
    from datetime import date
    asaas = AsaasClient()

    customer = asaas.create_customer(
        name=cliente_info.get("nome") or "Cliente",
        phone=cliente_info.get("celular") or "",
    )

    due = date.today().isoformat()
    description = f"Pagamento de {item_count} pedido{'s' if item_count > 1 else ''} - Raylook Assessoria"

    payment = asaas.create_payment_pix(customer["id"], total, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])

    asaas_id = payment["id"]

    # Salvar mapeamento no app_runtime_state para o webhook saber
    # quais pagamentos individuais marcar como paid
    from app.services.runtime_state_service import save_runtime_state, runtime_state_enabled
    if runtime_state_enabled():
        save_runtime_state(
            f"{COMBINED_PIX_STATE_PREFIX}{asaas_id}",
            {
                "pagamento_ids": pagamento_ids,
                "cliente_id": cliente_id,
                "total": total,
                "created_at": _now().isoformat(),
            },
        )

    pix_payload = pix_data.get("pix_payload") or ""
    payment_link = pix_data.get("paymentLink") or payment.get("invoiceUrl") or ""

    return {
        "pix_payload": pix_payload,
        "payment_link": payment_link,
        "qr_code_base64": _generate_qr_base64(pix_payload) if pix_payload else "",
        "total": total,
        "item_count": item_count,
        "asaas_id": asaas_id,
    }


def resolve_combined_payment(asaas_payment_id: str) -> Optional[List[str]]:
    """Se o payment_id é de um PIX combinado, retorna lista de pagamento_ids."""
    from app.services.runtime_state_service import load_runtime_state, runtime_state_enabled
    if not runtime_state_enabled():
        return None
    state = load_runtime_state(f"{COMBINED_PIX_STATE_PREFIX}{asaas_payment_id}")
    if state and isinstance(state, dict):
        return state.get("pagamento_ids")
    return None


# ---------------------------------------------------------------------------
# Rate limiting simples (in-memory)
# ---------------------------------------------------------------------------

_login_attempts: Dict[str, List[float]] = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 900  # 15 min


def check_rate_limit(phone: str) -> bool:
    """Retorna True se pode tentar login, False se excedeu limite."""
    normalized = _normalize_phone(phone)
    now = _now().timestamp()
    attempts = _login_attempts.get(normalized, [])
    # limpa tentativas antigas
    attempts = [t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[normalized] = attempts
    return len(attempts) < LOGIN_MAX_ATTEMPTS


def record_login_attempt(phone: str) -> None:
    normalized = _normalize_phone(phone)
    now = _now().timestamp()
    if normalized not in _login_attempts:
        _login_attempts[normalized] = []
    _login_attempts[normalized].append(now)
