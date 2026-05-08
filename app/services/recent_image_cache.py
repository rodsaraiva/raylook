from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import os

from app.services.runtime_state_service import (
    RECENT_IMAGES_STATE_KEY,
    load_runtime_state,
    runtime_state_enabled,
    save_runtime_state,
)
from app.storage import JsonFileStorage

_CACHE_PATH = Path(os.getenv("DATA_DIR", "data")) / "recent_images.json"
_storage = JsonFileStorage(_CACHE_PATH)
_MAX_ITEMS_PER_CHAT = 50
_MAX_AGE_SECONDS = 6 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return int(ts / 1000) if ts > 10_000_000_000 else ts
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        ts = int(raw)
        return int(ts / 1000) if ts > 10_000_000_000 else ts
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _load_cache() -> Dict[str, Any]:
    if runtime_state_enabled():
        return load_runtime_state(RECENT_IMAGES_STATE_KEY) or {}
    return _storage.load() or {}


def _save_cache(data: Dict[str, Any]) -> None:
    if runtime_state_enabled():
        save_runtime_state(RECENT_IMAGES_STATE_KEY, data)
        return
    _storage.save(data)


def remember_recent_image(
    *,
    chat_id: str,
    message_id: str,
    media_id: str,
    occurred_at: Any,
) -> None:
    if not chat_id or not media_id:
        return

    poll_ts = _parse_timestamp(occurred_at)
    if poll_ts is None:
        poll_ts = int(_utc_now().timestamp())

    data = _load_cache()
    entries = list(data.get(chat_id) or [])
    entries = [item for item in entries if item.get("media_id") != media_id]
    entries.insert(
        0,
        {
            "message_id": message_id,
            "media_id": media_id,
            "timestamp": poll_ts,
        },
    )
    data[chat_id] = entries[:_MAX_ITEMS_PER_CHAT]
    _save_cache(data)


def find_recent_image(*, chat_id: str, poll_ts: int) -> Optional[Dict[str, Any]]:
    if not chat_id:
        return None
    data = _load_cache()
    entries = list(data.get(chat_id) or [])
    if not entries:
        return None

    now_ts = int(_utc_now().timestamp())
    candidates = []
    for item in entries:
        ts = _parse_timestamp(item.get("timestamp"))
        if ts is None:
            continue
        if now_ts - ts > _MAX_AGE_SECONDS:
            continue
        if poll_ts and ts > poll_ts:
            continue
        candidates.append({**item, "timestamp": ts})

    if not candidates:
        return None
    candidates.sort(key=lambda item: item["timestamp"], reverse=True)
    return candidates[0]
