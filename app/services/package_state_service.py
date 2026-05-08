"""
Package state service — PostgreSQL-backed (via Supabase REST).

Replaces the former JSON-file-based storage.  All state now lives in the
``pacotes`` and ``pagamentos`` tables.
"""

import json
import logging
from typing import Any, Dict, Optional
from uuid import UUID

logger = logging.getLogger("raylook.package_state_service")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_package_uuid(pkg_id: str) -> Optional[str]:
    """Return the Supabase UUID for *pkg_id*.

    *pkg_id* may already be a UUID string **or** a legacy identifier of the
    form ``<poll_id>_<sequence_no>``.  Returns ``None`` when resolution fails.
    """
    # Fast path: already a UUID
    try:
        UUID(pkg_id)
        return pkg_id
    except ValueError:
        pass

    # Legacy format: poll_id_seqno
    if "_" not in pkg_id:
        return None
    poll_id, seq_str = pkg_id.rsplit("_", 1)
    try:
        seq = int(seq_str) + 1  # legacy IDs are 0-indexed, DB sequence_no is 1-indexed
    except ValueError:
        return None

    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()

    resp = sb._request(
        "GET",
        f"/rest/v1/enquetes?external_poll_id=eq.{poll_id}&select=id&limit=1",
    )
    enquetes = resp.json() if resp.status_code == 200 else []
    if not enquetes:
        return None

    eid = enquetes[0]["id"]

    resp = sb._request(
        "GET",
        f"/rest/v1/pacotes?enquete_id=eq.{eid}&sequence_no=eq.{seq}&select=id&limit=1",
    )
    pacotes = resp.json() if resp.status_code == 200 else []
    return pacotes[0]["id"] if pacotes else None


# Columns on the ``pacotes`` table that we allow callers to write via
# ``update_package_state``.
_PACOTES_COLUMN_MAP = {
    "pdf_status": "pdf_status",
    "pdf_file_name": "pdf_file_name",
    "pdf_sent_at": "pdf_sent_at",
    "pdf_attempts": "pdf_attempts",
    "tag": "tag",
    "custom_title": "custom_title",
}

# ---------------------------------------------------------------------------
# Public API — same signatures as the former JSON-backed implementation
# ---------------------------------------------------------------------------


def load_package_states() -> Dict[str, Any]:
    """Return an empty dict.

    Bulk-loading all package states is no longer necessary — the data now
    lives in PostgreSQL and callers should query specific packages as needed.
    Kept as a no-op for backwards compatibility.
    """
    return {}


def save_package_states(states: Dict[str, Any]) -> None:  # noqa: ARG001
    """No-op — retained only for backwards compatibility.

    All writes now go directly to PostgreSQL via the other helpers.
    """
    logger.debug("save_package_states() called — no-op (data is in PostgreSQL)")


def update_package_state(pkg_id: str, update_data: Dict[str, Any]) -> None:
    """Persist package-level fields to the ``pacotes`` table.

    *pkg_id* can be a UUID or a legacy ``poll_id_seqno`` identifier.
    Only keys present in ``_PACOTES_COLUMN_MAP`` are written; everything
    else is silently ignored.
    """
    uuid = _resolve_package_uuid(pkg_id)
    if uuid is None:
        logger.warning(
            "update_package_state: could not resolve pkg_id=%s to a UUID — skipping",
            pkg_id,
        )
        return

    payload: Dict[str, Any] = {}
    for src_key, col in _PACOTES_COLUMN_MAP.items():
        if src_key in update_data:
            payload[col] = update_data[src_key]

    if not payload:
        logger.debug(
            "update_package_state: no mappable fields in update_data for pkg_id=%s",
            pkg_id,
        )
        return

    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()
    resp = sb._request(
        "PATCH",
        f"/rest/v1/pacotes?id=eq.{uuid}",
        payload=payload,
        prefer="return=minimal",
    )

    if resp.status_code not in (200, 204):
        logger.error(
            "update_package_state: PATCH pacotes failed for %s — %s %s",
            pkg_id,
            resp.status_code,
            resp.text,
        )
    else:
        logger.debug("update_package_state: updated pacotes %s with %s", uuid, payload)


