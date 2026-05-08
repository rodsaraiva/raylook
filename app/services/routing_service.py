from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.services.domain_lookup import load_poll_chat_map as load_domain_poll_chat_map

logger = logging.getLogger("raylook.routing")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def resolve_test_phone(configured_test_phone: Optional[str] = None) -> str:
    candidate = configured_test_phone or settings.TEST_PHONE_NUMBER or settings.ESTOQUE_PHONE_NUMBER or ""
    return _normalize_phone(candidate)


def resolve_outbound_instance_name(configured_instance_name: Optional[str]) -> str:
    if settings.TEST_MODE:
        return "raylook-sandbox"
    return _normalize_text(configured_instance_name)


def resolve_poll_id(package_snapshot: Dict[str, Any]) -> Optional[str]:
    poll_id = _normalize_text(package_snapshot.get("poll_id") or package_snapshot.get("pollId"))
    if poll_id:
        return poll_id

    pkg_id = _normalize_text(package_snapshot.get("id"))
    if not pkg_id:
        return None

    # Package IDs are typically composed as: "{poll_id}_{index}".
    head, sep, tail = pkg_id.rpartition("_")
    if sep and tail.isdigit() and head:
        return head
    return None


def resolve_chat_id(package_snapshot: Dict[str, Any], chat_map_cache: Dict[str, str]) -> Optional[str]:
    chat_id = _normalize_text(package_snapshot.get("chat_id") or package_snapshot.get("chatId"))
    if chat_id:
        return chat_id

    poll_id = resolve_poll_id(package_snapshot)
    if not poll_id:
        return None

    cached = _normalize_text(chat_map_cache.get(poll_id))
    return cached or None


def resolve_target_phone(
    chat_id: Optional[str],
    vote_phone: str,
    test_phone: str,
    test_group_chat_id: str,
) -> Tuple[str, str]:
    normalized_chat_id = _normalize_text(chat_id)
    normalized_test_group = _normalize_text(test_group_chat_id)
    member_phone = _normalize_phone(vote_phone)
    safe_phone = resolve_test_phone() if settings.TEST_MODE else resolve_test_phone(test_phone)

    if settings.TEST_MODE and safe_phone:
        return safe_phone, "forced_test_mode"

    if normalized_chat_id == normalized_test_group and member_phone:
        return member_phone, "member"

    if safe_phone:
        return safe_phone, "test"

    return "", "test_missing"


def load_poll_chat_map() -> Dict[str, str]:
    return load_domain_poll_chat_map()


def backfill_metrics_routing(data: Dict[str, Any], chat_map_cache: Dict[str, str]) -> Dict[str, int]:
    packages = data.setdefault("votos", {}).setdefault("packages", {})
    sections = ("open", "closed_today", "closed_week", "confirmed_today")
    counters = {"updated": 0, "unchanged": 0, "failed": 0}

    for section in sections:
        items = packages.get(section, []) or []
        if not isinstance(items, list):
            continue

        for package_snapshot in items:
            if not isinstance(package_snapshot, dict):
                counters["failed"] += 1
                continue

            changed = False

            poll_id = resolve_poll_id(package_snapshot)
            if poll_id and _normalize_text(package_snapshot.get("poll_id")) != poll_id:
                package_snapshot["poll_id"] = poll_id
                changed = True

            chat_id = resolve_chat_id(package_snapshot, chat_map_cache)
            if chat_id and _normalize_text(package_snapshot.get("chat_id")) != chat_id:
                package_snapshot["chat_id"] = chat_id
                changed = True

            # Keep compatibility for readers that still inspect camelCase.
            if chat_id and _normalize_text(package_snapshot.get("chatId")) != chat_id:
                package_snapshot["chatId"] = chat_id
                changed = True

            if not chat_id:
                counters["failed"] += 1
            elif changed:
                counters["updated"] += 1
            else:
                counters["unchanged"] += 1

    return counters
