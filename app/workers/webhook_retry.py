"""Worker de retry para webhook_inbox parados (F-003).

O fluxo normal do webhook é síncrono dentro do request HTTP:
1. INSERT em webhook_inbox (status='received')
2. Processar evento
3. UPDATE para status='processed' ou 'failed'

Se o processo crashar/timeout entre (1) e (3), o registro fica zumbi
em status='received' e nunca é retentado. Este worker varre
periodicamente e reprocessa esses casos.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("raylook.webhook_retry")

# Parâmetros do worker
POLL_INTERVAL_SECONDS = 60         # varre a cada 1 min
STALE_THRESHOLD_MINUTES = 5        # considera zumbi se received há > 5min
BATCH_LIMIT = 20                   # no máximo 20 por ciclo


def _normalize_payload_to_events(payload: Any) -> List[Any]:
    """Recebe o payload_json bruto e retorna lista de WebhookEvent."""
    from app.services.whatsapp_domain_service import normalize_webhook_events

    try:
        return list(normalize_webhook_events(payload))
    except Exception as exc:
        logger.warning("failed to normalize stale webhook payload: %s", exc)
        return []


def _reprocess_one(client: Any, row: Dict[str, Any]) -> str:
    """Reprocessa um webhook_inbox parado. Retorna o novo status."""
    from app.services.whatsapp_domain_service import build_domain_services

    services = build_domain_services(client)
    payload = row.get("payload_json") or {}
    events = _normalize_payload_to_events(payload)

    if not events:
        return "failed"

    try:
        for event in events:
            if event.kind == "poll_created":
                services["poll_service"].upsert_poll(event)
            elif event.kind == "vote_updated":
                services["vote_service"].process_vote(event)
        return "processed"
    except Exception as exc:
        logger.exception("retry processing failed for inbox id=%s", row.get("id"))
        raise


def _scan_and_retry() -> Dict[str, int]:
    """Uma passada: busca parados e tenta reprocessar cada um."""
    from app.services.supabase_service import SupabaseRestClient

    client = SupabaseRestClient.from_settings()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MINUTES)).isoformat()

    try:
        stale = client.select(
            "webhook_inbox",
            columns="id,provider,event_kind,event_key,payload_json,received_at",
            filters=[("status", "eq", "received"), ("received_at", "lt", cutoff)],
            order="received_at.asc",
            limit=BATCH_LIMIT,
        )
    except Exception as exc:
        logger.warning("webhook_retry: failed to fetch stale rows: %s", exc)
        return {"scanned": 0, "processed": 0, "failed": 0}

    if not stale:
        return {"scanned": 0, "processed": 0, "failed": 0}

    processed = 0
    failed = 0
    for row in stale:
        row_id = row.get("id")
        try:
            new_status = _reprocess_one(client, row)
            client.update(
                "webhook_inbox",
                {
                    "status": new_status,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "error": None if new_status == "processed" else "retry_failed_normalize",
                },
                filters=[("id", "eq", row_id)],
                returning="minimal",
            )
            if new_status == "processed":
                processed += 1
                logger.info("webhook_retry: reprocessed id=%s event_key=%s", row_id, row.get("event_key"))
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            try:
                client.update(
                    "webhook_inbox",
                    {
                        "status": "failed",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "error": f"retry: {str(exc)[:900]}",
                    },
                    filters=[("id", "eq", row_id)],
                    returning="minimal",
                )
            except Exception:
                logger.exception("webhook_retry: failed to mark id=%s as failed", row_id)

    return {"scanned": len(stale), "processed": processed, "failed": failed}


async def webhook_retry_loop(stop_event: Optional[asyncio.Event] = None) -> None:
    """Loop principal. Chamado uma vez no startup via asyncio.create_task."""
    logger.info(
        "webhook_retry_loop started (interval=%ds, stale_threshold=%dmin, batch=%d)",
        POLL_INTERVAL_SECONDS,
        STALE_THRESHOLD_MINUTES,
        BATCH_LIMIT,
    )
    while True:
        if stop_event and stop_event.is_set():
            logger.info("webhook_retry_loop stop requested")
            return
        try:
            result = await asyncio.to_thread(_scan_and_retry)
            if result["scanned"] > 0:
                logger.info("webhook_retry cycle: %s", result)
        except Exception:
            logger.exception("webhook_retry cycle crashed; will retry next interval")
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("webhook_retry_loop cancelled")
            return
