"""Edição de pacote fechado (status='closed').

Diferença crítica pro edit de pacote confirmado (`confirmed_package_sync_service`):
pacote fechado AINDA não tem `vendas` nem `pagamentos` — esses só nascem no
approve (`SalesService.approve_package`). Então a edição é bem mais simples:
só inserir/remover linhas em `pacote_clientes`.

Regras do fluxo (pedido da Alana, 19/04):
  - Membros trocáveis vêm só da **fila da mesma enquete** (votos com status='in'
    que não estão em nenhum outro pacote não-cancelado da enquete). Nunca de
    outros pacotes (nem fechados nem confirmados).
  - Membro removido volta automaticamente pra fila: basta apagar a linha em
    `pacote_clientes` (o voto continua com status='in' e a query da fila filtra
    por ausência em `pacote_clientes`).
  - Total do pacote precisa continuar = 24 peças.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.confirmed_package_edit_service import (
    _clean_phone,
    diff_votes_by_phone,
    normalize_votes_payload,
)
from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.closed_package_edit")

COMMISSION_PERCENT = 13.0


class ClosedPackageNotFound(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc_financials(unit_price: float, qty: int) -> Dict[str, float]:
    subtotal = round(unit_price * qty, 2)
    commission_amount = round(subtotal * (COMMISSION_PERCENT / 100.0), 2)
    total_amount = round(subtotal + commission_amount, 2)
    return {"subtotal": subtotal, "commission_amount": commission_amount, "total_amount": total_amount}


def _load_pacote_context(sb: SupabaseRestClient, pacote_uuid: str) -> Optional[Dict[str, Any]]:
    row = sb.select(
        "pacotes",
        columns="id,enquete_id,status,enquete:enquete_id(produto_id,produtos(valor_unitario))",
        filters=[("id", "eq", pacote_uuid)],
        single=True,
    )
    if not isinstance(row, dict):
        return None
    enquete = row.get("enquete") or {}
    produto = enquete.get("produtos") or {}
    unit_price = float(produto.get("valor_unitario") or 0)
    return {
        "pacote_id": row["id"],
        "enquete_id": row["enquete_id"],
        "status": row.get("status"),
        "produto_id": enquete.get("produto_id"),
        "unit_price": unit_price,
    }


def _fetch_selected_votes(sb: SupabaseRestClient, pacote_uuid: str) -> List[Dict[str, Any]]:
    rows = sb.select(
        "pacote_clientes",
        columns="cliente_id,qty,cliente:cliente_id(nome,celular)",
        filters=[("pacote_id", "eq", pacote_uuid)],
    )
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for pc in rows:
        cliente = pc.get("cliente") or {}
        out.append({
            "phone": _clean_phone(cliente.get("celular")),
            "name": cliente.get("nome") or "",
            "qty": int(pc.get("qty") or 0),
        })
    return out


def _fetch_available_queue(sb: SupabaseRestClient, enquete_id: str, exclude_pacote_uuid: str) -> List[Dict[str, Any]]:
    """Votos da fila da enquete (status='in', qty>0) que NÃO estão em nenhum
    pacote não-cancelado (exceto o próprio pacote sendo editado — esses são os
    'selected' e vão numa coluna separada)."""
    votos = sb.select(
        "votos",
        columns="id,cliente_id,qty,voted_at,cliente:cliente_id(nome,celular)",
        filters=[("enquete_id", "eq", enquete_id), ("status", "eq", "in"), ("qty", "gt", 0)],
        order="voted_at.asc",
    )
    if not isinstance(votos, list):
        return []

    # Clientes já alocados em algum pacote não-cancelado da enquete
    pacotes_enquete = sb.select(
        "pacotes",
        columns="id,status",
        filters=[("enquete_id", "eq", enquete_id)],
    )
    busy_clientes: set = set()
    if isinstance(pacotes_enquete, list):
        active_ids = [
            str(p["id"]) for p in pacotes_enquete
            if str(p.get("status") or "").lower() in ("open", "closed", "approved") and p.get("id")
        ]
        if active_ids:
            pcs = sb.select_all(
                "pacote_clientes",
                columns="cliente_id,pacote_id",
                filters=[("pacote_id", "in", active_ids)],
            )
            if isinstance(pcs, list):
                for pc in pcs:
                    busy_clientes.add(str(pc.get("cliente_id") or ""))

    # Re-inclui membros do próprio pacote (serão removidos depois via set diff)
    own_members = sb.select(
        "pacote_clientes",
        columns="cliente_id",
        filters=[("pacote_id", "eq", exclude_pacote_uuid)],
    )
    own_ids = set()
    if isinstance(own_members, list):
        own_ids = {str(m.get("cliente_id") or "") for m in own_members}

    free: List[Dict[str, Any]] = []
    for v in votos:
        cliente_id = str(v.get("cliente_id") or "")
        if cliente_id in own_ids:
            continue  # aparece em selected
        if cliente_id in busy_clientes:
            continue  # em outro pacote
        cliente = v.get("cliente") or {}
        free.append({
            "phone": _clean_phone(cliente.get("celular")),
            "name": cliente.get("nome") or "",
            "qty": int(v.get("qty") or 0),
        })
    return free


def get_edit_data(pacote_uuid: str) -> Dict[str, Any]:
    sb = SupabaseRestClient.from_settings()
    ctx = _load_pacote_context(sb, pacote_uuid)
    if not ctx:
        raise ClosedPackageNotFound(pacote_uuid)
    if str(ctx["status"] or "").lower() != "closed":
        raise ValueError(f"Pacote não está no estado 'closed' (status atual: {ctx['status']})")

    selected = _fetch_selected_votes(sb, pacote_uuid)
    available = _fetch_available_queue(sb, ctx["enquete_id"], pacote_uuid)
    total_selected = sum(int(v.get("qty") or 0) for v in selected)
    return {
        "package_id": pacote_uuid,
        "enquete_id": ctx["enquete_id"],
        "available_votes": available,
        "selected_votes": selected,
        "selected_qty": total_selected,
        "required_qty": 24,
    }


def apply_edit(pacote_uuid: str, new_votes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aplica a nova composição no `pacote_clientes`. Só mexe nessa tabela —
    nada de vendas/pagamentos (fechado ainda não tem).

    Valida total = 24 peças. Faz diff em memória e só atualiza o que mudou.
    """
    sb = SupabaseRestClient.from_settings()
    ctx = _load_pacote_context(sb, pacote_uuid)
    if not ctx:
        raise ClosedPackageNotFound(pacote_uuid)
    if str(ctx["status"] or "").lower() != "closed":
        raise ValueError(f"Pacote não está no estado 'closed'")

    new_votes = normalize_votes_payload(new_votes)
    total = sum(int(v.get("qty") or 0) for v in new_votes)
    if total != 24:
        raise ValueError(f"Pacote precisa ter exatamente 24 peças (got {total})")

    current = _fetch_selected_votes(sb, pacote_uuid)
    diff = diff_votes_by_phone(current, new_votes)

    summary = {"added": 0, "removed": 0, "changed_qty": 0}

    # REMOVED primeiro pra liberar unique index (pacote_id, cliente_id)
    for vote in diff["removed"]:
        phone = _clean_phone(vote.get("phone"))
        cliente = _find_cliente_by_phone(sb, phone)
        if not cliente:
            continue
        pc = sb.select(
            "pacote_clientes",
            columns="id",
            filters=[("pacote_id", "eq", pacote_uuid), ("cliente_id", "eq", cliente["id"])],
            single=True,
        )
        if isinstance(pc, dict):
            sb._request("DELETE", f"/rest/v1/pacote_clientes?id=eq.{pc['id']}")
            summary["removed"] += 1

    # CHANGED QTY: atualiza qty + financeiro
    for vote in diff["changed"]:
        phone = _clean_phone(vote.get("phone"))
        cliente = _find_cliente_by_phone(sb, phone)
        if not cliente:
            continue
        new_qty = int(vote["new_qty"])
        fin = _calc_financials(ctx["unit_price"], new_qty)
        sb.update(
            "pacote_clientes",
            {
                "qty": new_qty,
                "subtotal": fin["subtotal"],
                "commission_amount": fin["commission_amount"],
                "total_amount": fin["total_amount"],
                "updated_at": _now_iso(),
            },
            filters=[("pacote_id", "eq", pacote_uuid), ("cliente_id", "eq", cliente["id"])],
            returning="minimal",
        )
        summary["changed_qty"] += 1

    # ADDED: insere em pacote_clientes
    for vote in diff["added"]:
        phone = _clean_phone(vote.get("phone"))
        cliente = _find_cliente_by_phone(sb, phone)
        if not cliente:
            cliente = sb.upsert_one(
                "clientes",
                {"nome": (vote.get("name") or "Cliente").strip() or "Cliente", "celular": phone},
                on_conflict="celular",
            )
        # voto_id: precisa existir na enquete (FK NOT NULL)
        voto_row = sb.select(
            "votos",
            columns="id",
            filters=[("enquete_id", "eq", ctx["enquete_id"]), ("cliente_id", "eq", cliente["id"])],
            order="voted_at.desc",
            limit=1,
        )
        if isinstance(voto_row, list) and voto_row:
            voto_id = voto_row[0]["id"]
        else:
            voto = sb.upsert_one(
                "votos",
                {
                    "enquete_id": ctx["enquete_id"],
                    "cliente_id": cliente["id"],
                    "qty": int(vote["qty"]),
                    "status": "in",
                    "voted_at": _now_iso(),
                },
                on_conflict="enquete_id,cliente_id",
            )
            voto_id = voto["id"]

        qty = int(vote["qty"])
        fin = _calc_financials(ctx["unit_price"], qty)
        sb.insert(
            "pacote_clientes",
            {
                "pacote_id": pacote_uuid,
                "cliente_id": cliente["id"],
                "voto_id": voto_id,
                "produto_id": ctx["produto_id"],
                "qty": qty,
                "unit_price": ctx["unit_price"],
                "subtotal": fin["subtotal"],
                "commission_percent": COMMISSION_PERCENT,
                "commission_amount": fin["commission_amount"],
                "total_amount": fin["total_amount"],
                "status": "closed",
            },
            upsert=True,
            on_conflict="pacote_id,cliente_id",
        )
        summary["added"] += 1

    # Atualiza total_qty do pacote
    sb.update(
        "pacotes",
        {"total_qty": total, "updated_at": _now_iso()},
        filters=[("id", "eq", pacote_uuid)],
        returning="minimal",
    )

    logger.info(
        "apply_edit (closed) pacote=%s added=%d removed=%d changed=%d",
        pacote_uuid, summary["added"], summary["removed"], summary["changed_qty"],
    )
    return summary


def _find_cliente_by_phone(sb: SupabaseRestClient, phone: str) -> Optional[Dict[str, Any]]:
    if not phone:
        return None
    row = sb.select(
        "clientes",
        columns="id,nome,celular",
        filters=[("celular", "eq", phone)],
        single=True,
    )
    return row if isinstance(row, dict) else None
