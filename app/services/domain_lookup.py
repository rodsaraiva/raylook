from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.domain_lookup")


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _looks_uuid(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except Exception:
        return False


def _supabase_client() -> Optional[SupabaseRestClient]:
    if not supabase_domain_enabled():
        return None
    try:
        return SupabaseRestClient.from_settings()
    except Exception as exc:
        logger.warning("Failed to initialize Supabase lookup client: %s", exc)
        return None


def _select_one(client: SupabaseRestClient, table: str, *, columns: str, filters: list[tuple[str, str, Any]]) -> Optional[Dict[str, Any]]:
    rows = client.select(table, columns=columns, filters=filters, limit=1)
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def parse_legacy_package_id(package_id: str) -> Tuple[Optional[str], Optional[int]]:
    raw = _normalize_text(package_id)
    if not raw:
        return None, None
    head, sep, tail = raw.rpartition("_")
    if not sep or not head or not tail.isdigit():
        return None, None
    return head, int(tail) + 1


def resolve_supabase_package_id(package_id: str) -> Optional[str]:
    raw = _normalize_text(package_id)
    if not raw:
        return None
    if _looks_uuid(raw):
        return raw

    poll_id, sequence_no = parse_legacy_package_id(raw)
    if not poll_id or sequence_no is None:
        return None

    sb = _supabase_client()
    if not sb:
        return None
    try:
        poll = _select_one(
            sb,
            "enquetes",
            columns="id",
            filters=[("external_poll_id", "eq", poll_id)],
        )
        if not isinstance(poll, dict):
            return None
        pacote = _select_one(
            sb,
            "pacotes",
            columns="id",
            filters=[("enquete_id", "eq", poll["id"]), ("sequence_no", "eq", sequence_no)],
        )
    except Exception as exc:
        logger.warning("Failed to resolve Supabase package id from legacy key=%s: %s", raw, exc)
        return None

    if not isinstance(pacote, dict):
        return None
    resolved = _normalize_text(pacote.get("id"))
    return resolved or None


def get_poll_title_by_poll_id(poll_id: str) -> Optional[str]:
    sb = _supabase_client()
    if not sb:
        return None
    try:
        row = _select_one(
            sb,
            "enquetes",
            columns="titulo",
            filters=[("external_poll_id", "eq", poll_id)],
        )
        title = row.get("titulo") if isinstance(row, dict) else None
        return str(title).strip() if title else None
    except Exception as exc:
        logger.warning("Failed to fetch poll title from Supabase pollId=%s: %s", poll_id, exc)
        return None


def get_poll_chat_id_by_poll_id(poll_id: str) -> Optional[str]:
    sb = _supabase_client()
    if not sb:
        return None
    try:
        row = _select_one(
            sb,
            "enquetes",
            columns="chat_id",
            filters=[("external_poll_id", "eq", poll_id)],
        )
        chat_id = row.get("chat_id") if isinstance(row, dict) else None
        return str(chat_id).strip() if chat_id else None
    except Exception as exc:
        logger.warning("Failed to fetch poll chat_id from Supabase pollId=%s: %s", poll_id, exc)
        return None


def get_latest_vote_qty(poll_id: str, voter_phone: str) -> Optional[int]:
    phone = normalize_phone(voter_phone)
    if not poll_id or not phone:
        return None

    sb = _supabase_client()
    if not sb:
        return None
    try:
        poll = _select_one(
            sb,
            "enquetes",
            columns="id",
            filters=[("external_poll_id", "eq", poll_id)],
        )
        client_row = _select_one(
            sb,
            "clientes",
            columns="id",
            filters=[("celular", "eq", phone)],
        )
        if poll and client_row:
            vote = _select_one(
                sb,
                "votos",
                columns="qty",
                filters=[
                    ("enquete_id", "eq", poll["id"]),
                    ("cliente_id", "eq", client_row["id"]),
                ],
            )
            if isinstance(vote, dict):
                return int(float(vote.get("qty") or 0))
    except Exception as exc:
        logger.warning("Failed to fetch vote qty from Supabase pollId=%s phone=%s: %s", poll_id, phone, exc)
    return None


def load_poll_chat_map() -> Dict[str, str]:
    sb = _supabase_client()
    if not sb:
        return {}
    try:
        rows = sb.select_all("enquetes", columns="external_poll_id,chat_id", order="created_at.asc")
        mapping: Dict[str, str] = {}
        for row in rows:
            poll_id = str(row.get("external_poll_id") or "").strip()
            chat_id = str(row.get("chat_id") or "").strip()
            if poll_id and chat_id:
                mapping[poll_id] = chat_id
        return mapping
    except Exception as exc:
        logger.warning("Failed to load poll chat map from Supabase: %s", exc)
        return {}
