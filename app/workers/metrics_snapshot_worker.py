"""F-045: snapshot imutável de KPIs a cada hora cheia.

Motivação: os KPIs do dash são calculados em tempo real lendo o banco.
Se alguém editar dados antigos (custom_title, unit_price, cancelar venda),
as métricas históricas mudam retroativamente. Isso quebra análise
temporal confiável.

Este worker grava um snapshot dos principais indicadores em
`metrics_hourly_snapshots` a cada hora cheia. Append-only, imutável.
Preservar histórico é o objetivo.

Roda como task async infinito iniciado no startup. Dorme até a próxima
hora cheia e grava o snapshot. Idempotente (upsert por hour_bucket).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote as _urlquote


def _iso_q(ts: datetime) -> str:
    """URL-encoded ISO timestamp pra query strings do PostgREST.

    PostgREST interpreta `+` em `+00:00` como espaço no query param,
    corrompendo o timestamp (bug encontrado em 2026-04-09 que fazia
    todos os _count_in_range retornarem 0 desde F-045). Encodamos o
    `+` e `:` explicitamente.
    """
    return _urlquote(ts.isoformat(), safe="")

logger = logging.getLogger("raylook.workers.metrics_snapshot")


def _truncate_to_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def _seconds_until_next_hour(now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    next_hour = _truncate_to_hour(now) + timedelta(hours=1)
    return max(1.0, (next_hour - now).total_seconds())


def _count_in_range(sb, table: str, start: datetime, end: datetime, field: str) -> int:
    """Conta linhas de `table` onde `field` ∈ [start, end)."""
    try:
        resp = sb._request(
            "GET",
            f"/rest/v1/{table}?{field}=gte.{_iso_q(start)}&{field}=lt.{_iso_q(end)}&select=id",
            extra_headers={"Prefer": "count=exact"},
        )
        # PostgREST retorna o count no header Content-Range
        content_range = resp.headers.get("content-range", "")
        if "/" in content_range:
            total = content_range.split("/")[-1]
            if total != "*":
                return int(total)
        # fallback: count via len
        rows = resp.json() if resp.text else []
        return len(rows) if isinstance(rows, list) else 0
    except Exception as exc:
        logger.warning("_count_in_range(%s, %s) falhou: %s", table, field, exc)
        return 0


def _trim_metrics_for_snapshot(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Reduz o payload completo de /api/metrics pro essencial histórico.

    Mantém: votos.summary/by_hour/by_poll_today/by_customer_today/top_*,
    pacotes.summary/counts, enquetes.summary, generated_at.
    Remove: listas grandes de pacotes (open/closed/etc), customers_map,
    thumbnails, dados que tornam o jsonb gigante sem agregar histórico.
    """
    if not isinstance(metrics, dict):
        return {}
    out: Dict[str, Any] = {"generated_at": metrics.get("generated_at")}

    votos = metrics.get("votos") or {}
    if isinstance(votos, dict):
        keep = {}
        for k in (
            "summary",
            "by_hour",
            "by_poll_today",
            "by_customer_today",
            "by_customer_week",
            "top_polls",
            "top_clients",
            "today_so_far",
            "yesterday_until_same_hour",
            "same_weekday_last_week_same_hour",
            "avg_monthly_same_weekday",
            "pct_vs_yesterday_same_hour",
            "pct_vs_last_week_same_weekday",
            "pct_vs_monthly_avg",
        ):
            if k in votos:
                keep[k] = votos[k]
        # de pacotes só mantemos contagens, não as listas
        pkgs = votos.get("packages") or {}
        if isinstance(pkgs, dict):
            keep_pkgs = {}
            for k in ("summary", "counts"):
                if k in pkgs:
                    keep_pkgs[k] = pkgs[k]
            if keep_pkgs:
                keep["packages"] = keep_pkgs
        out["votos"] = keep

    enquetes = metrics.get("enquetes") or {}
    if isinstance(enquetes, dict):
        out["enquetes"] = {k: v for k, v in enquetes.items() if k in ("summary", "counts", "by_status")}

    return out


