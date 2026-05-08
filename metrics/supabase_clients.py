"""
Supabase REST client for metrics reads.

Used by metrics/services.py when METRICS_SOURCE=supabase.
Returns lists of dicts compatible with the existing processors pipeline
(same key names as Baserow rows: pollId, voterPhone, qty, etc.).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.services.group_context_service import (
    annotate_group,
    get_test_group_monitor_started_at,
    is_test_group_monitoring_enabled,
    monitored_chat_ids,
    normalize_chat_id,
    test_group_chat_id,
)
from app.services.supabase_service import SupabaseRestClient
from metrics import processors

logger = logging.getLogger("raylook.metrics.supabase_clients")

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _headers() -> Dict[str, str]:
    """Build standard Supabase REST headers."""
    api_key = settings.SUPABASE_ANON_KEY or settings.SUPABASE_SERVICE_ROLE_KEY
    if not api_key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY must be configured."
        )
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _base_url() -> str:
    url = settings.SUPABASE_URL
    if not url:
        raise RuntimeError("SUPABASE_URL not configured.")
    return url.rstrip("/")


def _monitored_chat_ids() -> List[str]:
    return monitored_chat_ids()


def _test_group_monitor_floor():
    started_at = get_test_group_monitor_started_at()
    if not started_at:
        return None
    return processors.parse_timestamp(started_at)


def _include_for_test_group_monitoring(*timestamps: Any) -> bool:
    floor = _test_group_monitor_floor()
    if floor is None:
        return True
    for value in timestamps:
        dt = processors.parse_timestamp(value)
        if dt and dt >= floor:
            return True
    return False


def _chat_id_allowed(chat_id: Any) -> bool:
    allowed = set(_monitored_chat_ids())
    normalized = normalize_chat_id(chat_id)
    if not allowed:
        return True
    return normalized in allowed


def _include_for_monitored_group(chat_id: Any, *timestamps: Any) -> bool:
    normalized = normalize_chat_id(chat_id)
    if normalized != test_group_chat_id() or not is_test_group_monitoring_enabled():
        return True
    return _include_for_test_group_monitoring(*timestamps)


def _normalize_rest_path(path: str) -> str:
    """Strip /rest/v1 prefix when SUPABASE_REST_PATH is empty (direct PostgREST)."""
    rest_path = str(getattr(settings, "SUPABASE_REST_PATH", "/rest/v1") or "").strip()
    if path.startswith("/rest/v1") and not rest_path:
        return path[len("/rest/v1"):] or "/"
    return path


def _get(path: str, params: Dict[str, Any], max_retries: int = 3) -> List[Dict[str, Any]]:
    """Paginate a Supabase REST endpoint (Range header pagination).

    Usa `SupabaseRestClient.from_settings()._request(...)` internamente pra que
    o backend selecionado (Postgres real em prod, SQLite em dev) seja respeitado.
    """
    sb = SupabaseRestClient.from_settings()
    page_size = 1000
    offset = 0
    results: List[Dict[str, Any]] = []
    while True:
        range_header = f"{offset}-{offset + page_size - 1}"
        try:
            resp = sb._request(
                "GET",
                path,
                params=params,
                extra_headers={"Range": range_header},
            )
        except Exception as exc:
            logger.warning("_get %s: %s", path, exc)
            break
        batch = resp.json() if resp.text else []
        if not isinstance(batch, list):
            batch = [batch] if batch else []
        results.extend(batch)
        if len(batch) < page_size:
            return results
        offset += page_size
    return results


def _get_LEGACY_HTTPX_UNUSED(path: str, params: Dict[str, Any], max_retries: int = 3) -> List[Dict[str, Any]]:
    """Versão antiga com httpx direto (mantida só como referência; não é chamada)."""
    base = _base_url()
    url = f"{base}{_normalize_rest_path(path)}"
    headers = _headers()
    timeout = httpx.Timeout(15.0, connect=5.0)
    page_size = 1000
    offset = 0
    results: List[Dict[str, Any]] = []

    while True:
        range_header = f"{offset}-{offset + page_size - 1}"
        req_headers = {**headers, "Range": range_header}
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt < max_retries:
            attempt += 1
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(url, headers=req_headers, params=params, follow_redirects=True)
                if resp.status_code in (200, 206):
                    batch = resp.json()
                    if not isinstance(batch, list):
                        batch = []
                    results.extend(batch)
                    if len(batch) < page_size:
                        return results  # last page
                    offset += page_size
                    break  # next page
                if resp.status_code == 416:
                    return results  # range out of bounds = done
                raise RuntimeError(f"Supabase REST error {resp.status_code}: {resp.text[:200]}")
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise RuntimeError(f"Network error calling Supabase: {exc}") from exc
        else:
            if last_exc:
                raise RuntimeError(f"Failed fetching {path}: {last_exc}") from last_exc

    return results


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def fetch_enquetes_for_metrics() -> List[Dict[str, Any]]:
    """
    Fetch enquetes + linked produto (for drive_file_id).

    Returns list of dicts with keys compatible with processors.py:
      pollId, title / field_173, driveFileId / field_200,
      createdAtTs / field_171, chatId
    """
    params = {
        "select": (
            "external_poll_id,titulo,status,chat_id,"
            "created_at_provider,created_at,"
            "drive_file_id,drive_folder_id,"
            "produto:produto_id(drive_file_id,drive_folder_id,nome)"
        ),
        "order": "created_at_provider.asc",
    }
    target_chat_ids = _monitored_chat_ids()
    if len(target_chat_ids) == 1:
        params["chat_id"] = f"eq.{target_chat_ids[0]}"
    rows = _get("/rest/v1/enquetes", params=params)

    result = []
    for r in rows:
        if not _chat_id_allowed(r.get("chat_id")):
            continue
        if not _include_for_monitored_group(
            r.get("chat_id"),
            r.get("created_at_provider"),
            r.get("created_at"),
        ):
            continue
        produto = r.get("produto") or {}
        # Normalize into the shape processors expect
        created_ts = r.get("created_at_provider") or r.get("created_at")
        # Convert ISO timestamp to epoch seconds (float string) for parse_timestamp
        epoch_str: Optional[str] = None
        if created_ts:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(str(created_ts).replace("Z", "+00:00"))
                epoch_str = str(dt.timestamp())
            except Exception:
                epoch_str = created_ts  # fallback: pass ISO string, parse_timestamp handles it

        # F-061: imagem da própria enquete tem prioridade sobre a do produto.
        drive_file_id = r.get("drive_file_id") or produto.get("drive_file_id")
        item = {
            # Baserow-compatible keys
            "pollId": r.get("external_poll_id"),
            "field_169": r.get("external_poll_id"),
            "title": r.get("titulo", ""),
            "field_173": r.get("titulo", ""),
            "driveFileId": drive_file_id,
            "field_200": drive_file_id,
            "chatId": r.get("chat_id"),
            "status": r.get("status", "open"),
            # timestamp for date filtering
            "createdAtTs": epoch_str,
            "field_171": epoch_str,
            # extras
            "_supabase_produto_nome": produto.get("nome"),
        }
        annotate_group(item, r.get("chat_id"))
        result.append(item)

    logger.info("fetch_enquetes_for_metrics: %d enquetes loaded from Supabase", len(result))
    return result


def fetch_votos_for_metrics() -> List[Dict[str, Any]]:
    """
    Fetch current vote state (votos table) joined with clients and alternativas.

    Returns list of dicts compatible with processors.py:
      pollId, voterPhone, voterName, qty, timestamp / field_166, rawJson
    """
    rows = _get(
        "/rest/v1/votos",
        params={
            "select": (
                "id,qty,status,voted_at,updated_at,"
                "enquete:enquete_id(external_poll_id,titulo,chat_id,created_at_provider,created_at),"
                "cliente:cliente_id(celular,nome),"
                "alternativa:alternativa_id(qty,label)"
            ),
            "status": "in.(in,out)",  # include all active votes
            "order": "voted_at.asc",
        },
    )

    result = []
    for r in rows:
        enquete = r.get("enquete") or {}
        if not _chat_id_allowed(enquete.get("chat_id")):
            continue
        if not _include_for_monitored_group(
            enquete.get("chat_id"),
            enquete.get("created_at_provider"),
            enquete.get("created_at"),
        ):
            continue
        cliente = r.get("cliente") or {}
        alternativa = r.get("alternativa") or {}

        voted_at = r.get("voted_at") or r.get("updated_at")
        epoch_str: Optional[str] = None
        if voted_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(voted_at).replace("Z", "+00:00"))
                epoch_str = str(dt.timestamp())
            except Exception:
                epoch_str = voted_at

        # qty: prefer alternativa.qty (official), fallback to votos.qty
        qty_val = alternativa.get("qty") or r.get("qty") or 0
        try:
            qty_val = int(qty_val)
        except Exception:
            qty_val = 0

        # Map status: "in" = valid vote, "out" = removed
        # processors handle qty=0 as removed
        if r.get("status") == "out":
            qty_val = 0

        item = {
            # Baserow-compatible keys
            "id": r.get("id"),
            "pollId": enquete.get("external_poll_id"),
            "field_158": enquete.get("external_poll_id"),
            "voterPhone": cliente.get("celular"),
            "field_160": cliente.get("celular"),
            "voterName": cliente.get("nome"),
            "field_161": cliente.get("nome"),
            "qty": qty_val,
            "field_164": qty_val,
            "timestamp": epoch_str,
            "field_166": epoch_str,
            "chatId": enquete.get("chat_id"),
            "field_155": enquete.get("chat_id"),
            "optionName": alternativa.get("label"),
            # Empty rawJson — titles already resolved via enquetes query
            "rawJson": None,
        }
        annotate_group(item, enquete.get("chat_id"))
        result.append(item)

    logger.info("fetch_votos_for_metrics: %d votos loaded from Supabase", len(result))
    return result


def _safe_dt(value: Any):
    return processors.parse_timestamp(value)


def _metrics_floor():
    raw = str(getattr(settings, "METRICS_MIN_DATE", "") or "").strip()
    if not raw:
        return None
    return processors.parse_timestamp(raw)


def _include_from_floor(*timestamps: Any) -> bool:
    floor = _metrics_floor()
    if floor is None:
        return True
    for value in timestamps:
        dt = _safe_dt(value)
        if dt and dt >= floor:
            return True
    return False


def _chunked(values: List[str], size: int = 200) -> List[List[str]]:
    return [values[idx: idx + size] for idx in range(0, len(values), size)]


def _legacy_package_id(poll_id: str, sequence_no: Any, fallback_id: str) -> str:
    try:
        normalized = max(int(sequence_no) - 1, 0)
    except Exception:
        return fallback_id
    return f"{poll_id}_{normalized}"


def fetch_package_lists_for_metrics() -> Dict[str, List[Dict[str, Any]]]:
    client = SupabaseRestClient.from_settings()
    rows = client.select_all(
        "pacotes",
        columns=(
            "id,status,sequence_no,total_qty,participants_count,opened_at,closed_at,approved_at,cancelled_at,updated_at,"
            "custom_title,tag,fornecedor,pdf_status,pdf_file_name,pdf_sent_at,pdf_attempts,confirmed_by,cancelled_by,"
            "enquete:enquete_id(id,external_poll_id,titulo,chat_id,created_at_provider,drive_file_id,"
            "produto:produto_id(drive_file_id))"
        ),
        order="created_at.desc",
    )

    pacote_ids = [str(row.get("id")) for row in rows if row.get("id")]
    enquete_ids = [str((row.get("enquete") or {}).get("id") or row.get("enquete_id") or "").strip() for row in rows]
    enquete_ids = [value for value in enquete_ids if value]
    package_clients: List[Dict[str, Any]] = []
    if pacote_ids:
        for batch in _chunked(pacote_ids, size=200):
            package_clients.extend(
                client.select_all(
                    "pacote_clientes",
                    # F-040: incluir unit_price, subtotal, commission_amount,
                    # total_amount para que o snapshot passado a pdf_worker e
                    # payments_worker tenha os valores financeiros corretos
                    # sem precisar recalcular do poll_title.
                    columns="id,pacote_id,voto_id,cliente_id,qty,unit_price,subtotal,commission_amount,total_amount,cliente:cliente_id(celular,nome)",
                    filters=[("pacote_id", "in", batch)],
                    order="created_at.asc",
                )
            )

    current_votes: List[Dict[str, Any]] = []
    if enquete_ids:
        for batch in _chunked(sorted(set(enquete_ids)), size=200):
            current_votes.extend(
                client.select_all(
                    "votos",
                    columns="id,enquete_id,cliente_id,qty,status,voted_at,updated_at,cliente:cliente_id(celular,nome)",
                    filters=[("enquete_id", "in", batch)],
                    order="voted_at.asc",
                )
            )

    clients_by_package: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    package_row_by_id: Dict[str, Dict[str, Any]] = {}
    package_poll_by_id: Dict[str, str] = {}
    poll_id_by_enquete_id: Dict[str, str] = {}
    assigned_qty_by_poll_customer: Dict[tuple[str, str], int] = defaultdict(int)
    assigned_vote_ids: set[str] = set()
    for row in rows:
        package_id = str(row.get("id") or "").strip()
        enquete = row.get("enquete") or {}
        enquete_id = str(enquete.get("id") or row.get("enquete_id") or "").strip()
        poll_id = str(enquete.get("external_poll_id") or "").strip()
        if package_id:
            package_row_by_id[package_id] = row
        if package_id and poll_id:
            package_poll_by_id[package_id] = poll_id
        if enquete_id and poll_id:
            poll_id_by_enquete_id[enquete_id] = poll_id

    for row in package_clients:
        pacote_id = str(row.get("pacote_id") or "").strip()
        cliente = row.get("cliente") or {}
        if not pacote_id:
            continue
        try:
            qty = int(row.get("qty") or 0)
        except Exception:
            qty = 0
        clients_by_package[pacote_id].append(
            {
                "name": cliente.get("nome") or "Cliente",
                "phone": cliente.get("celular") or "",
                "qty": qty,
                "unit_price": float(row.get("unit_price") or 0),
                "subtotal": float(row.get("subtotal") or 0),
                "commission_amount": float(row.get("commission_amount") or 0),
                "total_amount": float(row.get("total_amount") or 0),
            }
        )
        package_row = package_row_by_id.get(pacote_id) or {}
        status = str(package_row.get("status") or "").strip().lower()
        poll_id = package_poll_by_id.get(pacote_id) or ""
        customer_key = str(row.get("cliente_id") or cliente.get("celular") or "").strip()
        vote_id = str(row.get("voto_id") or "").strip()
        if poll_id and customer_key and status in {"approved", "closed"}:
            assigned_qty_by_poll_customer[(poll_id, customer_key)] += qty
        if vote_id:
            assigned_vote_ids.add(vote_id)

    open_votes_by_poll: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in current_votes:
        cliente = row.get("cliente") or {}
        qty = int(row.get("qty") or 0)
        if qty <= 0:
            continue
        if str(row.get("status") or "").strip().lower() == "out":
            continue
        vote_id = str(row.get("id") or "").strip()
        enquete_id = str(row.get("enquete_id") or "").strip()
        poll_id = poll_id_by_enquete_id.get(enquete_id) or ""
        if not poll_id:
            continue
        customer_key = str(row.get("cliente_id") or cliente.get("celular") or "").strip()
        assigned_qty = assigned_qty_by_poll_customer.get((poll_id, customer_key), 0)
        remaining_qty = max(qty - assigned_qty, 0)
        if remaining_qty <= 0:
            continue
        open_votes_by_poll[poll_id].append(
            {
                "name": cliente.get("nome") or "Cliente",
                "phone": cliente.get("celular") or "",
                "qty": remaining_qty,
            }
        )

    dates = processors.get_date_range()
    now = dates.get("now") or processors.datetime.now()
    last_72h_start = now - processors.timedelta(hours=72)
    last_24h_start = now - processors.timedelta(hours=24)
    last_7d_start = now - processors.timedelta(days=7)
    packages = {"open": [], "closed_today": [], "closed_week": [], "confirmed_today": [], "rejected_today": []}
    confirmed_last7 = [0] * 7
    for row in rows:
        enquete = row.get("enquete") or {}
        if not _chat_id_allowed(enquete.get("chat_id")):
            continue
        produto = enquete.get("produto") or {}
        package_id = str(row.get("id") or "").strip()
        poll_id = str(enquete.get("external_poll_id") or "").strip()
        if not package_id or not poll_id:
            continue

        opened_at = row.get("opened_at") or enquete.get("created_at_provider")
        closed_at = row.get("closed_at")
        approved_at = row.get("approved_at")
        cancelled_at = row.get("cancelled_at")
        if not _include_for_monitored_group(
            enquete.get("chat_id"),
            enquete.get("created_at_provider"),
            opened_at,
            closed_at,
            approved_at,
            cancelled_at,
            row.get("updated_at"),
        ):
            continue
        if not _include_from_floor(opened_at, closed_at, approved_at, enquete.get("created_at_provider")):
            continue

        item = {
            "id": _legacy_package_id(poll_id, row.get("sequence_no"), package_id),
            "source_package_id": package_id,
            "poll_id": poll_id,
            "poll_title": row.get("custom_title") or enquete.get("titulo") or poll_id,
            "chat_id": enquete.get("chat_id"),
            "image": processors.build_drive_image_url(
                processors.resolve_enquete_drive_file_id(enquete, produto)
            ),
            "qty": int(row.get("total_qty") or 0),
            "opened_at": _safe_dt(opened_at).isoformat() if _safe_dt(opened_at) else None,
            "closed_at": _safe_dt(closed_at).isoformat() if _safe_dt(closed_at) else None,
            "confirmed_at": _safe_dt(approved_at).isoformat() if _safe_dt(approved_at) else None,
            "cancelled_at": _safe_dt(cancelled_at).isoformat() if _safe_dt(cancelled_at) else None,
            "status": row.get("status") or "open",
            "tag": row.get("tag"),
            "fornecedor": row.get("fornecedor"),
            "pdf_status": row.get("pdf_status"),
            "pdf_file_name": row.get("pdf_file_name"),
            "pdf_sent_at": _safe_dt(row.get("pdf_sent_at")).isoformat() if _safe_dt(row.get("pdf_sent_at")) else None,
            "pdf_attempts": int(row.get("pdf_attempts") or 0),
            "confirmed_by": row.get("confirmed_by"),
            "cancelled_by": row.get("cancelled_by"),
            "votes": clients_by_package.get(package_id, []),
        }
        annotate_group(item, enquete.get("chat_id"))

        status = str(row.get("status") or "open").strip().lower()
        opened_dt = _safe_dt(opened_at)
        closed_dt = _safe_dt(closed_at)
        approved_dt = _safe_dt(approved_at)

        if status == "open" and opened_dt and opened_dt >= last_72h_start:
            item["votes"] = sorted(
                open_votes_by_poll.get(poll_id, []),
                key=lambda vote: int(vote.get("qty") or 0),
                reverse=True,
            )
            packages["open"].append(item)
            continue

        if status == "closed" and closed_dt:
            # Proteger contra pacotes órfãos (closed sem membros em pacote_clientes)
            if not item.get("votes"):
                continue
            if closed_dt >= last_72h_start:
                packages["closed_today"].append(item)
            if closed_dt >= dates["week_start"]:
                packages["closed_week"].append(item)
            continue

        if status == "approved" and approved_dt:
            if approved_dt >= last_72h_start:
                packages["confirmed_today"].append(item)
            for i in range(1, 8):
                day_start = dates["today_start"] - processors.timedelta(days=i)
                day_end = day_start + processors.timedelta(days=1)
                if day_start <= approved_dt < day_end:
                    confirmed_last7[i - 1] += 1
                    break
            continue

        if status == "cancelled":
            cancelled_dt = _safe_dt(row.get("cancelled_at") or row.get("updated_at") or closed_at)
            # Cancelados: mostra nos últimos 7 dias (desaparecem depois)
            # + filtra órfãos sem membros (cancelamentos de pacotes vazios)
            if cancelled_dt and cancelled_dt >= last_7d_start and item.get("votes"):
                packages["rejected_today"].append(item)

    packages["open"].sort(key=lambda item: item.get("opened_at") or "", reverse=True)
    packages["closed_today"].sort(key=lambda item: item.get("closed_at") or "", reverse=True)
    packages["closed_week"].sort(key=lambda item: item.get("closed_at") or "", reverse=True)
    packages["confirmed_today"].sort(key=lambda item: item.get("confirmed_at") or "", reverse=True)
    packages["rejected_today"].sort(key=lambda item: item.get("cancelled_at") or item.get("closed_at") or "", reverse=True)

    avg_confirmed = (sum(confirmed_last7) / 7) if confirmed_last7 else 0
    yesterday_confirmed = confirmed_last7[0] if confirmed_last7 else 0
    week_ago_confirmed = confirmed_last7[6] if len(confirmed_last7) >= 7 else 0
    packages_summary_confirmed = {
        "today": len(packages["confirmed_today"]),
        "yesterday": yesterday_confirmed,
        "last_7_days": confirmed_last7,
        "avg_7_days": avg_confirmed,
        "same_weekday_last_week": week_ago_confirmed,
    }

    return {
        "packages": packages,
        "packages_summary_confirmed": packages_summary_confirmed,
    }
