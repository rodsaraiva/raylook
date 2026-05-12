from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

logger = logging.getLogger("raylook.finance_service")

from app.services.domain_lookup import get_poll_chat_id_by_poll_id, parse_legacy_package_id
from app.services.group_context_service import (
    annotate_group,
    get_test_group_monitor_started_at,
    is_test_group_monitoring_enabled,
    monitored_chat_ids,
    normalize_chat_id,
    test_group_chat_id,
)
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
from app.services.runtime_state_service import (
    FINANCE_CHARGES_STATE_KEY,
    FINANCE_STATS_STATE_KEY,
    load_runtime_state,
    runtime_state_enabled,
    save_runtime_state,
)
from metrics.processors import build_drive_image_url, parse_timestamp, resolve_enquete_drive_file_id
from finance.manager import FinanceManager


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _finance_timezone() -> ZoneInfo | None:
    tz_name = str(os.getenv("TZ", "America/Sao_Paulo") or "America/Sao_Paulo").strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return None


def _local_date(value: Any):
    dt = parse_timestamp(value)
    return dt.date() if dt else None


def _today_local_date():
    tz = _finance_timezone()
    return datetime.now(tz).date() if tz else datetime.now().date()


def _include_for_test_group_monitoring(*timestamps: Any) -> bool:
    started_at = get_test_group_monitor_started_at()
    floor = _parse_dt(started_at)
    if floor is None:
        return True
    for value in timestamps:
        dt = _parse_dt(value)
        if dt and dt >= floor:
            return True
    return False


def _allowed_monitored_chat_ids() -> set[str]:
    return {normalize_chat_id(chat_id) for chat_id in monitored_chat_ids() if normalize_chat_id(chat_id)}


def _should_include_charge(charge: Dict[str, Any]) -> bool:
    chat_id = normalize_chat_id(charge.get("chat_id"))
    allowed = _allowed_monitored_chat_ids()
    if allowed and chat_id and chat_id not in allowed:
        return False
    if chat_id != test_group_chat_id() or not is_test_group_monitoring_enabled():
        return True
    return _include_for_test_group_monitoring(
        charge.get("poll_created_at"),
        charge.get("created_at"),
        charge.get("updated_at"),
        charge.get("paid_at"),
    )