def build_snapshot_payload() -> Dict[str, Any]:
    """Monta o dict de snapshot olhando o estado atual do banco."""
    from app.services.supabase_service import SupabaseRestClient
    from app.services.finance_service import refresh_charge_snapshot, build_dashboard_stats
    from app.services.customer_service import refresh_customer_rows_snapshot

    now = datetime.now(timezone.utc)
    hour_bucket = _truncate_to_hour(now)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)
    hour_prev = hour_bucket - timedelta(hours=1)

    sb = SupabaseRestClient.from_settings()

    # === Votos ===
    votes_today = _count_in_range(sb, "votos", today_start, now, "voted_at")
    votes_24h = _count_in_range(sb, "votos", last_24h, now, "voted_at")
    votes_hour_delta = _count_in_range(sb, "votos", hour_prev, hour_bucket, "voted_at")

    # === Enquetes ===
    def _count_all(table: str, filters: str = "") -> int:
        try:
            path = f"/rest/v1/{table}?select=id{'&' + filters if filters else ''}"
            resp = sb._request("GET", path, extra_headers={"Prefer": "count=exact"})
            content_range = resp.headers.get("content-range", "")
            if "/" in content_range:
                total = content_range.split("/")[-1]
                if total != "*":
                    return int(total)
            rows = resp.json() if resp.text else []
            return len(rows) if isinstance(rows, list) else 0
        except Exception:
            return 0

    enquetes_total = _count_all("enquetes")
    enquetes_open = _count_all("enquetes", "status=eq.open")
    enquetes_closed = _count_all("enquetes", "status=eq.closed")
    enquetes_created_today = _count_in_range(sb, "enquetes", today_start, now, "created_at_provider")
    # F-048: enquetes ativas nas últimas 72h (status=open AND created_at_provider >= now-72h)
    cutoff_72h = now - timedelta(hours=72)
    enquetes_active_72h = _count_all(
        "enquetes",
        f"status=eq.open&created_at_provider=gte.{_iso_q(cutoff_72h)}",
    )

    # === Pacotes ===
    pacotes_open = _count_all("pacotes", "status=eq.open")
    pacotes_closed = _count_all("pacotes", "status=eq.closed")
    pacotes_approved = _count_all("pacotes", "status=eq.approved")
    pacotes_cancelled = _count_all("pacotes", "status=eq.cancelled")
    pacotes_approved_today = _count_in_range(sb, "pacotes", today_start, now, "approved_at")

    # === Financeiro (via snapshot já existente) ===
    charges = refresh_charge_snapshot()
    stats = build_dashboard_stats(charges)
    total_pending = float(stats.get("total_pending") or 0)
    total_paid = float(stats.get("total_paid") or 0)
    total_paid_today = float(stats.get("paid_today_total") or 0)
    total_cancelled = float(stats.get("total_cancelled") or 0)
    pending_count = int(stats.get("pending_count") or 0)
    paid_count = int(stats.get("paid_count") or 0)
    cancelled_count = int(stats.get("cancelled_count") or 0)
    active_count = int(stats.get("active_count") or (pending_count + paid_count))
    conversion_rate = (
        (total_paid / (total_pending + total_paid)) * 100
        if (total_pending + total_paid) > 0
        else 0
    )

    # === Clientes ===
    customer_rows = refresh_customer_rows_snapshot()
    customers_total = len(customer_rows) if isinstance(customer_rows, list) else 0
    customers_with_debt = sum(
        1 for c in (customer_rows or []) if float(c.get("total_debt") or 0) > 0
    )

    # === Fila WhatsApp ===
    try:
        from app.services.payment_queue_service import get_queue_snapshot
        queue_data = get_queue_snapshot(limit=1000) or {}
        queue_summary = queue_data.get("summary", {}) or {}
    except Exception:
        queue_summary = {}

    # === Webhook inbox ===
    webhook_received = _count_all("webhook_inbox", "status=eq.received")
    webhook_processed = _count_all("webhook_inbox", "status=eq.processed")
    webhook_failed = _count_all("webhook_inbox", "status=eq.failed")

    # === F-047 + F-048: pacotes (closed/approved) em enquetes ativas (72h) ===
    closed_packages_on_active_enquetes = 0
    try:
        open_ids_resp = sb._request(
            "GET",
            f"/rest/v1/enquetes?status=eq.open&created_at_provider=gte.{_iso_q(cutoff_72h)}&select=id",
        )
        open_rows = open_ids_resp.json() if open_ids_resp.text else []
        open_ids = [r.get("id") for r in (open_rows or []) if r.get("id")]
        if open_ids:
            # PostgREST suporta `in.(...)` com lista entre parênteses
            ids_csv = ",".join(open_ids)
            resp = sb._request(
                "GET",
                f"/rest/v1/pacotes?status=in.(closed,approved)&enquete_id=in.({ids_csv})&select=id",
                extra_headers={"Prefer": "count=exact"},
            )
            cr = resp.headers.get("content-range", "")
            if "/" in cr:
                total = cr.split("/")[-1]
                if total != "*":
                    closed_packages_on_active_enquetes = int(total)
    except Exception as exc:
        logger.warning("snapshot: falha ao contar pacotes fechados em ativas: %s", exc)

    # === Payload completo de /api/metrics (votos por hora, top clientes, top enquetes, etc) ===
    metrics_full: Dict[str, Any] = {}
    try:
        from app.services.metrics_service import load_metrics as svc_load_metrics
        metrics_full = _trim_metrics_for_snapshot(svc_load_metrics() or {})
    except Exception as exc:
        logger.warning("snapshot: falha ao carregar metrics_service.load_metrics: %s", exc)

    payload = {
        "hour_bucket": hour_bucket.isoformat(),
        "captured_at": now.isoformat(),
        # votos
        "votes_today_so_far": votes_today,
        "votes_last_24h": votes_24h,
        "votes_hour_delta": votes_hour_delta,
        # enquetes
        "enquetes_total": enquetes_total,
        "enquetes_open": enquetes_open,
        "enquetes_closed": enquetes_closed,
        "enquetes_created_today": enquetes_created_today,
        "enquetes_active_72h": enquetes_active_72h,
        # pacotes
        "pacotes_open": pacotes_open,
        "pacotes_closed": pacotes_closed,
        "pacotes_approved": pacotes_approved,
        "pacotes_cancelled": pacotes_cancelled,
        "pacotes_approved_today": pacotes_approved_today,
        # financeiro
        "total_pending_brl": round(total_pending, 2),
        "total_paid_brl": round(total_paid, 2),
        "total_paid_today_brl": round(total_paid_today, 2),
        "total_cancelled_brl": round(total_cancelled, 2),
        "pending_count": pending_count,
        "paid_count": paid_count,
        "cancelled_count": cancelled_count,
        "active_count": active_count,
        "conversion_rate_pct": round(conversion_rate, 2),
        # clientes
        "customers_total": customers_total,
        "customers_with_debt": customers_with_debt,
        # fila
        "queue_queued": int(queue_summary.get("queued") or 0),
        "queue_sending": int(queue_summary.get("sending") or 0),
        "queue_retry": int(queue_summary.get("retry") or 0),
        "queue_error": int(queue_summary.get("error") or 0),
        "queue_sent": int(queue_summary.get("sent") or 0),
        # webhook
        "webhook_received": webhook_received,
        "webhook_processed": webhook_processed,
        "webhook_failed": webhook_failed,
        # F-047: pacotes fechados em enquetes ativas
        "closed_packages_on_active_enquetes": closed_packages_on_active_enquetes,
        # meta
        "raw_stats": {
            "source": "metrics_snapshot_worker",
            "version": 2,
            "stats_full": {
                k: v
                for k, v in (stats or {}).items()
                if k != "timeline"  # timeline é grande e redundante
            },
            "metrics_full": metrics_full,  # /api/metrics (votos por hora, top clientes, top enquetes...)
        },
    }
    return payload


