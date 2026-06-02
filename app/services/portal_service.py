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

from app.services.enquete_title_parser import parse_enquete_title
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.portal")

SESSION_DURATION_DAYS = 30
RESET_TOKEN_MINUTES = 30
TEMP_PASSWORD_HOURS = 24
TEMP_PASSWORD_LENGTH = 8
# Alfabeto sem caracteres ambíguos (0/O, 1/I/l) — usuário vai digitar de novo
TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BCRYPT_ROUNDS = 12

PENDING_STATUSES = {"created", "sent", "pending", "enviando", "erro no envio"}
PAID_STATUSES = {"paid"}
CANCELLED_STATUSES = {"cancelled", "cancelado"}


class CpfMissingError(Exception):
    """Cliente sem CPF cadastrado — bloqueia criação de cobrança no Asaas.

    Frontend deve capturar via 412 e abrir modal pra coletar CPF antes
    de re-tentar o pagamento.
    """


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

    # Se não começa com 55 e não é número internacional começando com 1 (EUA/Canadá), adicionar DDI BR
    if not normalized.startswith("55") and not normalized.startswith("1") and len(normalized) >= 10:
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


def get_client_by_id(cliente_id: str) -> Optional[Dict[str, Any]]:
    if not cliente_id:
        return None
    rows = _client().select(
        "clientes",
        columns="id,nome,celular,email,cpf_cnpj",
        filters=[("id", "eq", str(cliente_id))],
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
        columns="id,nome,celular,email,cpf_cnpj,session_expires_at,must_change_password",
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


def update_cpf(cliente_id: str, cpf: str) -> None:
    """Atualiza CPF do cliente (usado pelo modal de contingência no portal).

    Validação de formato/checksum é responsabilidade do handler — o service
    só normaliza e grava.
    """
    _client().update(
        "clientes",
        {"cpf_cnpj": _normalize_cpf_cnpj(cpf), "updated_at": _now().isoformat()},
        filters=[("id", "eq", cliente_id)],
    )


def setup_client(
    cliente_id: str,
    password: str,
    email: str,
    cpf_cnpj: str,
) -> str:
    """Primeiro acesso: salva senha, email, CPF/CNPJ e cria sessão.

    CPF ou CNPJ obrigatório — validação já foi feita pelo handler
    (_is_valid_cpf_cnpj).
    """
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    session_token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=SESSION_DURATION_DAYS)

    payload: Dict[str, Any] = {
        "password_hash": pw_hash,
        "email": email.strip(),
        "cpf_cnpj": _normalize_cpf_cnpj(cpf_cnpj),
        "session_token": session_token,
        "session_expires_at": expires.isoformat(),
        "updated_at": _now().isoformat(),
    }

    _client().update("clientes", payload, filters=[("id", "eq", cliente_id)])
    return session_token


def verify_password(cliente_id: str, password: str) -> Optional[str]:
    """Verifica credencial e devolve o tipo usado: 'master', 'regular',
    'temp' ou None se inválida. Caller decide o que fazer com cada tipo
    (ex.: marcar must_change_password quando for 'temp').
    """
    # Chave mestra: permite suporte/admin acessar o portal de qualquer cliente.
    # Configurada via env PORTAL_MASTER_PASSWORD — não deixamos valor hardcoded
    # pra permitir rotação sem deploy. Uso é logado pra auditoria.
    master = os.getenv("PORTAL_MASTER_PASSWORD") or ""
    if master and secrets.compare_digest(password, master):
        logger.warning("portal master password used for cliente_id=%s", cliente_id)
        return "master"

    rows = _client().select(
        "clientes",
        columns="password_hash,temp_password_hash,temp_password_expires_at",
        filters=[("id", "eq", cliente_id)],
        limit=1,
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]

    pw_hash = row.get("password_hash")
    if pw_hash and bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8")):
        return "regular"

    temp_hash = row.get("temp_password_hash")
    temp_expires = row.get("temp_password_expires_at")
    if temp_hash and temp_expires and not _is_expired(temp_expires):
        if bcrypt.checkpw(password.encode("utf-8"), temp_hash.encode("utf-8")):
            return "temp"

    return None


def _is_expired(value: Any) -> bool:
    """True se o timestamp (str ISO ou datetime) já passou."""
    if not value:
        return True
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _now() > value


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
# Senha temporária + troca de senha
# ---------------------------------------------------------------------------

def generate_temp_password_plaintext() -> str:
    """Gera só o plaintext (sem persistir). Usado no caminho de "phone não
    existe" do reset pra devolver uma senha falsa e não revelar cadastro."""
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


