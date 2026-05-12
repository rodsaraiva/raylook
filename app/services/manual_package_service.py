"""Montagem de pacotes criados manualmente, com suporte a Supabase no staging."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger("raylook.manual_package")

from app.config import settings
from app.services.customer_service import load_customers
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
from finance.utils import extract_price
from images.thumbs import drive_export_view_url, ensure_thumbnail_for_image_url
from metrics import clients, processors


def _clean_phone(phone: Any) -> str:
    if not phone:
        return ""
    return "".join(filter(str.isdigit, str(phone)))


def fetch_enquete_row(poll_id: str) -> Optional[Dict[str, Any]]:
    if supabase_domain_enabled():
        client = SupabaseRestClient.from_settings()
        row = client.select(
            "enquetes",
            columns=(
                "id,external_poll_id,titulo,created_at_provider,drive_file_id,"
                "produto:produto_id(id,nome,valor_unitario,drive_file_id)"
            ),
            filters=[("external_poll_id", "eq", poll_id)],
            single=True,
        )
        return row if isinstance(row, dict) else None

    table_enquetes = os.getenv("BASEROW_TABLE_ENQUETES", settings.BASEROW_TABLE_ENQUETES)
    for filt in (
        {"filter__pollId__equal": poll_id},
        {"filter__field_169__equal": poll_id},
    ):
        try:
            found = clients.fetch_rows_filtered(table_enquetes, filt, size=5)
            if found:
                return found[0]
        except Exception:
            continue
    return None


def _resolve_poll_votes_and_media(
    poll_id: str, vote_lines: List[Any]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int, Optional[str], Optional[str]]:
    row = fetch_enquete_row(poll_id)
    if not row:
        raise ValueError("Enquete não encontrada.")

    produto = row.get("produto") or {}
    title = row.get("titulo", row.get("title", row.get("field_173", "")))
    valor_col = produto.get("valor_unitario", row.get("valor"))
    # F-061: imagem da enquete vem primeiro; produto é fallback pro legado
    # (e pro caminho Baserow que ainda pode estar presente em dados antigos).
    drive_id = (
        row.get("drive_file_id")
        or produto.get("drive_file_id", row.get("driveFileId", row.get("field_200", row.get("driveFileid"))))
    )
    image_url = drive_export_view_url(str(drive_id).strip()) if drive_id else None
    image_thumb: Optional[str] = None
    if image_url:
        image_thumb = ensure_thumbnail_for_image_url(image_url)

    raw_customers = load_customers()
    customers_norm = {_clean_phone(k): v for k, v in raw_customers.items() if _clean_phone(k)}

    votes_out: List[Dict[str, Any]] = []
    total_qty = 0
    for v in vote_lines:
        qty = int(getattr(v, "qty", v.get("qty") if isinstance(v, dict) else 0))
        phone_raw = getattr(v, "phone", None) if not isinstance(v, dict) else v.get("phone")
        phone = _clean_phone(phone_raw)
        total_qty += qty
        name = customers_norm.get(phone, "")
        votes_out.append({"name": name, "phone": phone, "qty": qty})

    meta = {
        "poll_title": title,
        "valor_col": valor_col,
        "image": image_url,
        "image_thumb": image_thumb,
        "opened_ts": processors.parse_timestamp(row.get("createdAtTs", row.get("field_171"))),
        "opened_ts_supabase": processors.parse_timestamp(row.get("created_at_provider")),
        "poll_row": row,
    }
    return meta, votes_out, total_qty, image_url, image_thumb


def build_preview_payload(poll_id: str, vote_lines: List[Any]) -> Dict[str, Any]:
    """Dados para o modal de preview (sem persistir)."""
    meta, votes_out, total_qty, _, _ = _resolve_poll_votes_and_media(poll_id, vote_lines)
    return {
        "poll_title": meta["poll_title"],
        "valor_col": meta["valor_col"],
        "total_qty": total_qty,
        "image": meta["image"],
        "image_thumb": meta["image_thumb"],
        "votes": [
            {"phone": v["phone"], "name": v.get("name") or "", "qty": v["qty"]} for v in votes_out
        ],
    }


def build_manual_confirmed_package(poll_id: str, vote_lines: List[Any]) -> Dict[str, Any]:
    """Constrói o dict de pacote no formato esperado por finance / PDF / confirmed_packages."""
    meta, votes_out, total_qty, image_url, image_thumb = _resolve_poll_votes_and_media(
        poll_id, vote_lines
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    pkg_id = f"{poll_id}_m_{uuid4().hex[:12]}"
    opened_ts = meta.get("opened_ts")

    return {
        "id": pkg_id,
        "poll_title": meta["poll_title"],
        "valor_col": meta["valor_col"],
        "image": image_url,
        "image_thumb": image_thumb,
        "qty": total_qty,
        "status": "confirmed",
        "votes": votes_out,
        "confirmed_at": now_iso,
        "manual_creation": True,
        "opened_at": opened_ts.isoformat() if opened_ts else None,
        "closed_at": now_iso,
        "pdf_attempts": 0,
        "pdf_status": "queued",
        "pdf_file_name": None,
    }


def create_manual_package_in_supabase(poll_id: str, vote_lines: List[Any]) -> Dict[str, Any]:
    if not supabase_domain_enabled():
        raise RuntimeError("Supabase domain disabled")

    meta, votes_out, total_qty, _, _ = _resolve_poll_votes_and_media(poll_id, vote_lines)
    poll_row = meta.get("poll_row") or {}
    produto = poll_row.get("produto") or {}
    enquete_id = poll_row.get("id")
    produto_id = produto.get("id")
    if not enquete_id or not produto_id:
        raise ValueError("Enquete sem produto associado no Supabase.")

    client = SupabaseRestClient.from_settings()
    opened_at = meta.get("opened_ts_supabase") or meta.get("opened_ts") or datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)

    try:
        next_sequence = int(client.rpc("next_pacote_sequence", {"p_enquete_id": enquete_id}))
    except Exception:
        existing = client.select(
            "pacotes",
            columns="sequence_no",
            filters=[("enquete_id", "eq", enquete_id)],
            order="sequence_no.desc",
            limit=1,
        )
        if isinstance(existing, list) and existing:
            next_sequence = max(int(existing[0].get("sequence_no") or 0) + 1, 1)
        else:
            next_sequence = 1

    pacote = client.insert(
        "pacotes",
        {
            "enquete_id": enquete_id,
            "sequence_no": next_sequence,
            "capacidade_total": 24,
            "total_qty": total_qty,
            "participants_count": len(votes_out),
            "status": "closed",
            "opened_at": opened_at.isoformat(),
            "closed_at": now.isoformat(),
        },
    )[0]

    customer_names = {_clean_phone(phone): name for phone, name in load_customers().items() if _clean_phone(phone)}
    unit_price = float(produto.get("valor_unitario") or extract_price(str(meta.get("poll_title") or "")) or 0.0)
    commission_per_piece = float(settings.COMMISSION_PER_PIECE)

    # Cache das alternativas da enquete (usado quando precisamos criar voto sintético)
    alternativas_by_qty: Dict[int, str] = {}
    try:
        alts_rows = client.select(
            "enquete_alternativas",
            columns="id,qty",
            filters=[("enquete_id", "eq", enquete_id)],
        )
        if isinstance(alts_rows, list):
            for alt in alts_rows:
                alt_qty = int(alt.get("qty") or 0)
                if alt_qty and alt.get("id"):
                    alternativas_by_qty[alt_qty] = str(alt["id"])
    except Exception:
        logger.warning("manual_package: falha lendo enquete_alternativas, voto sintético sem alternativa_id")

    for vote in votes_out:
        phone = _clean_phone(vote.get("phone"))
        if not phone:
            continue
        customer = client.upsert_one(
            "clientes",
            {"celular": phone, "nome": vote.get("name") or customer_names.get(phone) or phone},
            on_conflict="celular",
        )
        qty = int(vote.get("qty") or 0)
        # Look up existing vote (don't modify it — manual package is administrative)
        existing_voto = client.select(
            "votos", columns="id",
            filters=[("enquete_id", "eq", enquete_id), ("cliente_id", "eq", customer["id"])],
            limit=1,
        )
        voto_id: Optional[str] = (
            existing_voto[0]["id"]
            if isinstance(existing_voto, list) and existing_voto
            else None
        )
        # Se o cliente não tinha voto nessa enquete (caso comum em pacote
        # manual), criar um voto sintético para satisfazer a FK NOT NULL de
        # pacote_clientes.voto_id. Voto sintético fica com status='in' e
        # voted_at=now(), como se o operador tivesse registrado o voto em
        # nome do cliente.
        if not voto_id:
            synthetic_voto = {
                "enquete_id": enquete_id,
                "cliente_id": customer["id"],
                "alternativa_id": alternativas_by_qty.get(qty),
                "qty": qty,
                "status": "in",
                "voted_at": now.isoformat(),
            }
            created = client.upsert_one(
                "votos",
                synthetic_voto,
                on_conflict="enquete_id,cliente_id",
            )
            voto_id = str(created["id"])
            logger.info(
                "manual_package: criado voto sintético cliente=%s enquete=%s qty=%s id=%s",
                customer["id"],
                enquete_id,
                qty,
                voto_id,
            )
        subtotal = round(unit_price * qty, 2)
        commission_amount = round(qty * commission_per_piece, 2)
        total_amount = round(subtotal + commission_amount, 2)
        client.insert(
            "pacote_clientes",
            {
                "pacote_id": pacote["id"],
                "cliente_id": customer["id"],
                "voto_id": voto_id,
                "produto_id": produto_id,
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "commission_percent": 0,
                "commission_amount": commission_amount,
                "total_amount": total_amount,
                "status": "closed",
            },
            upsert=True,
            on_conflict="pacote_id,cliente_id",
            returning="minimal",
        )

    return {
        "package_id": str(pacote["id"]),
        "legacy_package_id": f"{poll_id}_{max(next_sequence - 1, 0)}",
    }
