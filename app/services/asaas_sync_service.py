"""Sincronização periódica de pagamentos com Asaas.

Consulta o status de todos os pagamentos pendentes no Asaas e marca
como pago os que foram pagos. Serve como GARANTIA caso o webhook
do Asaas falhe ou atrase.

Roda a cada 10 minutos por padrão.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.services.asaas_sync")

ASAAS_PAID_STATUSES = {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}

# Asaas opera em BRT (UTC-3). Quando retorna só a data em `confirmedDate`
# (ex: "2026-04-17"), convertendo ingenuamente pra UTC gera meia-noite UTC,
# que em BRT é 21:00 do dia anterior. Pra preservar o dia correto quando
# agregamos por dia BRT, interpretamos a data como meia-noite BRT = 03:00 UTC.
_BRT_OFFSET = timedelta(hours=3)


def _normalize_asaas_paid_at(payment_data: dict) -> str:
    """Retorna `paid_at` em ISO UTC, tratando Asaas BRT corretamente.

    - Se `confirmedDate`/`paymentDate` vier como data pura ("YYYY-MM-DD"),
      trata como meia-noite BRT e devolve `YYYY-MM-DDT03:00:00+00:00`.
    - Se vier como ISO datetime, devolve como está.
    - Fallback: agora (UTC).
    """
    raw = payment_data.get("confirmedDate") or payment_data.get("paymentDate")
    if raw:
        raw_str = str(raw).strip()
        # Formato de data pura (YYYY-MM-DD): interpreta como BRT midnight.
        if len(raw_str) == 10 and raw_str[4] == "-" and raw_str[7] == "-":
            try:
                d = datetime.strptime(raw_str, "%Y-%m-%d")
                dt_utc = d.replace(tzinfo=timezone.utc) + _BRT_OFFSET
                return dt_utc.isoformat()
            except Exception:
                return raw_str
        return raw_str
    return datetime.now(timezone.utc).isoformat()


async def sync_asaas_payments() -> int:
    """Busca todos os pagamentos pendentes e verifica status no Asaas.

    Sincroniza DOIS caminhos:
      1. Pagamentos individuais com provider_payment_id (cliente pagou um PIX avulso).
      2. PIX combinados do portal "Pagar todos" — UM pagamento Asaas mapeia
         pra N pagamentos individuais via `app_runtime_state` (prefixo
         `combined_pix_`). Se o Asaas reporta o combinado como pago, marca
         todos os pagamentos individuais associados como paid de uma vez.

    Retorna quantos pagamentos individuais foram atualizados para 'paid'.
    """
    try:
        from app.config import settings
        if getattr(settings, "RAYLOOK_SANDBOX", True):
            logger.debug("[asaas-stub] sync_asaas_payments: no-op em sandbox")
            return 0
    except Exception:
        pass

    if not supabase_domain_enabled():
        return 0

    try:
        sb = SupabaseRestClient.from_settings()

        from integrations.asaas.client import AsaasClient
        asaas = AsaasClient()

        updated_count = 0

        # ------------------------------------------------------------------
        # Caminho 1: pagamentos individuais com provider_payment_id
        # ------------------------------------------------------------------
        pending = sb.select_all(
            "pagamentos",
            columns="id,provider_payment_id,status",
            filters=[
                ("status", "in", ["created", "sent"]),
                ("provider_payment_id", "not.is", "null"),
            ],
        )
        pending = [p for p in (pending or []) if p.get("provider_payment_id")]

        logger.info("asaas_sync: verificando %d pagamentos individuais no Asaas", len(pending))

        for p in pending:
            pag_id = p["id"]
            asaas_id = p["provider_payment_id"]
            try:
                status = await asyncio.to_thread(asaas.get_payment_status, asaas_id)
                if str(status).upper() in ASAAS_PAID_STATUSES:
                    payment_data = await asyncio.to_thread(asaas.get_payment, asaas_id)
                    paid_at = _normalize_asaas_paid_at(payment_data)
                    sb.update(
                        "pagamentos",
                        {"status": "paid", "paid_at": paid_at},
                        filters=[("id", "eq", pag_id)],
                    )
                    updated_count += 1
                    logger.info(
                        "asaas_sync: pagamento individual %s marcado como paid (asaas=%s)",
                        pag_id, asaas_id,
                    )
            except Exception as exc:
                logger.warning(
                    "asaas_sync: falha ao consultar pagamento individual %s (asaas=%s): %s",
                    pag_id, asaas_id, exc,
                )

        # ------------------------------------------------------------------
        # Caminho 2: PIX combinados do portal "Pagar todos"
        # ------------------------------------------------------------------
        try:
            combined_updated = await _sync_combined_pix(sb, asaas)
            updated_count += combined_updated
        except Exception:
            logger.exception("asaas_sync: falha sincronizando PIX combinados")

        # Se houve atualizações, invalida snapshots do dashboard
        if updated_count > 0:
            try:
                from app.services.finance_service import refresh_charge_snapshot, refresh_dashboard_stats
                from app.services.customer_service import refresh_customer_rows_snapshot
                await asyncio.to_thread(refresh_charge_snapshot)
                await asyncio.to_thread(refresh_dashboard_stats)
                await asyncio.to_thread(refresh_customer_rows_snapshot)
            except Exception:
                logger.warning("asaas_sync: refresh snapshots falhou", exc_info=True)

        logger.info("asaas_sync: concluído, %d pagamentos marcados como paid", updated_count)
        return updated_count

    except Exception as exc:
        logger.error("asaas_sync: erro crítico: %s", exc, exc_info=True)
        return 0


async def _sync_combined_pix(sb, asaas) -> int:
    """Resolve PIX combinados pagos, marcando pagamentos individuais como paid.

    Cada entry em `app_runtime_state` com prefixo `combined_pix_<asaas_id>`
    mapeia UM pagamento Asaas pra N pagamentos individuais. Consultamos
    o Asaas; se o combinado estiver pago, propagamos pra todos os individuais
    que ainda não estão paid.
    """
    states = sb.select_all(
        "app_runtime_state",
        columns="key,payload_json",
        filters=[("key", "like", "combined_pix_%")],
    )
    if not states:
        return 0

    logger.info("asaas_sync: verificando %d PIX combinados no Asaas", len(states))

    updated = 0
    for state in states:
        key = state.get("key") or ""
        asaas_id = key.replace("combined_pix_", "", 1)
        payload = state.get("payload_json") or {}
        pag_ids = payload.get("pagamento_ids") or []
        if not asaas_id or not pag_ids:
            continue
        try:
            payment = await asyncio.to_thread(asaas.get_payment, asaas_id)
        except Exception as exc:
            logger.warning("asaas_sync: falha ao consultar combinado %s: %s", asaas_id, exc)
            continue

        status = str(payment.get("status") or "").upper()
        if status not in ASAAS_PAID_STATUSES:
            continue

        paid_at = _normalize_asaas_paid_at(payment)
        for pag_id in pag_ids:
            try:
                row = sb.select(
                    "pagamentos",
                    columns="id,status",
                    filters=[("id", "eq", pag_id)],
                    limit=1,
                )
                if not isinstance(row, list) or not row:
                    continue
                if str(row[0].get("status")) == "paid":
                    continue
                sb.update(
                    "pagamentos",
                    {"status": "paid", "paid_at": paid_at},
                    filters=[("id", "eq", pag_id)],
                    returning="minimal",
                )
                updated += 1
                logger.info(
                    "asaas_sync: pagamento individual %s marcado como paid (combinado %s)",
                    pag_id, asaas_id,
                )
            except Exception as exc:
                logger.warning(
                    "asaas_sync: falha ao marcar %s como paid (combinado %s): %s",
                    pag_id, asaas_id, exc,
                )

    return updated


async def start_asaas_sync_scheduler(interval_minutes: int = 10) -> None:
    """Loop infinito que roda sync_asaas_payments a cada N minutos."""
    logger.info(
        "asaas_sync: agendador iniciado (intervalo: %d minutos)",
        interval_minutes,
    )
    # Delay inicial para não competir com startup
    await asyncio.sleep(30)
    while True:
        try:
            await sync_asaas_payments()
        except Exception:
            logger.exception("asaas_sync: erro não tratado no loop")
        await asyncio.sleep(interval_minutes * 60)
