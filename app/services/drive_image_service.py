"""
Drive Image Service

Orchestrates the full flow:
  webhook cache or WHAPI -> download -> Google Drive upload -> Supabase update

Called as a background task after a new poll (enquete) is created via webhook.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.services.recent_image_cache import find_recent_image
from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.services.drive_image")


async def attach_poll_image(
    poll_id: str,
    chat_id: str,
    poll_ts: int,
    produto_id: str,
) -> Optional[str]:
    """
    Fetch the image that was sent just before this poll in the WhatsApp group,
    upload it to Google Drive, and save the drive_file_id in the enquete row.

    F-061: imagem fica na enquete (não no produto), pra que posts com título
    idêntico não compartilhem a mesma foto.
    """
    import asyncio

    try:
        result = await asyncio.to_thread(
            _attach_poll_image_sync, poll_id, chat_id, poll_ts, produto_id
        )
        return result
    except Exception as exc:
        logger.error(
            "attach_poll_image failed poll_id=%s chat_id=%s: %s",
            poll_id, chat_id, exc, exc_info=True
        )
        return None


def _attach_poll_image_sync(
    poll_id: str,
    chat_id: str,
    poll_ts: int,
    produto_id: str,
) -> Optional[str]:
    """Blocking implementation run inside asyncio.to_thread."""
    from integrations.google_drive import GoogleDriveClient
    from integrations.whapi import WHAPIClient

    whapi = WHAPIClient()
    media_id: Optional[str] = None
    image_msg_id = poll_id

    cached = find_recent_image(chat_id=chat_id, poll_ts=poll_ts)
    if cached:
        media_id = str(cached.get("media_id") or "").strip() or None
        image_msg_id = str(cached.get("message_id") or poll_id).strip() or poll_id
        logger.info(
            "attach_poll_image: using cached webhook image chat_id=%s poll_id=%s media_id=%s",
            chat_id,
            poll_id,
            media_id,
        )

    if not media_id:
        logger.info("attach_poll_image: fetching messages chat_id=%s poll_ts=%s", chat_id, poll_ts)
        try:
            messages = whapi.get_recent_messages(chat_id=chat_id, time_to=poll_ts, limit=30)
        except Exception as exc:
            logger.error("attach_poll_image: WHAPI get_recent_messages failed: %s", exc)
            return None

        image_msg = whapi.find_image_before_poll(messages)
        if not image_msg:
            logger.warning("attach_poll_image: no image found near poll poll_id=%s", poll_id)
            return None

        media_id = (image_msg.get("image") or {}).get("id")
        image_msg_id = str(image_msg.get("id") or poll_id)
        if not media_id:
            logger.warning("attach_poll_image: image message has no media_id")
            return None

    logger.info("attach_poll_image: downloading media_id=%s", media_id)
    try:
        img_bytes = whapi.download_media(media_id)
    except Exception as exc:
        logger.error("attach_poll_image: download_media failed: %s", exc)
        return None

    drive = GoogleDriveClient()
    try:
        folder_id = drive.create_folder(poll_id)
    except Exception as exc:
        logger.error("attach_poll_image: create_folder failed: %s", exc)
        return None

    filename = f"{image_msg_id}.jpg"
    try:
        file_id = drive.upload_file(filename, img_bytes, folder_id)
    except Exception as exc:
        logger.error("attach_poll_image: upload_file failed: %s", exc)
        return None

    _update_enquete_drive_ids(poll_id, file_id, folder_id, image_msg_id)

    logger.info(
        "attach_poll_image: success poll_id=%s drive_file_id=%s", poll_id, file_id
    )
    return file_id


def _update_enquete_drive_ids(
    external_poll_id: str,
    drive_file_id: str,
    drive_folder_id: str,
    image_message_id: Optional[str] = None,
) -> None:
    """F-061: PATCH enquetes row with the Drive IDs (scoped to one poll)."""
    from app.services.supabase_service import supabase_domain_enabled
    if not supabase_domain_enabled():
        logger.warning("_update_enquete_drive_ids: domínio não habilitado, skipping update")
        return

    payload = {
        "drive_file_id": drive_file_id,
        "drive_folder_id": drive_folder_id,
    }
    if image_message_id:
        payload["image_message_id"] = image_message_id

    try:
        client = SupabaseRestClient.from_settings()
        client.update(
            "enquetes",
            payload,
            filters=[("external_poll_id", "eq", external_poll_id)],
            returning="minimal",
        )
        logger.info(
            "_update_enquete_drive_ids: updated poll_id=%s drive_file_id=%s",
            external_poll_id,
            drive_file_id,
        )
    except Exception as exc:
        logger.error("_update_enquete_drive_ids: request failed: %s", exc)