def create_temp_password(cliente_id: str) -> str:
    """Gera senha temp de 24h e grava o hash. Retorna a senha em plaintext
    pra exibir uma única vez na tela. A senha original continua válida —
    login aceita as duas até a temp expirar (24h) ou o cliente trocar a senha
    (change_password zera o hash da temp).
    """
    plaintext = generate_temp_password_plaintext()
    pw_hash = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    expires = _now() + timedelta(hours=TEMP_PASSWORD_HOURS)
    _client().update(
        "clientes",
        {
            "temp_password_hash": pw_hash,
            "temp_password_expires_at": expires.isoformat(),
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )
    return plaintext


def mark_must_change_password(cliente_id: str, value: bool) -> None:
    _client().update(
        "clientes",
        {
            "must_change_password": value,
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )


def change_password(cliente_id: str, new_password: str) -> None:
    """Troca a senha permanente, invalida a temp e zera must_change_password.
    Mantém a sessão atual (cookie continua válido).
    """
    pw_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    _client().update(
        "clientes",
        {
            "password_hash": pw_hash,
            "temp_password_hash": None,
            "temp_password_expires_at": None,
            "must_change_password": False,
            "updated_at": _now().isoformat(),
        },
        filters=[("id", "eq", cliente_id)],
    )


# ---------------------------------------------------------------------------
# Dados de pedidos do cliente
# ---------------------------------------------------------------------------

def _delivery_status(
    pag_status: str,
    pacote: Dict[str, Any],
    pacote_cliente: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mapeia o estado logístico do pacote pro que aparece no portal do
    cliente. Pré-pagamento devolve 'pending' (mesma semântica antiga,
    significa 'aguardando pagamento'); pós-pagamento mapeia pelo estágio
    real DO CLIENTE — separado/enviado são granulares por pacote_cliente.
    Fallback em pkg.* protege pacotes legados sem backfill aplicado.

    Retorna dict com `code` e, quando pendente, `reasons`/`observations`.
    """
    if pag_status in CANCELLED_STATUSES:
        return {"code": "cancelled", "reasons": [], "observations": ""}
    if pag_status not in PAID_STATUSES:
        return {"code": "pending", "reasons": [], "observations": ""}
    if (pacote.get("status") or "").lower() == "cancelled":
        return {"code": "cancelled", "reasons": [], "observations": ""}
    pc = pacote_cliente or {}
    shipped = pc.get("shipped_at") or pacote.get("shipped_at")
    pdf = pc.get("pdf_sent_at") or pacote.get("pdf_sent_at")
    if shipped:
        return {"code": "enviado", "reasons": [], "observations": ""}
    if pdf:
        return {"code": "separado", "reasons": [], "observations": ""}
    reasons = pacote.get("pending_reasons") or []
    if isinstance(reasons, list) and reasons:
        return {
            "code": "pendente_logistica",
            "reasons": list(reasons),
            "observations": pacote.get("pending_observations") or "",
        }
    return {"code": "em_separacao", "reasons": [], "observations": ""}


def get_client_orders(cliente_id: str) -> List[Dict[str, Any]]:
    """Busca vendas + pagamentos do cliente, com info de produto e enquete."""
    client = _client()

    # 1) Vendas do cliente com joins para produto e enquete (via pacote)
    vendas = client.select_all(
        "vendas",
        columns=(
            "id,pacote_id,pacote_cliente_id,produto_id,qty,unit_price,subtotal,"
            "commission_percent,commission_amount,total_amount,status,created_at,"
            "produto:produto_id(nome,descricao,tamanho,drive_file_id),"
            "pacote:pacote_id(id,friendly_id,status,shipped_at,pdf_sent_at,pending_reasons,pending_observations,"
            "enquete:enquete_id(titulo,created_at_provider,drive_file_id))"
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

    # 2b) Pacote_clientes deste cliente — granularidade de separado/enviado.
    pc_ids = [str(v["pacote_cliente_id"]) for v in vendas if v.get("pacote_cliente_id")]
    pcs_rows = []
    if pc_ids:
        pcs_rows = client.select_all(
            "pacote_clientes",
            columns="id,shipped_at,pdf_sent_at",
            filters=[("id", "in", pc_ids)],
        ) or []
    pc_by_id = {str(pc["id"]): pc for pc in pcs_rows}

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
        # Pacote ainda formando (aberto/fechado) ou já cancelado não aparece
        # no portal — só faz sentido após approve.
        pkg_status = (pacote.get("status") or "").lower()
        if pkg_status in ("open", "closed", "cancelled"):
            continue

        pag_status = str(pagamento.get("status") or venda.get("status") or "pending").lower()
        if pag_status in CANCELLED_STATUSES:
            continue
        pc_row = pc_by_id.get(str(venda.get("pacote_cliente_id") or ""))
        delivery = _delivery_status(pag_status, pacote, pc_row)

        # F-061: imagem da enquete (post específico) tem prioridade sobre a do produto.
        # ?size=600 entrega thumb cacheada em vez do original (~200KB) — corta
        # banda da listagem em ~70% (a foto reduzida fica ~50-70KB).
        drive_id = enquete.get("drive_file_id") or produto.get("drive_file_id")
        image_url = f"/files/{drive_id}?size=600" if drive_id else ""

        parsed = parse_enquete_title(enquete.get("titulo") or "")

        orders.append({
            "id": str(venda["id"]),
            "pagamento_id": str(pagamento["id"]) if pagamento.get("id") else None,
            "pacote_codigo": pacote.get("friendly_id") or "",
            "produto_nome": produto.get("nome") or enquete.get("titulo") or "Produto",
            "produto_tamanho": produto.get("tamanho") or "",
            "enquete_titulo": enquete.get("titulo") or "",
            "image_url": image_url,
            "item": parsed["item"],
            "tecido": parsed["tecido"],
            "valor_extraido": parsed["valor"],
            "tamanho": parsed["tamanho"],
            "categoria": parsed["categoria"],
            "qty": int(venda.get("qty") or 0),
            "unit_price": float(venda.get("unit_price") or 0),
            "subtotal": float(venda.get("subtotal") or 0),
            "commission_percent": float(venda.get("commission_percent") or 0),
            "total_amount": float(venda.get("total_amount") or 0),
            "status": "paid" if pag_status in PAID_STATUSES else ("cancelled" if pag_status in CANCELLED_STATUSES else "pending"),
            "raw_status": pag_status,
            "delivery_status": delivery["code"],
            "pending_reasons": delivery["reasons"],
            "pending_observations": delivery["observations"],
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

    from app.services import credit_service
    total = float(venda.get("total_amount") or 0)

    # Já tem PIX gerado → retorna o existente reportando o crédito já registrado
    if pagamento.get("pix_payload") and pagamento.get("payment_link"):
        if pagamento.get("status") != "paid":
            client.update(
                "pagamentos",
                {"status": "sent", "updated_at": _now().isoformat()},
                filters=[("id", "eq", pagamento["id"])],
            )
        credito_ja = credit_service.get_applied_credit(pagamento["id"])
        return _build_pix_response(pagamento, extra={
            "saldo_antes": credit_service.get_balance(cliente_id),
            "credito_aplicado": credito_ja,
            "cobranca": round(total - credito_ja, 2),
            "pago_com_credito": False,
        })

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
        credito_ja = credit_service.get_applied_credit(pagamento["id"])
        return _build_pix_response(pagamento, extra={
            "saldo_antes": credit_service.get_balance(cliente_id),
            "credito_aplicado": credito_ja,
            "cobranca": round(total - credito_ja, 2),
            "pago_com_credito": False,
        })

    # Nenhuma cobrança Asaas ainda → único ponto onde o crédito é avaliado
    saldo_antes, credito_aplicado, cobranca = _apply_credit(cliente_id, total)

    if credito_aplicado > 0:
        _cancel_other_open_charges(cliente_id, keep_pagamento_ids=[pagamento["id"]])

    # Crédito cobre 100% → quita sem PIX (pagamento novo, sem cobrança Asaas)
    if cobranca <= 0:
        now = _now().isoformat()
        client.update(
            "pagamentos",
            {"status": "paid", "paid_at": now, "updated_at": now},
            filters=[("id", "eq", pagamento["id"])],
        )
        credit_service.add_confirmed_debit(
            cliente_id, credito_aplicado, pagamento_id=pagamento["id"],
            descricao="Pago com crédito",
        )
        return {
            "pix_payload": "", "payment_link": "", "qr_code_base64": "",
            "status": "paid", "saldo_antes": saldo_antes,
            "credito_aplicado": credito_aplicado, "cobranca": 0.0,
            "pago_com_credito": True,
        }
    credit_extra = {
        "saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado,
        "cobranca": cobranca, "pago_com_credito": False,
    }

    # Precisa criar pagamento no Asaas
    cliente_rows = client.select(
        "clientes",
        columns="nome,celular,cpf_cnpj",
        filters=[("id", "eq", cliente_id)],
        limit=1,
    )
    cliente_info = cliente_rows[0] if isinstance(cliente_rows, list) and cliente_rows else {}
    cpf = (cliente_info.get("cpf_cnpj") or "").strip()
    if not cpf:
        raise CpfMissingError("cliente sem CPF cadastrado")
    customer = asaas.create_customer(
        name=cliente_info.get("nome") or "Cliente",
        phone=cliente_info.get("celular") or "",
        cpf_cnpj=cpf,
    )

    from datetime import date
    due = date.today().isoformat()
    amount = cobranca
    produto = venda.get("produto") or {}
    description = f"{produto.get('nome', 'Produto')} - {venda.get('qty', 1)} peça(s)"

    payment = asaas.create_payment_pix(customer["id"], amount, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])

    if credito_aplicado > 0:
        credit_service.add_pending_debit(
            cliente_id, credito_aplicado, pagamento_id=pagamento["id"],
            descricao="Crédito aplicado",
        )

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
    return _build_pix_response(pagamento, extra=credit_extra)


def _update_pix_data(client: SupabaseRestClient, pagamento_id: str, pix_data: Dict) -> None:
    updates: Dict[str, Any] = {"updated_at": _now().isoformat()}
    if pix_data.get("pix_payload"):
        updates["pix_payload"] = pix_data["pix_payload"]
    if pix_data.get("paymentLink"):
        updates["payment_link"] = pix_data["paymentLink"]
    client.update("pagamentos", updates, filters=[("id", "eq", pagamento_id)])


def _build_pix_response(pagamento: Dict, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = pagamento.get("pix_payload") or ""
    qr_b64 = _generate_qr_base64(payload) if payload else ""
    out = {
        "pix_payload": payload,
        "payment_link": pagamento.get("payment_link") or "",
        "qr_code_base64": qr_b64,
        "status": pagamento.get("status") or "pending",
    }
    if extra:
        out.update(extra)
    return out


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


def _apply_credit(cliente_id: str, total: float):
    """Retorna (saldo_antes, credito_aplicado, cobranca) sem alterar o ledger."""
    from app.services import credit_service
    saldo = credit_service.get_balance(cliente_id)
    aplicado = round(min(saldo, total), 2)
    cobranca = round(total - aplicado, 2)
    return saldo, aplicado, cobranca


def _cancel_other_open_charges(cliente_id: str, keep_pagamento_ids) -> None:
    """Cancela as OUTRAS cobranças em aberto do cliente (serialização do crédito).

    Para cada pagamento individual com cobrança Asaas ativa (provider_payment_id,
    status created/sent, não pago) que NÃO esteja em keep_pagamento_ids: cancela
    no Asaas, reseta o pagamento p/ recarregável e remove o débito pending dele.
    Para cada PIX combinado do cliente no runtime_state (exceto os que cobrem só
    os keep): cancela no Asaas, remove o débito pending (asaas_payment_id) e apaga
    o runtime_state.
    """
    import json

    from app.services import credit_service
    from app.services.runtime_state_service import delete_runtime_state
    from integrations.asaas.client import AsaasClient

    keep = set(str(p) for p in (keep_pagamento_ids or []))
    client = _client()
    asaas = AsaasClient()

    # 1) pagamentos individuais com cobrança Asaas ativa
    rows = client.select_all(
        "pagamentos",
        columns="id,provider_payment_id,status,venda:venda_id(cliente_id)",
        filters=[("status", "in", ["created", "sent"])],
    ) or []
    for p in rows:
        pid = str(p.get("id") or "")
        if not pid or pid in keep:
            continue
        venda = p.get("venda")
        if isinstance(venda, list):
            venda = venda[0] if venda else {}
        if str((venda or {}).get("cliente_id") or "") != str(cliente_id):
            continue
        prov = p.get("provider_payment_id")
        if prov:
            try:
                asaas.cancel_payment(prov)
            except Exception:
                logger.warning("cancel_other_charges: falha ao cancelar Asaas %s", prov, exc_info=True)
        client.update(
            "pagamentos",
            {"provider_payment_id": None, "payment_link": None, "pix_payload": None,
             "due_date": None, "status": "created", "updated_at": _now().isoformat()},
            filters=[("id", "eq", pid)],
        )
        credit_service.remove_pending_debit(pagamento_id=pid)

    # 2) PIX combinados do cliente
    states = client.select_all(
        "app_runtime_state",
        columns="key,payload_json",
        filters=[("key", "like", f"{COMBINED_PIX_STATE_PREFIX}%")],
    ) or []
    for st in states:
        key = st.get("key") or ""
        payload = st.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if str(payload.get("cliente_id") or "") != str(cliente_id):
            continue
        pag_ids = set(str(x) for x in (payload.get("pagamento_ids") or []))
        # se o combinado cobre só os keep atuais, não cancela
        if pag_ids and pag_ids.issubset(keep):
            continue
        asaas_id = key[len(COMBINED_PIX_STATE_PREFIX):]
        if asaas_id:
            try:
                asaas.cancel_payment(asaas_id)
            except Exception:
                logger.warning("cancel_other_charges: falha ao cancelar combinado Asaas %s", asaas_id, exc_info=True)
            credit_service.remove_pending_debit(asaas_payment_id=asaas_id)
        try:
            delete_runtime_state(key)
        except Exception:
            logger.warning("cancel_other_charges: falha ao apagar runtime_state %s", key, exc_info=True)


def _mark_paid_with_credit(pagamento_ids):
    """Marca pagamentos pendentes como paid quando o crédito cobre 100% (sem PIX)."""
    client = _client()
    for pid in pagamento_ids:
        client.update(
            "pagamentos",
            {"status": "paid", "paid_at": _now().isoformat(), "updated_at": _now().isoformat()},
            filters=[("id", "eq", pid), ("status", "eq", "pending")],
        )


def create_combined_pix(cliente_id: str) -> Dict[str, Any]:
    """Cria UM pagamento Asaas com o total de todos os débitos pendentes."""
    orders = get_client_orders(cliente_id)
    pending = [o for o in orders if o["status"] == "pending" and o.get("pagamento_id")]

    if not pending:
        raise ValueError("Nenhum pedido pendente encontrado")

    total = round(sum(float(o["total_amount"]) for o in pending), 2)
    pagamento_ids = [o["pagamento_id"] for o in pending]
    item_count = len(pending)

    from app.services import credit_service

    saldo_antes, credito_aplicado, cobranca = _apply_credit(cliente_id, total)

    if credito_aplicado > 0:
        _cancel_other_open_charges(cliente_id, keep_pagamento_ids=pagamento_ids)

    # Crédito cobre 100% → quita sem PIX
    if cobranca <= 0:
        _mark_paid_with_credit(pagamento_ids)
        credit_service.add_confirmed_debit(
            cliente_id, credito_aplicado,
            pagamento_id=pagamento_ids[0],
            descricao=f"Pago com crédito — {item_count} pedido{'s' if item_count > 1 else ''}",
        )
        return {
            "pix_payload": "", "payment_link": "", "qr_code_base64": "",
            "total": total, "item_count": item_count,
            "saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado,
            "cobranca": 0.0, "pago_com_credito": True, "asaas_id": None,
        }

    # Buscar dados do cliente
    client = _client()
    cliente_rows = client.select(
        "clientes", columns="nome,celular,cpf_cnpj",
        filters=[("id", "eq", cliente_id)], limit=1,
    )
    cliente_info = cliente_rows[0] if isinstance(cliente_rows, list) and cliente_rows else {}
    cpf = (cliente_info.get("cpf_cnpj") or "").strip()
    if not cpf:
        raise CpfMissingError("cliente sem CPF cadastrado")

    from integrations.asaas.client import AsaasClient
    from datetime import date
    asaas = AsaasClient()
    customer = asaas.create_customer(
        name=cliente_info.get("nome") or "Cliente",
        phone=cliente_info.get("celular") or "", cpf_cnpj=cpf,
    )
    due = date.today().isoformat()
    description = f"Pagamento de {item_count} pedido{'s' if item_count > 1 else ''} - Raylook Assessoria"
    payment = asaas.create_payment_pix(customer["id"], cobranca, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])
    asaas_id = payment["id"]

    # Débito pendente — confirmado só quando o polling confirmar o pagamento
    if credito_aplicado > 0:
        credit_service.add_pending_debit(
            cliente_id, credito_aplicado, asaas_payment_id=asaas_id,
            descricao=f"Crédito aplicado em {item_count} pedido{'s' if item_count > 1 else ''}",
        )

    from app.services.runtime_state_service import save_runtime_state, runtime_state_enabled
    if runtime_state_enabled():
        save_runtime_state(
            f"{COMBINED_PIX_STATE_PREFIX}{asaas_id}",
            {"pagamento_ids": pagamento_ids, "cliente_id": cliente_id,
             "total": cobranca, "created_at": _now().isoformat()},
        )

    pix_payload = pix_data.get("pix_payload") or ""
    payment_link = pix_data.get("paymentLink") or payment.get("invoiceUrl") or ""
    return {
        "pix_payload": pix_payload, "payment_link": payment_link,
        "qr_code_base64": _generate_qr_base64(pix_payload) if pix_payload else "",
        "total": total, "item_count": item_count,
        "saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado,
        "cobranca": cobranca, "pago_com_credito": False, "asaas_id": asaas_id,
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