def _normalize_charge(
    row: Dict[str, Any],
    venda: Dict[str, Any],
    cliente: Dict[str, Any],
    produto: Dict[str, Any],
    *,
    enquete: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    enquete = enquete or {}
    status = row.get("status") or "pending"
    # sent_at: para cobranças Asaas, usar updated_at quando status indica que o PIX foi gerado
    sent_at = None
    if status in ("sent", "paid") and row.get("updated_at"):
        sent_at = row.get("updated_at")
    charge = {
        "id": row.get("id"),
        "package_id": venda.get("pacote_id"),
        "asaas_id": row.get("provider_payment_id"),
        "poll_title": enquete.get("titulo") or produto.get("nome") or "Enquete",
        "customer_name": cliente.get("nome") or "Desconhecido",
        "customer_phone": cliente.get("celular") or "",
        "quantity": int(venda.get("qty") or 0),
        "subtotal": float(venda.get("subtotal") or 0),
        "commission_percent": float(venda.get("commission_percent") or 0),
        "commission_amount": float(venda.get("commission_amount") or 0),
        "total_amount": float(venda.get("total_amount") or 0),
        "status": status,
        "payment_link": row.get("payment_link"),
        "due_date": row.get("due_date"),
        "paid_at": row.get("paid_at"),
        "sent_at": sent_at,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "poll_created_at": enquete.get("created_at_provider"),
    }
    annotate_group(charge, enquete.get("chat_id"))
    return charge


def _expand_legacy_history_charge(
    charge: Dict[str, Any],
    *,
    venda: Dict[str, Any],
    cliente: Dict[str, Any],
    produto: Dict[str, Any],
    enquete: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = _normalize_charge(
        {
            "id": charge.get("id"),
            "provider_payment_id": charge.get("mercadopago_id") or charge.get("mercadopago_payment_id") or charge.get("asaas_id"),
            "payment_link": charge.get("payment_link"),
            "due_date": charge.get("due_date"),
            "paid_at": charge.get("paid_at"),
            "status": charge.get("status"),
            "created_at": charge.get("created_at"),
            "updated_at": charge.get("updated_at"),
        },
        venda,
        cliente,
        produto,
        enquete=enquete,
    )
    normalized.update(
        {
            "package_id": charge.get("package_id") or venda.get("pacote_id"),
            "poll_title": charge.get("poll_title") or produto.get("nome") or "Enquete",
            "customer_name": charge.get("customer_name") or cliente.get("nome") or "Desconhecido",
            "customer_phone": charge.get("customer_phone") or cliente.get("celular") or "",
            "quantity": int(charge.get("quantity") or venda.get("qty") or 0),
            "subtotal": float(charge.get("subtotal") or venda.get("subtotal") or 0),
            "commission_percent": float(charge.get("commission_percent") or venda.get("commission_percent") or 0),
            "commission_amount": float(charge.get("commission_amount") or venda.get("commission_amount") or 0),
            "total_amount": float(charge.get("total_amount") or venda.get("total_amount") or 0),
        }
    )
    return normalized


def _merge_charge_with_legacy_metadata(
    charge: Dict[str, Any],
    legacy_charge: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not isinstance(legacy_charge, dict) or not legacy_charge:
        return charge

    merged = dict(charge)
    fallback_payment_id = (
        legacy_charge.get("mercadopago_id")
        or legacy_charge.get("mercadopago_payment_id")
        or legacy_charge.get("provider_payment_id")
        or legacy_charge.get("asaas_id")
    )
    if not merged.get("asaas_id") and fallback_payment_id:
        merged["asaas_id"] = fallback_payment_id

    for field in ("payment_link", "due_date", "paid_at", "poll_title", "customer_name", "customer_phone"):
        if not merged.get(field) and legacy_charge.get(field):
            merged[field] = legacy_charge.get(field)

    if legacy_charge.get("sent_at"):
        merged["sent_at"] = legacy_charge.get("sent_at")
    if legacy_charge.get("image"):
        merged["image"] = legacy_charge.get("image")
    if legacy_charge.get("image_thumb"):
        merged["image_thumb"] = legacy_charge.get("image_thumb")
    legacy_chat_id = _resolve_legacy_chat_id(legacy_charge)
    if legacy_chat_id and not normalize_chat_id(merged.get("chat_id")):
        annotate_group(merged, legacy_chat_id)

    return merged


def _resolve_legacy_chat_id(legacy_charge: Dict[str, Any] | None) -> str | None:
    if not isinstance(legacy_charge, dict) or not legacy_charge:
        return None

    chat_id = normalize_chat_id(legacy_charge.get("chat_id"))
    if chat_id:
        return chat_id

    package_id = str(legacy_charge.get("package_id") or "").strip()
    poll_id, _sequence_no = parse_legacy_package_id(package_id)
    if not poll_id:
        return None

    resolved = normalize_chat_id(get_poll_chat_id_by_poll_id(poll_id))
    return resolved or None


def _chunked(values: Iterable[str], size: int = 200) -> List[List[str]]:
    items = [str(value) for value in values if str(value).strip()]
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def _select_in_batches(
    client: SupabaseRestClient,
    table: str,
    *,
    columns: str,
    filter_field: str,
    values: Iterable[str],
    order: str | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for batch in _chunked(sorted(set(values)), size=200):
        rows.extend(
            client.select_all(
                table,
                columns=columns,
                filters=[(filter_field, "in", batch)],
                order=order,
            )
        )
    return rows


def _hydrate_charge_rows(
    client: SupabaseRestClient,
    pagamentos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    pagamentos = pagamentos if isinstance(pagamentos, list) else []
    if not pagamentos:
        return []

    venda_ids = [str(row.get("venda_id")) for row in pagamentos if row.get("venda_id")]
    vendas = _select_in_batches(
        client,
        "vendas",
        columns=(
            "id,pacote_id,cliente_id,produto_id,qty,subtotal,commission_percent,commission_amount,total_amount,"
            "pacote:pacote_id(id,enquete:enquete_id(chat_id,titulo,created_at_provider))"
        ),
        filter_field="id",
        values=venda_ids,
    )
    venda_by_id = {str(row.get("id")): row for row in vendas if row.get("id")}

    cliente_ids = sorted({str(row.get("cliente_id")) for row in vendas if row.get("cliente_id")})
    produto_ids = sorted({str(row.get("produto_id")) for row in vendas if row.get("produto_id")})

    clientes = (
        _select_in_batches(
            client,
            "clientes",
            columns="id,nome,celular",
            filter_field="id",
            values=cliente_ids,
        )
        if cliente_ids
        else []
    )
    produtos = (
        _select_in_batches(
            client,
            "produtos",
            columns="id,nome",
            filter_field="id",
            values=produto_ids,
        )
        if produto_ids
        else []
    )

    cliente_by_id = {str(row.get("id")): row for row in clientes if row.get("id")}
    produto_by_id = {str(row.get("id")): row for row in produtos if row.get("id")}

    charges: List[Dict[str, Any]] = []
    for pagamento in pagamentos:
        venda = venda_by_id.get(str(pagamento.get("venda_id")))
        if not venda:
            continue
        cliente = cliente_by_id.get(str(venda.get("cliente_id")), {})
        produto = produto_by_id.get(str(venda.get("produto_id")), {})
        pacote = venda.get("pacote") or {}
        enquete = pacote.get("enquete") or {}
        payload = pagamento.get("payload_json") or {}
        normalized = _normalize_charge(pagamento, venda, cliente, produto, enquete=enquete)
        if isinstance(payload, dict) and payload.get("source") == "official_runtime_api":
            history = payload.get("legacy_charge_history") or []
            legacy_charge = None
            if isinstance(history, list) and history:
                candidates = [row for row in history if isinstance(row, dict)]
                if candidates:
                    legacy_charge = max(
                        candidates,
                        key=lambda row: str(
                            row.get("updated_at")
                            or row.get("sent_at")
                            or row.get("created_at")
                            or ""
                        ),
                    )
            if legacy_charge is None:
                single_legacy = payload.get("legacy_charge") or {}
                if isinstance(single_legacy, dict) and single_legacy:
                    legacy_charge = single_legacy
            normalized = _merge_charge_with_legacy_metadata(normalized, legacy_charge)
        if not _should_include_charge(normalized):
            continue
        charges.append(normalized)

    charges.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return charges


def _filter_charge_rows(
    charges: List[Dict[str, Any]],
    *,
    status: str | None = None,
    search: str | None = None,
) -> List[Dict[str, Any]]:
    """F-040: aceita aliases de filtro de status.

    O frontend envia 'pending' mas no banco as charges ativas podem estar
    com status 'created' (acabou de criar), 'sent' (mensagem enviada) ou
    'erro no envio'. Mapeamos tudo pro grupo 'pending' para que o filtro
    funcione como o operador espera.

    Aliases:
      - pending/pendente → {created, sent, pending}
      - enviando → {enviando}
      - erro no envio → {erro no envio}
      - paid/pago → {paid}
      - cancelled/cancelado → {cancelled, cancelado}
    """
    STATUS_GROUPS = {
        "pending": {"created", "sent", "pending"},
        "pendente": {"created", "sent", "pending"},
        "enviando": {"enviando"},
        "erro no envio": {"erro no envio"},
        "paid": {"paid"},
        "pago": {"paid"},
        "cancelled": {"cancelled", "cancelado"},
        "cancelado": {"cancelled", "cancelado"},
    }

    normalized_status = (status or "").strip().lower()
    normalized_search = (search or "").strip().lower()
    filtered = charges
    if normalized_status and normalized_status != "all":
        allowed = STATUS_GROUPS.get(normalized_status, {normalized_status})
        filtered = [row for row in filtered if str(row.get("status") or "").strip().lower() in allowed]
    if normalized_search:
        filtered = [
            row
            for row in filtered
            if normalized_search in str(row.get("customer_name") or "").lower()
            or normalized_search in str(row.get("customer_phone") or "")
            or normalized_search in str(row.get("poll_title") or "").lower()
        ]
    filtered.sort(
        key=lambda row: str(row.get("updated_at") or row.get("sent_at") or row.get("created_at") or ""),
        reverse=True,
    )
    return filtered


def _load_charge_snapshot() -> List[Dict[str, Any]] | None:
    if not runtime_state_enabled():
        return None
    payload = load_runtime_state(FINANCE_CHARGES_STATE_KEY) or {}
    items = payload.get("items")
    return items if isinstance(items, list) else None


def _save_charge_snapshot(charges: List[Dict[str, Any]]) -> None:
    if not runtime_state_enabled():
        return
    save_runtime_state(FINANCE_CHARGES_STATE_KEY, {"items": charges})


def _compute_charge_rows() -> List[Dict[str, Any]]:
    client = SupabaseRestClient.from_settings()
    pagamentos = client.select_all(
        "pagamentos",
        columns="id,venda_id,provider_payment_id,payment_link,due_date,paid_at,status,created_at,updated_at,payload_json",
        order="created_at.desc",
    )
    return _hydrate_charge_rows(client, pagamentos)


def _load_legacy_charges() -> List[Dict[str, Any]]:
    """Carrega cobranças históricas do MercadoPago (legacy_charges table)."""
    try:
        client = SupabaseRestClient.from_settings()
        rows = client.select_all(
            "legacy_charges",
            columns="id,package_id,mercadopago_id,poll_title,customer_name,customer_phone,"
                    "item_price,quantity,subtotal,commission_percent,commission_amount,total_amount,"
                    "status,created_at,confirmed_at,sent_at,paid_at,updated_at,image,source",
            order="created_at.desc",
        )
        return rows if isinstance(rows, list) else []
    except Exception:
        logger.warning("_load_legacy_charges: falha (tabela pode não existir)", exc_info=True)
        return []


def refresh_charge_snapshot() -> List[Dict[str, Any]]:
    if not supabase_domain_enabled():
        return list_charges()
    charges = _compute_charge_rows()
    # Merge com cobranças históricas do MercadoPago
    legacy = _load_legacy_charges()
    if legacy:
        existing_ids = {str(c.get("id")) for c in charges}
        for lc in legacy:
            if str(lc.get("id")) not in existing_ids:
                charges.append(lc)
        charges.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
    _save_charge_snapshot(charges)
    return charges


def list_package_charges(package_id: str) -> List[Dict[str, Any]]:
    if not supabase_domain_enabled():
        return []

    normalized_package_id = str(package_id or "").strip()
    if not normalized_package_id:
        return []

    client = SupabaseRestClient.from_settings()
    vendas = client.select_all(
        "vendas",
        columns="id",
        filters=[("pacote_id", "eq", normalized_package_id)],
    )
    venda_ids = [str(row.get("id")) for row in vendas if row.get("id")]
    if not venda_ids:
        return []

    pagamentos = _select_in_batches(
        client,
        "pagamentos",
        columns="id,venda_id,provider_payment_id,payment_link,pix_payload,due_date,paid_at,status,created_at,updated_at,payload_json",
        filter_field="venda_id",
        values=venda_ids,
        order="created_at.desc",
    )
    charges = _hydrate_charge_rows(client, pagamentos)
    charges.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return [row for row in charges if str(row.get("package_id") or "").strip() == normalized_package_id]


def update_payment_record_by_id(
    payment_id: str,
    *,
    provider: str | None = None,
    provider_payment_id: str | None = None,
    payment_link: str | None = None,
    pix_payload: str | None = None,
    due_date: str | None = None,
    paid_at: str | None = None,
    status: str | None = None,
    payload_json_patch: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not supabase_domain_enabled():
        return {}

    normalized_payment_id = str(payment_id or "").strip()
    if not normalized_payment_id:
        return {}

    client = SupabaseRestClient.from_settings()
    existing = client.select(
        "pagamentos",
        columns="id,payload_json",
        filters=[("id", "eq", normalized_payment_id)],
        single=True,
    )
    if not isinstance(existing, dict):
        return {}

    payload: Dict[str, Any] = {}
    if provider is not None:
        payload["provider"] = provider
    if provider_payment_id is not None:
        payload["provider_payment_id"] = provider_payment_id
    if payment_link is not None:
        payload["payment_link"] = payment_link
    if pix_payload is not None:
        payload["pix_payload"] = pix_payload
    if due_date is not None:
        payload["due_date"] = due_date
    if paid_at is not None:
        payload["paid_at"] = paid_at
    if status is not None:
        payload["status"] = status
    if payload_json_patch is not None:
        existing_payload = existing.get("payload_json")
        merged_payload = dict(existing_payload) if isinstance(existing_payload, dict) else {}
        merged_payload.update(payload_json_patch)
        payload["payload_json"] = merged_payload

    if not payload:
        return existing

    rows = client.update(
        "pagamentos",
        payload,
        filters=[("id", "eq", normalized_payment_id)],
        returning="representation",
    )
    return rows[0] if rows else {}


def list_charges() -> List[Dict[str, Any]]:
    if not supabase_domain_enabled():
        return FinanceManager().list_charges()
    snapshot = _load_charge_snapshot()
    if snapshot is not None:
        return snapshot
    return refresh_charge_snapshot()


def list_charges_page(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    search: str | None = None,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size

    if not supabase_domain_enabled():
        filtered = _filter_charge_rows(list_charges(), status=status, search=search)
        items = filtered[offset: offset + page_size]
        return {
            "items": items,
            "total": len(filtered),
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": offset + page_size < len(filtered),
        }

    normalized_search = (search or "").strip()
    snapshot = _load_charge_snapshot()
    if snapshot is not None:
        filtered = _filter_charge_rows(snapshot, status=status, search=normalized_search)
        items = filtered[offset: offset + page_size]
        return {
            "items": items,
            "total": len(filtered),
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": offset + page_size < len(filtered),
        }
    if normalized_search:
        filtered = _filter_charge_rows(list_charges(), status=status, search=normalized_search)
        items = filtered[offset: offset + page_size]
        return {
            "items": items,
            "total": len(filtered),
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": offset + page_size < len(filtered),
        }

    client = SupabaseRestClient.from_settings()
    filters = []
    normalized_status = (status or "").strip()
    if normalized_status and normalized_status != "all":
        filters.append(("status", "eq", normalized_status))
    pagamentos = client.select(
        "pagamentos",
        columns="id,venda_id,provider_payment_id,payment_link,due_date,paid_at,status,created_at,updated_at,payload_json",
        filters=filters,
        order="created_at.desc",
        limit=page_size + 1,
        offset=offset,
    )
    pagamentos = pagamentos if isinstance(pagamentos, list) else []
    has_next = len(pagamentos) > page_size
    items = _hydrate_charge_rows(client, pagamentos[:page_size])
    return {
        "items": items,
        "total": offset + len(items) + (1 if has_next else 0),
        "page": page,
        "page_size": page_size,
        "has_prev": page > 1,
        "has_next": has_next,
    }


def get_package_charge_contexts(package_id: str) -> List[Dict[str, Any]]:
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    vendas = client.select_all(
        "vendas",
        columns=(
            "id,pacote_id,cliente_id,produto_id,qty,subtotal,commission_percent,commission_amount,total_amount,"
            "pacote:pacote_id(id,enquete:enquete_id(chat_id,titulo,drive_file_id)),"
            "cliente:cliente_id(id,nome,celular),"
            "produto:produto_id(id,nome,drive_file_id)"
        ),
        filters=[("pacote_id", "eq", package_id)],
        order="created_at.asc",
    )
    if not vendas:
        return []

    venda_ids = [str(row.get("id")) for row in vendas if row.get("id")]
    pagamentos = (
        _select_in_batches(
            client,
            "pagamentos",
            columns="id,venda_id,provider,provider_customer_id,provider_payment_id,payment_link,pix_payload,due_date,paid_at,status,payload_json",
            filter_field="venda_id",
            values=venda_ids,
            order="created_at.asc",
        )
        if venda_ids
        else []
    )
    pagamentos_by_venda = {str(row.get("venda_id")): row for row in pagamentos if row.get("venda_id")}

    result: List[Dict[str, Any]] = []
    for venda in vendas:
        cliente = venda.get("cliente") or {}
        produto = venda.get("produto") or {}
        pacote = venda.get("pacote") or {}
        enquete = pacote.get("enquete") or {}
        pagamento = pagamentos_by_venda.get(str(venda.get("id")), {})
        result.append(
            {
                "venda_id": venda.get("id"),
                "pacote_id": venda.get("pacote_id"),
                "customer_name": cliente.get("nome") or "Cliente",
                "customer_phone": cliente.get("celular") or "",
                "poll_title": enquete.get("titulo") or produto.get("nome") or "Enquete",
                "chat_id": enquete.get("chat_id"),
                "quantity": int(venda.get("qty") or 0),
                "subtotal": float(venda.get("subtotal") or 0),
                "commission_percent": float(venda.get("commission_percent") or 0),
                "commission_amount": float(venda.get("commission_amount") or 0),
                "total_amount": float(venda.get("total_amount") or 0),
                "image": build_drive_image_url(resolve_enquete_drive_file_id(enquete, produto)),
                "pagamento": pagamento,
            }
        )
    return result


def upsert_payment_record(
    venda_id: str,
    *,
    provider: str = "asaas",
    provider_customer_id: str | None = None,
    provider_payment_id: str | None = None,
    payment_link: str | None = None,
    pix_payload: str | None = None,
    due_date: str | None = None,
    paid_at: str | None = None,
    status: str | None = None,
    payload_json: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not supabase_domain_enabled():
        raise RuntimeError("Supabase domain disabled")

    payload: Dict[str, Any] = {"venda_id": venda_id, "provider": provider}
    if provider_customer_id is not None:
        payload["provider_customer_id"] = provider_customer_id
    if provider_payment_id is not None:
        payload["provider_payment_id"] = provider_payment_id
    if payment_link is not None:
        payload["payment_link"] = payment_link
    if pix_payload is not None:
        payload["pix_payload"] = pix_payload
    if due_date is not None:
        payload["due_date"] = due_date
    if paid_at is not None:
        payload["paid_at"] = paid_at
    if status is not None:
        payload["status"] = status
    if payload_json is not None:
        payload["payload_json"] = payload_json

    client = SupabaseRestClient.from_settings()
    return client.upsert_one("pagamentos", payload, on_conflict="venda_id")


def build_stats(charges: List[Dict[str, Any]]) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "timeline": {},
        "status_distribution": {"paid": 0, "pending": 0},
        "totals": {"revenue": 0.0, "commission": 0.0},
    }

    for charge in charges:
        status = charge.get("status", "pending")
        total = float(charge.get("total_amount") or 0)
        subtotal = float(charge.get("subtotal") or total)
        commission = float(charge.get("commission_amount") or 0)

        created_at = charge.get("created_at")
        created_date = _local_date(created_at)
        if created_date:
            day_stats = stats["timeline"].setdefault(created_date.isoformat(), {"created": 0.0, "paid": 0.0})
            day_stats["created"] += total

        if status == "paid":
            stats["status_distribution"]["paid"] += 1
            stats["totals"]["revenue"] += subtotal
            stats["totals"]["commission"] += commission
            paid_date = _local_date(charge.get("paid_at") or charge.get("updated_at") or created_at)
            if paid_date:
                day_stats = stats["timeline"].setdefault(paid_date.isoformat(), {"created": 0.0, "paid": 0.0})
                day_stats["paid"] += total
        else:
            stats["status_distribution"]["pending"] += 1

    stats["timeline"] = dict(sorted(stats["timeline"].items()))
    return stats


def build_dashboard_stats(charges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """F-039: stats do painel financeiro.

    Definições (exclui cancelled em todas as contagens de "ativo"):
    - Pendente: status in (created, sent, pending, enviando, erro no envio)
    - Pago: status == paid
    - Cancelado: status == cancelled (reportado separadamente, não conta pendente)
    - Total ativo: pendente + pago (cancelled não entra)
    - % Pago: pago / (pendente + pago) * 100
    """
    PENDING_STATUSES = {"created", "sent", "pending", "enviando", "erro no envio"}
    PAID_STATUS = "paid"
    CANCELLED_STATUSES = {"cancelled", "cancelado"}

    def _status(c: Dict[str, Any]) -> str:
        return str(c.get("status") or "").strip().lower()

    today = _today_local_date()
    timeline: Dict[str, Dict[str, float]] = {}
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        timeline[date.strftime("%d/%m")] = {"created": 0.0, "paid": 0.0}

    for charge in charges:
        created_at = charge.get("created_at")
        charge_date = _local_date(created_at)
        if not charge_date:
            continue
        date_str = charge_date.strftime("%d/%m")
        if date_str not in timeline:
            continue
        if _status(charge) in CANCELLED_STATUSES:
            continue  # cancelled não entra na timeline
        total = float(charge.get("total_amount") or 0)
        timeline[date_str]["created"] += total
        if _status(charge) == PAID_STATUS:
            paid_date = _local_date(charge.get("paid_at") or charge.get("updated_at") or created_at)
            if paid_date:
                paid_key = paid_date.strftime("%d/%m")
                if paid_key in timeline:
                    timeline[paid_key]["paid"] += total

    pending_charges = [c for c in charges if _status(c) in PENDING_STATUSES]
    paid_charges = [c for c in charges if _status(c) == PAID_STATUS]
    cancelled_charges = [c for c in charges if _status(c) in CANCELLED_STATUSES]

    total_pending = sum(float(c.get("total_amount") or 0) for c in pending_charges)
    total_paid = sum(float(c.get("total_amount") or 0) for c in paid_charges)
    total_cancelled = sum(float(c.get("total_amount") or 0) for c in cancelled_charges)

    # F-065: alinhado com coluna "Data de Pagamento" na lista (updated_at).
    # paid_at do Asaas vem só com DATE (00:00), não representa a hora real
    # em que o pagamento caiu no sistema — updated_at sim.
    paid_today = [
        c
        for c in paid_charges
        if _local_date(c.get("updated_at") or c.get("paid_at") or c.get("created_at")) == today
    ]
    paid_today_total = sum(float(c.get("total_amount") or 0) for c in paid_today)

    active_count = len(pending_charges) + len(paid_charges)
    active_total = total_pending + total_paid

    return {
        "timeline": timeline,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_cancelled": total_cancelled,
        "total_active": active_total,
        "total_charges": len(charges),
        "pending_count": len(pending_charges),
        "paid_count": len(paid_charges),
        "cancelled_count": len(cancelled_charges),
        "active_count": active_count,
        "paid_today_total": paid_today_total,
        "paid_today_count": len(paid_today),
    }


def get_dashboard_stats() -> Dict[str, Any]:
    if not supabase_domain_enabled():
        return build_dashboard_stats(list_charges())
    if runtime_state_enabled():
        payload = load_runtime_state(FINANCE_STATS_STATE_KEY) or {}
        if payload:
            return payload
    charges = list_charges()
    stats = build_dashboard_stats(charges)
    if runtime_state_enabled():
        save_runtime_state(FINANCE_STATS_STATE_KEY, stats)
    return stats


def refresh_dashboard_stats() -> Dict[str, Any]:
    stats = build_dashboard_stats(refresh_charge_snapshot() if supabase_domain_enabled() else list_charges())
    if runtime_state_enabled():
        save_runtime_state(FINANCE_STATS_STATE_KEY, stats)
    return stats


# ---------------------------------------------------------------------------
# F-062: Gestão de contas a receber
# ---------------------------------------------------------------------------

PENDING_RECEIVABLE_STATUSES = ("created", "sent")
AGING_BUCKETS = (
    ("0-7", 0, 7),
    ("8-15", 8, 15),
    ("16-30", 16, 30),
    ("30+", 31, 10_000),
)


def _classify_bucket(age_days: int) -> str:
    for label, lo, hi in AGING_BUCKETS:
        if lo <= age_days <= hi:
            return label
    return "30+"


def _now_dt(now_iso: str | None) -> datetime:
    if now_iso:
        return datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    tz = _finance_timezone() or ZoneInfo("UTC")
    return datetime.now(tz=tz)


def build_receivables_by_client(now_iso: str | None = None) -> List[Dict[str, Any]]:
    """Agrega pagamentos pendentes (created/sent) por cliente.

    Retorna lista ordenada por idade do débito mais antigo desc.
    Cada item: {cliente_id, nome, celular_last4, total, count, oldest_age_days,
                bucket, charges:[{pagamento_id, pacote_id, enquete_titulo, valor,
                                  age_days, status}]}.
    """
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    pagamentos = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("status", "in", list(PENDING_RECEIVABLE_STATUSES))],
        order="created_at.asc",
    )
    if not pagamentos:
        return []

    venda_ids = list({str(p["venda_id"]) for p in pagamentos if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,pacote_id,total_amount",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}

    cliente_ids = list({str(v["cliente_id"]) for v in vendas if v.get("cliente_id")})
    clientes = _select_in_batches(
        client, "clientes",
        columns="id,nome,celular",
        filter_field="id", values=cliente_ids,
    )
    cliente_by_id = {str(c["id"]): c for c in clientes}

    pacote_ids = list({str(v["pacote_id"]) for v in vendas if v.get("pacote_id")})
    pacotes = _select_in_batches(
        client, "pacotes",
        columns="id,enquete_id,sequence_no",
        filter_field="id", values=pacote_ids,
    )
    pacote_by_id = {str(p["id"]): p for p in pacotes}

    enquete_ids = list({str(p["enquete_id"]) for p in pacotes if p.get("enquete_id")})
    enquetes = _select_in_batches(
        client, "enquetes",
        columns="id,titulo",
        filter_field="id", values=enquete_ids,
    )
    enquete_by_id = {str(e["id"]): e for e in enquetes}

    now = _now_dt(now_iso)
    by_cliente: Dict[str, Dict[str, Any]] = {}

    for pag in pagamentos:
        venda = venda_by_id.get(str(pag.get("venda_id")))
        if not venda:
            continue
        cliente_id = str(venda.get("cliente_id") or "")
        cliente = cliente_by_id.get(cliente_id)
        if not cliente:
            continue
        pacote = pacote_by_id.get(str(venda.get("pacote_id") or ""))
        enquete_titulo = ""
        if pacote:
            enq = enquete_by_id.get(str(pacote.get("enquete_id") or ""))
            if enq:
                enquete_titulo = enq.get("titulo") or ""

        created_at = _parse_dt(pag.get("created_at"))
        # Calcula idade em dias pela data (sem hora) para consistência
        age_days = (now.date() - created_at.date()).days if created_at else 0
        valor = float(venda.get("total_amount") or 0)

        bucket = by_cliente.setdefault(cliente_id, {
            "cliente_id": cliente_id,
            "nome": cliente.get("nome") or "",
            "celular_last4": str(cliente.get("celular") or "")[-4:],
            "total": 0.0,
            "count": 0,
            "oldest_age_days": 0,
            "bucket": "0-7",
            "charges": [],
        })
        bucket["total"] += valor
        bucket["count"] += 1
        if age_days > bucket["oldest_age_days"]:
            bucket["oldest_age_days"] = age_days
            bucket["bucket"] = _classify_bucket(age_days)
        bucket["charges"].append({
            "pagamento_id": str(pag["id"]),
            "pacote_id": str(venda.get("pacote_id") or ""),
            "enquete_titulo": enquete_titulo,
            "valor": valor,
            "age_days": age_days,
            "status": pag.get("status"),
        })

    rows = list(by_cliente.values())
    rows.sort(key=lambda r: r["oldest_age_days"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
    return rows


def build_aging_summary(now_iso: str | None = None) -> Dict[str, Any]:
    """KPIs de aging: total a receber, distribuição em buckets,
    idade média ponderada e taxa de conversão 30d.

    Retorna: {total_receivable, count, clients_count,
              buckets:{"0-7":{amount,count}, ...},
              avg_age_days, paid_rate_30d}.
    """
    empty_buckets = {label: {"amount": 0, "count": 0} for label, _, _ in AGING_BUCKETS}
    empty = {
        "total_receivable": 0, "count": 0, "clients_count": 0,
        "buckets": empty_buckets, "avg_age_days": 0, "paid_rate_30d": 0,
    }
    if not supabase_domain_enabled():
        return empty

    client = SupabaseRestClient.from_settings()
    now = _now_dt(now_iso)

    pagamentos_pendentes = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("status", "in", list(PENDING_RECEIVABLE_STATUSES))],
    )
    if not pagamentos_pendentes:
        empty_with_paid_rate = dict(empty)
        empty_with_paid_rate["paid_rate_30d"] = _paid_rate_30d(client, now)
        return empty_with_paid_rate

    venda_ids = list({str(p["venda_id"]) for p in pagamentos_pendentes if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,total_amount",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}

    buckets = {label: {"amount": 0.0, "count": 0} for label, _, _ in AGING_BUCKETS}
    total = 0.0
    weighted_age_sum = 0.0
    clientes = set()

    for pag in pagamentos_pendentes:
        venda = venda_by_id.get(str(pag.get("venda_id")))
        if not venda:
            continue
        valor = float(venda.get("total_amount") or 0)
        created_at = _parse_dt(pag.get("created_at"))
        # Diferença em dias usando .date() para evitar off-by-one por hora
        age_days = (now.date() - created_at.date()).days if created_at else 0
        bucket = _classify_bucket(age_days)
        buckets[bucket]["amount"] += valor
        buckets[bucket]["count"] += 1
        total += valor
        weighted_age_sum += valor * age_days
        if venda.get("cliente_id"):
            clientes.add(str(venda["cliente_id"]))

    return {
        "total_receivable": round(total, 2),
        "count": sum(b["count"] for b in buckets.values()),
        "clients_count": len(clientes),
        "buckets": {k: {"amount": round(v["amount"], 2), "count": v["count"]}
                    for k, v in buckets.items()},
        "avg_age_days": round(weighted_age_sum / total, 2) if total > 0 else 0,
        "paid_rate_30d": _paid_rate_30d(client, now),
    }


def _paid_rate_30d(client: SupabaseRestClient, now: datetime) -> float:
    """% de R$ pago sobre o total confirmado nos últimos 30d (rolling)."""
    cutoff = (now - timedelta(days=30)).isoformat()
    pagamentos = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("created_at", "gte", cutoff)],
    )
    if not pagamentos:
        return 0
    venda_ids = list({str(p["venda_id"]) for p in pagamentos if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,total_amount",
        filter_field="id", values=venda_ids,
    )
    valor_by_venda = {str(v["id"]): float(v.get("total_amount") or 0) for v in vendas}

    total = 0.0
    paid = 0.0
    for p in pagamentos:
        valor = valor_by_venda.get(str(p.get("venda_id")), 0)
        status = str(p.get("status") or "")
        if status in ("paid", "created", "sent"):
            total += valor
            if status == "paid":
                paid += valor
    return round(paid / total, 4) if total > 0 else 0


class PaymentNotFound(Exception):
    pass


def mark_payment_written_off(pagamento_id: str, *, reason: str) -> Dict[str, Any]:
    """Marca um pagamento como perdido. Idempotente: se já está written_off,
    retorna o estado atual sem sobrescrever."""
    if not supabase_domain_enabled():
        raise PaymentNotFound(pagamento_id)
    client = SupabaseRestClient.from_settings()
    existing = client.select(
        "pagamentos",
        columns="id,status,written_off_at,written_off_reason",
        filters=[("id", "eq", pagamento_id)],
        single=True,
    )
    if not isinstance(existing, dict) or not existing.get("id"):
        raise PaymentNotFound(pagamento_id)
    if existing.get("status") == "written_off":
        return existing

    now_iso = client.now_iso() if hasattr(client, "now_iso") else \
        datetime.now(tz=ZoneInfo("UTC")).isoformat()
    updates = {
        "status": "written_off",
        "written_off_at": now_iso,
        "written_off_reason": reason,
        "updated_at": now_iso,
    }
    client.update(
        "pagamentos",
        updates,
        filters=[("id", "eq", pagamento_id)],
    )
    # FakeSupabaseClient.update modifica in-place; retornamos o estado atualizado
    updated = client.select(
        "pagamentos",
        columns="id,status,written_off_at,written_off_reason",
        filters=[("id", "eq", pagamento_id)],
        single=True,
    )
    return updated if isinstance(updated, dict) else {**existing, **updates}


def build_payment_history(pagamento_id: str) -> List[Dict[str, Any]]:
    """Timeline derivada dos campos do pagamento + sessão do cliente.

    Cada evento: {kind, timestamp, label, reason?}.
    """
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    pag = client.select(
        "pagamentos",
        columns="id,venda_id,status,created_at,updated_at,pix_payload,paid_at,"
                "written_off_at,written_off_reason",
        filters=[("id", "eq", pagamento_id)],
        single=True,
    )
    if not isinstance(pag, dict) or not pag.get("id"):
        return []

    events: List[Dict[str, Any]] = []
    if pag.get("created_at"):
        events.append({
            "kind": "package_confirmed",
            "timestamp": pag["created_at"],
            "label": "Pacote confirmado",
        })
    if pag.get("pix_payload") and pag.get("updated_at"):
        events.append({
            "kind": "pix_generated",
            "timestamp": pag["updated_at"],
            "label": "PIX gerado (última tentativa registrada)",
        })

    venda = client.select(
        "vendas", columns="cliente_id",
        filters=[("id", "eq", pag.get("venda_id"))], single=True,
    )
    if isinstance(venda, dict) and venda.get("cliente_id"):
        cliente = client.select(
            "clientes", columns="session_expires_at",
            filters=[("id", "eq", venda["cliente_id"])], single=True,
        )
        if isinstance(cliente, dict) and cliente.get("session_expires_at"):
            expires = _parse_dt(cliente["session_expires_at"])
            if expires:
                # Sessão dura 30 dias — último acesso é expires - 30d
                last_access = (expires - timedelta(days=30)).isoformat()
                events.append({
                    "kind": "last_portal_access",
                    "timestamp": last_access,
                    "label": "Último acesso ao portal",
                })

    if pag.get("paid_at"):
        events.append({
            "kind": "paid",
            "timestamp": pag["paid_at"],
            "label": "Pago",
        })
    if pag.get("written_off_at"):
        events.append({
            "kind": "written_off",
            "timestamp": pag["written_off_at"],
            "label": "Marcado como perdido",
            "reason": pag.get("written_off_reason") or "",
        })

    events.sort(key=lambda e: e["timestamp"])
    return events
