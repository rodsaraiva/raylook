"""
Background Tasks: periodically backfill missing product images.

Runs as a FastAPI background task triggered on startup and
also on each webhook ingestion to catch new enquetes without images.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List

from app.config import settings
from app.services.group_context_service import monitored_chat_ids
from app.services.drive_image_service import _update_enquete_drive_ids
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
from images.thumbs import ensure_thumbnail_for_image_url
from integrations.google_drive import GoogleDriveClient

logger = logging.getLogger("raylook.workers.background_tasks")

_POLL_INTERVAL_SECONDS = 120  # check every 2 minutes


async def _fetch_missing_product_images(limit: int) -> List[Dict[str, object]]:
    if not supabase_domain_enabled():
        logger.debug("backfill_missing_product_images: domínio não habilitado, skipping")
        return []

    client = SupabaseRestClient.from_settings()
    filters = [("produto_id", "not.is", "null")]
    target_chat_ids = monitored_chat_ids()
    if len(target_chat_ids) == 1:
        filters.append(("chat_id", "eq", target_chat_ids[0]))
    elif target_chat_ids:
        filters.append(("chat_id", "in", target_chat_ids))

    rows = client.select(
        "enquetes",
        columns=(
            "external_poll_id,chat_id,created_at_provider,produto_id,"
            "drive_file_id,"
            "produto:produto_id(id,drive_file_id)"
        ),
        filters=[*filters, ("drive_file_id", "is", "null")],
        order="created_at_provider.desc",
        limit=limit,
    )
    if not isinstance(rows, list):
        return []

    # F-066: "sem imagem" = ENQUETE sem drive_file_id próprio. Ignoramos o
    # produto — um produto pode ser compartilhado entre várias enquetes com
    # fotos diferentes (reposições, novos posts). Se a enquete nova não tem
    # imagem própria, ela precisa de attach_poll_image mesmo que o produto
    # tenha uma imagem antiga herdada.
    return rows


async def _fetch_recent_drive_thumbnail_candidates(limit: int) -> List[Dict[str, object]]:
    if not supabase_domain_enabled():
        logger.debug("backfill_recent_drive_thumbnails: domínio não habilitado, skipping")
        return []

    client = SupabaseRestClient.from_settings()
    filters = [("produto_id", "not.is", "null")]
    target_chat_ids = monitored_chat_ids()
    if len(target_chat_ids) == 1:
        filters.append(("chat_id", "eq", target_chat_ids[0]))
    elif target_chat_ids:
        filters.append(("chat_id", "in", target_chat_ids))

    rows = client.select(
        "enquetes",
        columns=(
            "external_poll_id,chat_id,created_at_provider,produto_id,"
            "drive_file_id,drive_folder_id,"
            "produto:produto_id(id,drive_file_id,drive_folder_id)"
        ),
        filters=filters,
        order="created_at_provider.desc",
        limit=limit,
    )
    if not isinstance(rows, list):
        return []
    return rows


def _backfill_drive_thumbnail_for_row_sync(row: Dict[str, object]) -> bool:
    poll_id = str(row.get("external_poll_id") or "").strip()
    produto = row.get("produto") or {}
    produto_id = str((produto.get("id") or row.get("produto_id") or "")).strip()
    # F-061: prioriza imagem da própria enquete; cai no produto só pra legado.
    drive_file_id = (
        str(row.get("drive_file_id") or "").strip()
        or str(produto.get("drive_file_id") or "").strip()
    )
    drive_folder_id = (
        str(row.get("drive_folder_id") or "").strip()
        or str(produto.get("drive_folder_id") or "").strip()
    )
    if not poll_id or not produto_id:
        return False

    drive = GoogleDriveClient()

    if not drive_file_id and drive_folder_id:
        try:
            drive_file_id = str(drive.find_latest_file_id_in_folder(drive_folder_id) or "").strip()
        except Exception as exc:
            logger.warning(
                "backfill_drive_thumbnail: failed file lookup in folder poll_id=%s folder_id=%s: %s",
                poll_id,
                drive_folder_id,
                exc,
            )

    if not drive_file_id:
        try:
            folder_id, file_id = drive.find_latest_folder_and_file_by_name(poll_id)
        except Exception as exc:
            logger.warning("backfill_drive_thumbnail: failed folder lookup poll_id=%s: %s", poll_id, exc)
            return False
        drive_folder_id = str(folder_id or "").strip()
        drive_file_id = str(file_id or "").strip()
        if drive_file_id:
            try:
                _update_enquete_drive_ids(poll_id, drive_file_id, drive_folder_id)
            except Exception as exc:
                logger.warning(
                    "backfill_drive_thumbnail: failed enquete update poll_id=%s: %s",
                    poll_id,
                    exc,
                )

    if not drive_file_id:
        return False

    image_url = drive.get_public_url(drive_file_id)
    thumb_url = ensure_thumbnail_for_image_url(image_url)
    if not thumb_url:
        return False

    logger.info(
        "backfill_drive_thumbnail: ensured thumb poll_id=%s drive_file_id=%s thumb=%s",
        poll_id,
        drive_file_id,
        thumb_url,
    )
    return True


async def _backfill_drive_thumbnail_via_whapi(row: Dict[str, object]) -> bool:
    from app.services.drive_image_service import attach_poll_image

    poll_id = str(row.get("external_poll_id") or "").strip()
    chat_id = str(row.get("chat_id") or "").strip()
    produto_id = str((row.get("produto") or {}).get("id") or row.get("produto_id") or "").strip()
    created_at_provider = row.get("created_at_provider")
    if not poll_id or not chat_id or not produto_id or not created_at_provider:
        return False

    try:
        poll_dt = datetime.fromisoformat(str(created_at_provider).replace("Z", "+00:00"))
        poll_ts = int(poll_dt.timestamp())
    except Exception:
        poll_ts = 0

    drive_file_id = await attach_poll_image(
        poll_id=poll_id,
        chat_id=chat_id,
        poll_ts=poll_ts,
        produto_id=produto_id,
    )
    if not drive_file_id:
        return False

    try:
        image_url = GoogleDriveClient().get_public_url(str(drive_file_id))
        thumb_url = await asyncio.to_thread(ensure_thumbnail_for_image_url, image_url)
    except Exception as exc:
        logger.warning(
            "backfill_drive_thumbnail_via_whapi: thumb generation failed poll_id=%s drive_file_id=%s: %s",
            poll_id,
            drive_file_id,
            exc,
        )
        return False

    if not thumb_url:
        return False

    logger.info(
        "backfill_drive_thumbnail_via_whapi: ensured thumb poll_id=%s drive_file_id=%s thumb=%s",
        poll_id,
        drive_file_id,
        thumb_url,
    )
    return True


def _attach_existing_thumbnails_to_snapshot() -> bool:
    from app.services.metrics_service import load_metrics, save_metrics
    from images.thumbs import attach_existing_thumbnails

    try:
        data = load_metrics()
    except FileNotFoundError:
        return False

    if not isinstance(data, dict):
        return False
    if not attach_existing_thumbnails(data):
        return False
    save_metrics(data)
    return True


async def backfill_missing_product_images(*, wait_for_completion: bool = False, limit: int = 20) -> Dict[str, int]:
    """
    Fetch enquetes that have produto_id but the linked produto has no drive_file_id.
    For each, trigger attach_poll_image in the background.

    This fires automatically after webhook ingestion and on a timer.
    """
    try:
        missing = await _fetch_missing_product_images(limit)
    except Exception as exc:
        logger.error("backfill_missing_product_images: Supabase query failed: %s", exc)
        return {"updated": 0, "failed": 0}

    if not missing:
        logger.debug("backfill_missing_product_images: all enquetes have images, nothing to do")
        return {"updated": 0, "failed": 0}

    logger.info(
        "backfill_missing_product_images: %d enquetes without product image", len(missing)
    )

    from app.services.drive_image_service import attach_poll_image

    tasks = []
    updated = 0
    failed = 0
    for e in missing:
        poll_id = e.get("external_poll_id")
        chat_id = e.get("chat_id")
        produto = e.get("produto") or {}
        produto_id = produto.get("id") or e.get("produto_id")

        if not all([poll_id, chat_id, produto_id]):
            logger.debug("backfill: skipping incomplete enquete %s", e)
            continue

        # Parse poll_ts from created_at_provider
        poll_ts: int = 0
        ts_str = e.get("created_at_provider")
        if ts_str:
            try:
                dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                poll_ts = int(dt.timestamp())
            except Exception:
                pass

        coro = attach_poll_image(
            poll_id=str(poll_id),
            chat_id=str(chat_id),
            poll_ts=poll_ts,
            produto_id=str(produto_id),
        )
        if wait_for_completion:
            tasks.append(coro)
        else:
            asyncio.create_task(coro)

    if not wait_for_completion:
        return {"updated": 0, "failed": 0}

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception) or not result:
            failed += 1
            continue
        updated += 1

    return {"updated": updated, "failed": failed}


async def backfill_recent_drive_thumbnails(*, wait_for_completion: bool = False, limit: int = 80) -> Dict[str, int]:
    try:
        rows = await _fetch_recent_drive_thumbnail_candidates(limit)
    except Exception as exc:
        logger.error("backfill_recent_drive_thumbnails: Supabase query failed: %s", exc)
        return {"updated": 0, "failed": 0, "snapshot_updated": 0}

    if not rows:
        logger.debug("backfill_recent_drive_thumbnails: no candidates")
        return {"updated": 0, "failed": 0, "snapshot_updated": 0}

    sync_tasks = []
    updated = 0
    failed = 0
    fallback_rows: List[Dict[str, object]] = []
    for row in rows:
        if wait_for_completion:
            sync_tasks.append(asyncio.to_thread(_backfill_drive_thumbnail_for_row_sync, row))
        else:
            asyncio.create_task(asyncio.to_thread(_backfill_drive_thumbnail_for_row_sync, row))

    if not wait_for_completion:
        return {"updated": 0, "failed": 0, "snapshot_updated": 0}

    results = await asyncio.gather(*sync_tasks, return_exceptions=True)
    for row, result in zip(rows, results):
        if isinstance(result, Exception):
            failed += 1
            continue
        if result:
            updated += 1
            continue
        fallback_rows.append(row)

    fallback_results = await asyncio.gather(
        *[_backfill_drive_thumbnail_via_whapi(row) for row in fallback_rows],
        return_exceptions=True,
    )
    for result in fallback_results:
        if isinstance(result, Exception) or not result:
            failed += 1
            continue
        updated += 1

    snapshot_updated = 0
    if updated > 0:
        try:
            snapshot_updated = 1 if await asyncio.to_thread(_attach_existing_thumbnails_to_snapshot) else 0
        except Exception as exc:
            logger.warning("backfill_recent_drive_thumbnails: snapshot thumbnail attach failed: %s", exc)

    return {"updated": updated, "failed": failed, "snapshot_updated": snapshot_updated}


async def run_periodic_backfill() -> None:
    """Long-running task: runs backfill_missing_product_images every 2 minutes."""
    logger.info("run_periodic_backfill: started (interval=%ds)", _POLL_INTERVAL_SECONDS)
    while True:
        try:
            await backfill_missing_product_images(wait_for_completion=True)
            await backfill_recent_drive_thumbnails(wait_for_completion=True)
        except Exception as exc:
            logger.error("run_periodic_backfill: unhandled error: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
