"""F-052: limpeza mensal de pastas antigas no Google Drive (RAG folder).

Regra: pastas de enquetes que não estão ativas (status != 'open') E foram
criadas há mais de 30 dias são deletadas do Drive. Mantém apenas enquetes
ativas pra não acumular imagens desnecessárias.

Também limpa pastas duplicadas (mesmo poll_id com 2+ pastas) — mantém
a mais recente e deleta as cópias.

Roda 1x por dia às 04:00 UTC (01:00 BRT, madrugada). Pode ser forçado
via POST /api/drive/cleanup.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("raylook.workers.drive_cleanup")

MAX_AGE_DAYS = 30


def _get_active_poll_ids() -> set:
    """Retorna set de external_poll_id das enquetes ativas (status='open')."""
    try:
        from app.services.supabase_service import SupabaseRestClient
        sb = SupabaseRestClient.from_settings()
        rows = sb.select(
            "enquetes",
            columns="external_poll_id",
            filters=[("status", "eq", "open")],
            limit=5000,
        )
        return {
            str(r.get("external_poll_id") or "").strip()
            for r in (rows or [])
            if r.get("external_poll_id")
        }
    except Exception:
        logger.exception("Falha ao buscar poll_ids ativos")
        return set()


def cleanup_drive_sync() -> Dict[str, Any]:
    """Executa a limpeza (blocking). Retorna relatório."""
    from integrations.google_drive import GoogleDriveClient

    drive = GoogleDriveClient()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    logger.info("drive_cleanup: listando pastas na RAG folder...")
    all_folders = drive.list_all_folders()
    logger.info("drive_cleanup: %d pastas encontradas", len(all_folders))

    active_polls = _get_active_poll_ids()
    logger.info("drive_cleanup: %d enquetes ativas (protegidas)", len(active_polls))

    # Agrupar por nome (poll_id)
    by_name: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for folder in all_folders:
        name = folder.get("name", "")
        by_name[name].append(folder)

    deleted_old = 0
    deleted_dupes = 0
    kept = 0
    errors = 0

    for poll_id, folders in by_name.items():
        # Ordenar por createdTime desc (mais recente primeiro)
        folders.sort(key=lambda f: f.get("createdTime", ""), reverse=True)

        # 1) Limpar duplicatas (manter só a primeira = mais recente)
        if len(folders) > 1:
            for dupe in folders[1:]:
                dupe_id = dupe.get("id")
                if dupe_id and drive.delete_file(dupe_id):
                    deleted_dupes += 1
                else:
                    errors += 1
            folders = folders[:1]  # manter só a mais recente

        # 2) Limpar pastas antigas de enquetes inativas
        primary = folders[0]
        created_str = primary.get("createdTime", "")
        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            created_at = now  # se não tem data, não deletar

        is_active = poll_id in active_polls
        is_old = created_at < cutoff

        if is_old and not is_active:
            fid = primary.get("id")
            if fid and drive.delete_file(fid):
                deleted_old += 1
            else:
                errors += 1
        else:
            kept += 1

    report = {
        "total_folders_found": len(all_folders),
        "unique_poll_ids": len(by_name),
        "active_polls_protected": len(active_polls),
        "deleted_duplicates": deleted_dupes,
        "deleted_old_inactive": deleted_old,
        "kept": kept,
        "errors": errors,
        "cutoff_date": cutoff.isoformat(),
        "ran_at": now.isoformat(),
    }
    logger.info("drive_cleanup: concluído — %s", report)
    return report


async def cleanup_drive_once() -> Dict[str, Any]:
    """Wrapper async."""
    return await asyncio.to_thread(cleanup_drive_sync)


async def drive_cleanup_loop() -> None:
    """Loop diário. Roda às 04:00 UTC (01:00 BRT)."""
    logger.info("drive_cleanup_loop iniciado")
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Próxima 04:00 UTC
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            sleep_s = (target - now).total_seconds()
            logger.info("drive_cleanup_loop: dormindo %.0fs até %s", sleep_s, target.isoformat())
            await asyncio.sleep(sleep_s)
            await cleanup_drive_once()
        except asyncio.CancelledError:
            logger.info("drive_cleanup_loop cancelado")
            return
        except Exception:
            logger.exception("drive_cleanup_loop: erro, tentando de novo em 1h")
            await asyncio.sleep(3600)
