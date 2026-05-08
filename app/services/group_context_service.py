from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.runtime_state_service import load_runtime_state, runtime_state_enabled, save_runtime_state
from metrics import processors


TEST_GROUP_MONITOR_STATE_KEY = "test_group_monitor"
_monitor_started_at_cache: Dict[str, str] = {}


def normalize_chat_id(value: Any) -> str:
    return str(value or "").strip()


def official_group_chat_id() -> str:
    return normalize_chat_id(getattr(settings, "OFFICIAL_GROUP_CHAT_ID", ""))


def test_group_chat_id() -> str:
    return normalize_chat_id(getattr(settings, "TEST_GROUP_CHAT_ID", ""))


def monitored_chat_ids() -> List[str]:
    ordered = [
        official_group_chat_id(),
        test_group_chat_id(),
    ]
    result: List[str] = []
    seen = set()
    for chat_id in ordered:
        if chat_id and chat_id not in seen:
            result.append(chat_id)
            seen.add(chat_id)
    return result


def monitored_chat_id() -> str:
    if bool(getattr(settings, "TEST_MODE", False)):
        test_chat_id = test_group_chat_id()
        if test_chat_id:
            return test_chat_id
    return official_group_chat_id()


def resolve_group_kind(chat_id: Any) -> str:
    normalized = normalize_chat_id(chat_id)
    if not normalized:
        return "unknown"
    if normalized == test_group_chat_id():
        return "test"
    if normalized == official_group_chat_id():
        return "official"
    return "authorized"


def resolve_group_label(chat_id: Any) -> str:
    kind = resolve_group_kind(chat_id)
    if kind == "test":
        return "Grupo de teste"
    if kind == "official":
        return "Grupo oficial"
    if kind == "authorized":
        return "Grupo autorizado"
    return "Grupo não identificado"


def annotate_group(payload: Dict[str, Any], chat_id: Any) -> Dict[str, Any]:
    normalized = normalize_chat_id(chat_id)
    payload["chat_id"] = normalized or None
    payload["group_kind"] = resolve_group_kind(normalized)
    payload["group_label"] = resolve_group_label(normalized)
    payload["is_test_group"] = payload["group_kind"] == "test"
    payload["is_official_group"] = payload["group_kind"] == "official"
    return payload


def is_test_group_monitoring_enabled() -> bool:
    return bool(getattr(settings, "TEST_MODE", False) and test_group_chat_id())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_test_group_monitor_started_at() -> Optional[str]:
    if not is_test_group_monitoring_enabled():
        return None

    metrics_floor = str(getattr(settings, "METRICS_MIN_DATE", "") or "").strip()
    if metrics_floor and processors.parse_timestamp(metrics_floor) is not None:
        return None

    if not runtime_state_enabled():
        return None

    current_chat_id = test_group_chat_id()
    if current_chat_id in _monitor_started_at_cache:
        return _monitor_started_at_cache[current_chat_id]

    payload = load_runtime_state(TEST_GROUP_MONITOR_STATE_KEY) or {}
    stored_chat_id = normalize_chat_id(payload.get("chat_id"))
    started_at = str(payload.get("started_at") or "").strip()

    if stored_chat_id == current_chat_id and started_at:
        _monitor_started_at_cache[current_chat_id] = started_at
        return started_at

    started_at = _now_iso()
    save_runtime_state(
        TEST_GROUP_MONITOR_STATE_KEY,
        {
            "chat_id": current_chat_id,
            "started_at": started_at,
        },
    )
    _monitor_started_at_cache[current_chat_id] = started_at
    return started_at
