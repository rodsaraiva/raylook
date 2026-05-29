"""Ledger de créditos do cliente.

Lançamentos:
  - credit (confirmed): gerado no cancelamento de pacote pago.
  - debit (pending):    reserva ao gerar PIX com crédito; não conta no saldo.
  - debit (confirmed):  abate efetivo (PIX pago ou cobertura total sem PIX).

Saldo = SUM(credit confirmed) − SUM(debit confirmed). Fonte única de verdade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.credit")


def _client() -> SupabaseRestClient:
    return SupabaseRestClient.from_settings()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_embed(value: Any) -> Dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def get_balance(cliente_id: str) -> float:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="tipo,valor",
        filters=[("cliente_id", "eq", cliente_id), ("status", "eq", "confirmed")],
    )
    total = 0.0
    for r in rows or []:
        v = float(r.get("valor") or 0)
        total += v if r.get("tipo") == "credit" else -v
    return round(total, 2)


def get_ledger(cliente_id: str) -> List[Dict[str, Any]]:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="id,tipo,status,valor,pacote_id,venda_id,pagamento_id,descricao,created_at",
        filters=[("cliente_id", "eq", cliente_id)],
        order="created_at.desc",
    )
    return rows or []


def list_balances() -> List[Dict[str, Any]]:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="cliente_id,tipo,valor,cliente:cliente_id(nome,celular)",
        filters=[("status", "eq", "confirmed")],
    )
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        cid = r.get("cliente_id")
        if not cid:
            continue
        cliente = _normalize_embed(r.get("cliente"))
        entry = agg.setdefault(cid, {
            "cliente_id": cid,
            "nome": cliente.get("nome") or "",
            "celular": cliente.get("celular") or "",
            "saldo": 0.0,
        })
        v = float(r.get("valor") or 0)
        entry["saldo"] += v if r.get("tipo") == "credit" else -v
    result = [
        {**e, "saldo": round(e["saldo"], 2)}
        for e in agg.values()
        if round(e["saldo"], 2) > 0
    ]
    result.sort(key=lambda e: e["saldo"], reverse=True)
    return result


def add_credit(
    cliente_id: str,
    valor: float,
    *,
    pacote_id: Optional[str] = None,
    venda_id: Optional[str] = None,
    descricao: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    valor = round(float(valor), 2)
    if valor <= 0:
        return None
    sb = _client()
    if venda_id:
        existing = sb.select(
            "creditos",
            columns="id",
            filters=[("venda_id", "eq", venda_id), ("tipo", "eq", "credit")],
            limit=1,
        )
        if isinstance(existing, list) and existing:
            return existing[0]
    payload = {
        "cliente_id": cliente_id, "tipo": "credit", "status": "confirmed",
        "valor": valor, "pacote_id": pacote_id, "venda_id": venda_id,
        "descricao": descricao, "created_by": created_by, "created_at": _now_iso(),
    }
    rows = sb.insert("creditos", {k: v for k, v in payload.items() if v is not None})
    return rows[0] if rows else None


def _existing_debit(sb, pagamento_id, asaas_payment_id):
    filters = [("tipo", "eq", "debit")]
    if pagamento_id:
        filters.append(("pagamento_id", "eq", pagamento_id))
    elif asaas_payment_id:
        filters.append(("asaas_payment_id", "eq", asaas_payment_id))
    else:
        raise ValueError("pagamento_id ou asaas_payment_id obrigatório")
    rows = sb.select("creditos", columns="id", filters=filters, limit=1)
    return rows[0] if isinstance(rows, list) and rows else None


def _add_debit(
    cliente_id: str,
    valor: float,
    *,
    status: str,
    pagamento_id: Optional[str] = None,
    asaas_payment_id: Optional[str] = None,
    descricao: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    valor = round(float(valor), 2)
    if valor <= 0:
        return None
    sb = _client()
    found = _existing_debit(sb, pagamento_id, asaas_payment_id)
    if found:
        return found
    payload = {
        "cliente_id": cliente_id, "tipo": "debit", "status": status,
        "valor": valor, "pagamento_id": pagamento_id,
        "asaas_payment_id": asaas_payment_id, "descricao": descricao,
        "created_at": _now_iso(),
    }
    rows = sb.insert("creditos", {k: v for k, v in payload.items() if v is not None})
    return rows[0] if rows else None


def add_pending_debit(cliente_id, valor, *, pagamento_id=None, asaas_payment_id=None, descricao=None):
    return _add_debit(cliente_id, valor, status="pending",
                      pagamento_id=pagamento_id, asaas_payment_id=asaas_payment_id, descricao=descricao)


def add_confirmed_debit(cliente_id, valor, *, pagamento_id=None, asaas_payment_id=None, descricao=None):
    return _add_debit(cliente_id, valor, status="confirmed",
                      pagamento_id=pagamento_id, asaas_payment_id=asaas_payment_id, descricao=descricao)


def confirm_debit(*, pagamento_id: Optional[str] = None, asaas_payment_id: Optional[str] = None) -> None:
    sb = _client()
    filters = [("tipo", "eq", "debit"), ("status", "eq", "pending")]
    if pagamento_id:
        filters.append(("pagamento_id", "eq", pagamento_id))
    elif asaas_payment_id:
        filters.append(("asaas_payment_id", "eq", asaas_payment_id))
    else:
        raise ValueError("pagamento_id ou asaas_payment_id obrigatório")
    sb.update("creditos", {"status": "confirmed"}, filters=filters)
