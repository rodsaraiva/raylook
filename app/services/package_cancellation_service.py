"""Cancelamento de pacote confirmado com cascade.

Regra de negócio (19/04/2026):

Quando a loja avisa que o estoque esgotou depois de confirmado, a admin precisa
cancelar o pacote inteiro do dashboard. Antes existia só o cancelamento por
pagamento no financeiro, que deixava o pacote com status='approved' (gerando
confusão pra equipe de separação).

Fluxo:
  - Se nenhum pagamento do pacote está pago: cancela em cascata
    (pacote + vendas + pagamentos).
  - Se algum pagamento está pago: exige `force=True` e preserva os pagos +
    suas vendas. Os demais (pendente/enviado) são cancelados.

Status resultantes:
  pacote.status            -> 'cancelled' (+ cancelled_at/cancelled_by)
  vendas[nao_paga].status  -> 'cancelled'
  pagamentos[nao_paga].status -> 'cancelled' (+ paid_at=NULL mantido)
  vendas[paga] / pagamentos[paga] -> inalterados
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.package_cancellation")

PAID_STATUS = "paid"


class PackageCancelBlocked(Exception):
    """Cancelamento exige confirmação explícita (há pagamentos pagos)."""

    def __init__(self, paid_info: List[Dict[str, Any]]):
        self.paid_info = paid_info
        super().__init__(f"{len(paid_info)} pagamento(s) já pago(s)")


class PackageNotFound(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_package(sb: SupabaseRestClient, package_id: str) -> Optional[Dict[str, Any]]:
    rows = sb.select(
        "pacotes",
        columns="id,status,enquete_id",
        filters=[("id", "eq", package_id)],
        limit=1,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def _fetch_sales_with_payments(
    sb: SupabaseRestClient, package_id: str
) -> List[Dict[str, Any]]:
    """Retorna vendas do pacote com pagamento embedado.

    Inclui vendas já canceladas também (pra decidir idempotência), mas o caller
    decide o que fazer.
    """
    rows = sb.select_all(
        "vendas",
        columns=(
            "id,status,cliente_id,total_amount,qty,"
            "cliente:cliente_id(nome,celular),"
            "pagamento:pagamentos(id,status,paid_at)"
        ),
        filters=[("pacote_id", "eq", package_id)],
    )
    if not isinstance(rows, list):
        return []
    # PostgREST retorna o embed 1:1 como lista — normalizar pra dict único
    normalized: List[Dict[str, Any]] = []
    for v in rows:
        pagamento = v.get("pagamento")
        if isinstance(pagamento, list):
            pagamento = pagamento[0] if pagamento else None
        v["pagamento"] = pagamento or {}
        normalized.append(v)
    return normalized


def _paid_clients_summary(sales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    paid: List[Dict[str, Any]] = []
    for v in sales:
        pag = v.get("pagamento") or {}
        if str(pag.get("status") or "").lower() != PAID_STATUS:
            continue
        cliente = v.get("cliente") or {}
        paid.append({
            "venda_id": str(v.get("id") or ""),
            "pagamento_id": str(pag.get("id") or ""),
            "cliente_nome": cliente.get("nome") or "",
            "cliente_celular": cliente.get("celular") or "",
            "qty": int(v.get("qty") or 0),
            "total_amount": float(v.get("total_amount") or 0),
            "paid_at": pag.get("paid_at"),
        })
    return paid


def preview_cancel(package_id: str) -> Dict[str, Any]:
    """Retorna info que o frontend precisa pra decidir se exibe aviso."""
    if not supabase_domain_enabled():
        raise RuntimeError("Supabase domain desabilitado")

    sb = SupabaseRestClient.from_settings()
    pkg = _fetch_package(sb, package_id)
    if not pkg:
        raise PackageNotFound(package_id)

    sales = _fetch_sales_with_payments(sb, package_id)
    paid = _paid_clients_summary(sales)
    pending_count = sum(
        1 for v in sales
        if str((v.get("pagamento") or {}).get("status") or "").lower() != PAID_STATUS
        and str(v.get("status") or "").lower() != "cancelled"
    )
    return {
        "package_id": package_id,
        "package_status": pkg.get("status"),
        "paid_count": len(paid),
        "paid_clients": paid,
        "pending_count": pending_count,
    }


def cancel_package(
    package_id: str,
    force: bool = False,
    cancelled_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Cancela o pacote em cascata.

    Raises:
      PackageNotFound: pacote não existe
      PackageCancelBlocked: há pagamentos pagos e force=False
    """
    if not supabase_domain_enabled():
        raise RuntimeError("Supabase domain desabilitado")

    sb = SupabaseRestClient.from_settings()
    pkg = _fetch_package(sb, package_id)
    if not pkg:
        raise PackageNotFound(package_id)

    if str(pkg.get("status") or "").lower() == "cancelled":
        # idempotente — já estava cancelado
        return {
            "package_id": package_id,
            "already_cancelled": True,
            "cancelled_sales": 0,
            "cancelled_payments": 0,
            "preserved_paid": 0,
        }

    sales = _fetch_sales_with_payments(sb, package_id)
    paid = _paid_clients_summary(sales)
    if paid and not force:
        raise PackageCancelBlocked(paid)

    now = _now_iso()
    cancelled_sales = 0
    cancelled_payments = 0

    for v in sales:
        pag = v.get("pagamento") or {}
        pag_status = str(pag.get("status") or "").lower()
        venda_id = str(v.get("id") or "")
        pagamento_id = str(pag.get("id") or "")

        if pag_status == PAID_STATUS:
            # preservado: venda e pagamento continuam como estão
            continue

        if str(v.get("status") or "").lower() != "cancelled" and venda_id:
            sb._request(
                "PATCH",
                f"/rest/v1/vendas?id=eq.{venda_id}",
                payload={"status": "cancelled", "updated_at": now},
                prefer="return=minimal",
            )
            cancelled_sales += 1

        if pagamento_id and pag_status != "cancelled":
            sb._request(
                "PATCH",
                f"/rest/v1/pagamentos?id=eq.{pagamento_id}",
                payload={
                    "status": "cancelled",
                    "paid_at": None,
                    "updated_at": now,
                },
                prefer="return=minimal",
            )
            cancelled_payments += 1

    # Pacote por último — depois que o cascade tá consistente
    sb._request(
        "PATCH",
        f"/rest/v1/pacotes?id=eq.{package_id}",
        payload={
            "status": "cancelled",
            "cancelled_at": now,
            "cancelled_by": cancelled_by or "admin",
            "updated_at": now,
        },
        prefer="return=minimal",
    )

    logger.info(
        "cancel_package id=%s force=%s paid_preserved=%d sales_cancelled=%d payments_cancelled=%d",
        package_id, force, len(paid), cancelled_sales, cancelled_payments,
    )

    return {
        "package_id": package_id,
        "cancelled_sales": cancelled_sales,
        "cancelled_payments": cancelled_payments,
        "preserved_paid": len(paid),
        "paid_clients": paid,
    }