def update_vote_state(pkg_id: str, vote_idx: int, vote_update: Dict[str, Any]) -> None:
    """Persist vote-level payment metadata.

    Looks up the ``pacote_clientes`` row by package UUID + position, then
    upserts into ``pagamentos.payload_json``.  If specific well-known keys
    are present (e.g. ``mercadopago_payment_id``, ``asaas_payment_id``), they
    are also written to ``provider_payment_id``.
    """
    uuid = _resolve_package_uuid(pkg_id)
    if uuid is None:
        logger.warning(
            "update_vote_state: could not resolve pkg_id=%s — skipping",
            pkg_id,
        )
        return

    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()

    # Fetch the pacote_clientes row at the given position.
    resp = sb._request(
        "GET",
        f"/rest/v1/pacote_clientes?pacote_id=eq.{uuid}&select=id&order=created_at.asc&offset={vote_idx}&limit=1",
    )
    rows = resp.json() if resp.status_code == 200 else []
    if not rows:
        logger.warning(
            "update_vote_state: no pacote_clientes row at idx=%d for pkg=%s",
            vote_idx,
            pkg_id,
        )
        return

    pc_id = rows[0]["id"]

    # Build the pagamentos payload.
    pag_payload: Dict[str, Any] = {}

    # Map well-known payment-id keys to provider_payment_id.
    for key in ("mercadopago_payment_id", "asaas_payment_id"):
        if key in vote_update:
            pag_payload["provider_payment_id"] = vote_update[key]
            # Also set provider hint so we know the source.
            if "mercadopago" in key:
                pag_payload.setdefault("provider", "mercadopago")
            elif "asaas" in key:
                pag_payload.setdefault("provider", "asaas")

    # Store the full vote_update blob in payload_json for anything extra.
    pag_payload["payload_json"] = json.dumps(vote_update, default=str)

    if not pag_payload:
        return

    # Find the venda row linked to this pacote_clientes entry.
    venda_resp = sb._request(
        "GET",
        f"/rest/v1/vendas?pacote_cliente_id=eq.{pc_id}&select=id&limit=1",
    )
    venda_rows = venda_resp.json() if venda_resp.status_code == 200 else []
    if not venda_rows:
        logger.warning(
            "update_vote_state: no venda row for pacote_cliente_id=%s — skipping pagamentos write",
            pc_id,
        )
        return

    venda_id = venda_rows[0]["id"]

    # Try to find an existing pagamentos row via venda_id.
    resp = sb._request(
        "GET",
        f"/rest/v1/pagamentos?venda_id=eq.{venda_id}&select=id&limit=1",
    )
    existing = resp.json() if resp.status_code == 200 else []

    if existing:
        pag_id = existing[0]["id"]
        resp = sb._request(
            "PATCH",
            f"/rest/v1/pagamentos?id=eq.{pag_id}",
            payload=pag_payload,
            prefer="return=minimal",
        )
    else:
        pag_payload["venda_id"] = venda_id
        resp = sb._request(
            "POST",
            "/rest/v1/pagamentos",
            payload=pag_payload,
            prefer="return=minimal",
        )

    if resp.status_code not in (200, 201, 204):
        logger.error(
            "update_vote_state: write pagamentos failed for pc_id=%s — %s %s",
            pc_id,
            resp.status_code,
            resp.text,
        )
    else:
        logger.debug(
            "update_vote_state: wrote pagamentos for pc_id=%s (pkg=%s idx=%d)",
            pc_id,
            pkg_id,
            vote_idx,
        )


def merge_states_into_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through — no longer merges from a JSON sidecar.

    The metrics pipeline (``fetch_package_lists_for_metrics``) now reads
    ``pdf_status``, ``tag``, etc. directly from the ``pacotes`` table, so
    there is nothing left to merge here.
    """
    return metrics
