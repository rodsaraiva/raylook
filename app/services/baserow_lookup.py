from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.config import settings
from metrics import clients

logger = logging.getLogger("raylook.baserow_lookup")

# Field IDs (see fields.txt) for the Alana Baserow database
_FIELD_VOTOS_POLL_ID = 158
_FIELD_VOTOS_VOTER_PHONE = 160
_FIELD_ENQUETES_POLL_ID = 169


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def poll_id_from_package_snapshot(package_snapshot: dict) -> Optional[str]:
    poll_id = package_snapshot.get("poll_id") or package_snapshot.get("pollId")
    if isinstance(poll_id, str) and poll_id.strip():
        return poll_id.strip()

    pkg_id = package_snapshot.get("id")
    if not isinstance(pkg_id, str) or not pkg_id:
        return None

    # Most packages are built as: f"{poll_id}_{i}"
    head, sep, tail = pkg_id.rpartition("_")
    if sep and tail.isdigit() and head:
        return head
    return None


def get_poll_data_by_poll_id(poll_id: str) -> Optional[dict[str, Any]]:
    try:
        rows = clients.fetch_rows_filtered(
            settings.BASEROW_TABLE_ENQUETES,
            {f"filter__field_{_FIELD_ENQUETES_POLL_ID}__equal": poll_id},
            size=1,
        )
        if not rows:
            return None
        row = rows[0]
        title = row.get("title") or row.get("field_173")
        valor = row.get("valor")
        return {
            "title": str(title).strip() if title else None,
            "valor": str(valor).strip() if valor else None
        }
    except Exception as exc:
        logger.warning("Failed to fetch poll data from Baserow pollId=%s: %s", poll_id, exc)
        return None


def get_poll_title_by_poll_id(poll_id: str) -> Optional[str]:
    data = get_poll_data_by_poll_id(poll_id)
    return data["title"] if data else None


def get_latest_vote_row(poll_id: str, voter_phone: str) -> Optional[dict[str, Any]]:
    phone = normalize_phone(voter_phone)
    if not poll_id or not phone:
        return None
    try:
        rows = clients.fetch_rows_filtered(
            settings.BASEROW_TABLE_VOTOS,
            {
                f"filter__field_{_FIELD_VOTOS_POLL_ID}__equal": poll_id,
                f"filter__field_{_FIELD_VOTOS_VOTER_PHONE}__equal": phone,
            },
            size=200,
        )
        if not rows:
            return None
        rows.sort(key=lambda r: r.get("id", 0))
        return rows[-1]
    except Exception as exc:
        logger.warning("Failed to fetch vote row from Baserow pollId=%s phone=%s: %s", poll_id, phone, exc)
        return None


def get_latest_vote_qty(poll_id: str, voter_phone: str) -> Optional[int]:
    row = get_latest_vote_row(poll_id, voter_phone)
    if not row:
        return None
    raw = row.get("qty") or row.get("field_164")
    try:
        return int(float(raw or 0))
    except Exception:
        return None