def persist_snapshot_sync(payload: Dict[str, Any]) -> Optional[str]:
    """Grava o snapshot no banco (idempotente por hour_bucket)."""
    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()
    try:
        result = sb.insert(
            "metrics_hourly_snapshots",
            payload,
            upsert=True,
            on_conflict="hour_bucket",
            returning="representation",
        )
        row = result[0] if isinstance(result, list) and result else None
        snap_id = row.get("id") if isinstance(row, dict) else None
        logger.info(
            "metrics snapshot gravado hour_bucket=%s id=%s votes_today=%s pending=%.2f",
            payload.get("hour_bucket"),
            snap_id,
            payload.get("votes_today_so_far"),
            payload.get("total_pending_brl") or 0,
        )
        return snap_id
    except Exception:
        logger.exception("Falha ao gravar metrics snapshot")
        return None


async def capture_once() -> Optional[str]:
    """Captura e grava um snapshot agora. Chamável manualmente."""
    payload = await asyncio.to_thread(build_snapshot_payload)
    return await asyncio.to_thread(persist_snapshot_sync, payload)


async def metrics_snapshot_loop() -> None:
    """Loop principal. Grava 1 snapshot imediato no startup + depois 1 por hora cheia."""
    logger.info("metrics_snapshot_loop iniciado")
    # Snapshot imediato pra ter pelo menos 1 linha logo no startup
    try:
        await capture_once()
    except Exception:
        logger.exception("Falha no snapshot inicial")

    while True:
        try:
            sleep_s = _seconds_until_next_hour()
            logger.info("metrics_snapshot_loop: dormindo %.0fs até próxima hora cheia", sleep_s)
            await asyncio.sleep(sleep_s)
            await capture_once()
        except asyncio.CancelledError:
            logger.info("metrics_snapshot_loop cancelado")
            return
        except Exception:
            logger.exception("Erro no ciclo do metrics_snapshot_loop; tentando de novo em 60s")
            await asyncio.sleep(60)
