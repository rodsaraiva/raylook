from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List
import logging

from app.config import settings
from app.services.runtime_state_service import (
    CUSTOMER_ROWS_STATE_KEY,
    load_runtime_state,
    runtime_state_enabled,
    save_runtime_state,
)
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.customer_service")

CUSTOMERS_FILE = Path(os.environ.get("DATA_DIR", "data")) / "customers.json"


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _ensure_data_dir() -> None:
    CUSTOMERS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _require_supabase_in_test_mode() -> None:
    if bool(getattr(settings, "TEST_MODE", False)) and not supabase_domain_enabled():
        raise RuntimeError("Staging test mode requires Supabase customer storage.")


def _load_customers_from_file() -> Dict[str, str]:
    if not CUSTOMERS_FILE.exists():
        return {}
    try:
        with CUSTOMERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {_normalize_phone(phone): str(name or "") for phone, name in data.items() if _normalize_phone(phone)}
    except Exception as exc:
        logger.error("Erro ao carregar clientes em arquivo: %s", exc)
        return {}


def _save_customers_to_file(customers: Dict[str, str]) -> None:
    _ensure_data_dir()
    tmp_path = CUSTOMERS_FILE.with_suffix(".json.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(customers, f, indent=2, ensure_ascii=False)
        tmp_path.replace(CUSTOMERS_FILE)
    except Exception as exc:
        logger.error("Erro ao salvar clientes em arquivo: %s", exc)
        if tmp_path.exists():
            tmp_path.unlink()


def load_customers() -> Dict[str, str]:
    _require_supabase_in_test_mode()
    if not supabase_domain_enabled():
        return _load_customers_from_file()

    client = SupabaseRestClient.from_settings()
    rows = client.select_all("clientes", columns="celular,nome", order="updated_at.desc")
    return {
        _normalize_phone(row.get("celular")): str(row.get("nome") or "")
        for row in rows
        if _normalize_phone(row.get("celular"))
    }


def save_customers(customers: Dict[str, str]) -> None:
    _require_supabase_in_test_mode()
    if supabase_domain_enabled():
        client = SupabaseRestClient.from_settings()
        payload = []
        for phone, name in customers.items():
            normalized = _normalize_phone(phone)
            if not normalized:
                continue
            payload.append({"celular": normalized, "nome": str(name or "")})
        if payload:
            client.insert("clientes", payload, upsert=True, on_conflict="celular", returning="minimal")
            refresh_customer_rows_snapshot()
        return
    _save_customers_to_file(customers)


def update_customer(phone: str, name: str) -> None:
    _require_supabase_in_test_mode()
    normalized = _normalize_phone(phone)
    if not normalized:
        return
    if supabase_domain_enabled():
        SupabaseRestClient.from_settings().upsert_one(
            "clientes",
            {"celular": normalized, "nome": name},
            on_conflict="celular",
        )
        refresh_customer_rows_snapshot()
        return

    customers = _load_customers_from_file()
    customers[normalized] = name
    _save_customers_to_file(customers)


def get_customer_name(phone: str, default: str = "Desconhecido") -> str:
    return load_customers().get(_normalize_phone(phone), default)


def sync_customer_names(data):
    return data


def _build_customer_rows_supabase() -> List[Dict[str, object]]:
    """Constrói as linhas da aba Clientes combinando:
    1. RPC get_customer_stats → qty de peças (pacotes closed/approved)
    2. legacy_charges → total_debt e total_paid (dados financeiros MercadoPago)
    3. pagamentos (se houver) → cobranças novas do Asaas
    """
    client = SupabaseRestClient.from_settings()

    # 1) Clientes base via RPC (nome, celular). qty e financeiro vêm do legacy_charges.
    qty_by_phone: Dict[str, Dict[str, object]] = {}
    try:
        stats = client.rpc("get_customer_stats", {})
        if isinstance(stats, list):
            for item in stats:
                phone = _normalize_phone(item.get("celular"))
                if not phone:
                    continue
                qty_by_phone[phone] = {
                    "name": str(item.get("nome") or ""),
                    "qty": 0,  # será preenchido pelo legacy_charges (peças pagas)
                    "total_debt": 0.0,
                    "total_paid": 0.0,
                    "last_pay_click_at": None,  # última vez que o cliente clicou "Pagar" no portal
                }
    except Exception as exc:
        logger.warning("get_customer_stats RPC falhou: %s", exc)

    # 2) Financeiro do legacy_charges (MercadoPago) — fonte principal
    # qty agora = peças PAGAS (não peças em pacotes fechados).
    # Só conta quando status='paid'. Pendente/cancelado/excluído não conta.
    PENDING = {"pending", "enviando", "erro no envio"}
    try:
        legacy = client.select_all(
            "legacy_charges",
            columns="customer_phone,customer_name,total_amount,quantity,status",
        )
        for lc in (legacy or []):
            phone = _normalize_phone(lc.get("customer_phone"))
            if not phone:
                continue
            amount = float(lc.get("total_amount") or 0)
            qty_charge = int(lc.get("quantity") or 0)
            status = str(lc.get("status") or "").strip().lower()

            if phone not in qty_by_phone:
                qty_by_phone[phone] = {
                    "name": str(lc.get("customer_name") or ""),
                    "qty": 0,
                    "total_debt": 0.0,
                    "total_paid": 0.0,
                    "last_pay_click_at": None,
                }

            entry = qty_by_phone[phone]
            if not entry.get("name") and lc.get("customer_name"):
                entry["name"] = str(lc["customer_name"])

            if status == "paid":
                entry["total_paid"] = round(float(entry["total_paid"]) + amount, 2)
                entry["qty"] = int(entry["qty"]) + qty_charge  # peças pagas
            elif status in PENDING:
                entry["total_debt"] = round(float(entry["total_debt"]) + amount, 2)
    except Exception as exc:
        logger.warning("legacy_charges aggregation falhou: %s", exc)

    # 3) Cobranças novas do Asaas (pagamentos + vendas)
    # vendas tem cliente_id → clientes.celular. pagamentos.status indica o estado.
    PENDING_ASAAS = {"created", "sent", "pending"}
    try:
        asaas_charges = client.select_all(
            "pagamentos",
            columns="id,venda_id,status,updated_at",
        )
        # Map venda_id → pagamento info
        venda_map: Dict[str, Dict] = {}
        for pg in (asaas_charges or []):
            vid = pg.get("venda_id")
            if vid:
                venda_map[vid] = pg

        if venda_map:
            vendas = client.select_all(
                "vendas",
                columns="id,cliente_id,total_amount,qty",
            )
            # Map cliente_id → celular
            cliente_ids = {v.get("cliente_id") for v in (vendas or []) if v.get("cliente_id")}
            phone_by_cid: Dict[str, str] = {}
            if cliente_ids:
                clientes = client.select_all(
                    "clientes",
                    columns="id,celular,nome",
                )
                for c in (clientes or []):
                    cid = c.get("id")
                    if cid:
                        phone_by_cid[cid] = _normalize_phone(c.get("celular"))

            for v in (vendas or []):
                vid = v.get("id")
                pg = venda_map.get(vid)
                if not pg:
                    continue  # Venda sem pagamento (cobrança excluída) — não contar
                cid = v.get("cliente_id")
                phone = phone_by_cid.get(cid, "")
                if not phone:
                    continue
                amount = float(v.get("total_amount") or 0)
                pg_status = str(pg.get("status") or "").strip().lower()

                if phone not in qty_by_phone:
                    qty_by_phone[phone] = {
                        "name": "",
                        "qty": 0,
                        "total_debt": 0.0,
                        "total_paid": 0.0,
                        "last_pay_click_at": None,
                    }

                entry = qty_by_phone[phone]
                venda_qty = int(v.get("qty") or 0)
                # Cancelados não contam em nada
                if pg_status in ("cancelled", "cancelado"):
                    continue
                entry["qty"] = int(entry["qty"]) + venda_qty
                if pg_status == "paid":
                    entry["total_paid"] = round(float(entry["total_paid"]) + amount, 2)
                elif pg_status in PENDING_ASAAS:
                    entry["total_debt"] = round(float(entry["total_debt"]) + amount, 2)

                # Último clique em "Pagar" individual: updated_at quando status
                # vai de created → sent (o cliente gerou o PIX individual).
                # Pagamentos paid também contam (o clique veio antes do pagamento).
                if pg_status in ("sent", "paid"):
                    candidate = pg.get("updated_at")
                    if candidate and (
                        entry.get("last_pay_click_at") is None
                        or str(candidate) > str(entry["last_pay_click_at"])
                    ):
                        entry["last_pay_click_at"] = candidate
    except Exception as exc:
        logger.warning("pagamentos/vendas aggregation falhou: %s", exc)

    # 3b) Último clique em "Pagar todos" (combined PIX) via app_runtime_state.
    try:
        combined_states = client.select_all(
            "app_runtime_state",
            columns="key,payload_json,updated_at",
            filters=[("key", "like", "combined_pix_%")],
        )
        # cliente_id → celular (reaproveita o mapa se já existe, senão busca)
        phone_by_cid_combined: Dict[str, str] = {}
        try:
            if 'phone_by_cid' in locals() and phone_by_cid:
                phone_by_cid_combined = phone_by_cid  # type: ignore[name-defined]
            else:
                clientes_full = client.select_all("clientes", columns="id,celular")
                for c in clientes_full or []:
                    cid = c.get("id")
                    if cid:
                        phone_by_cid_combined[cid] = _normalize_phone(c.get("celular"))
        except Exception:
            pass

        for state in combined_states or []:
            payload = state.get("payload_json") or {}
            cid = payload.get("cliente_id")
            if not cid:
                continue
            phone = phone_by_cid_combined.get(cid) or ""
            if not phone or phone not in qty_by_phone:
                continue
            clicked_at = payload.get("created_at") or state.get("updated_at")
            if not clicked_at:
                continue
            entry = qty_by_phone[phone]
            if entry.get("last_pay_click_at") is None or str(clicked_at) > str(entry["last_pay_click_at"]):
                entry["last_pay_click_at"] = clicked_at
    except Exception as exc:
        logger.warning("combined_pix aggregation falhou: %s", exc)

    # 4) Ordenar por total_paid desc, qty desc
    rows = list(qty_by_phone.values())
    for r in rows:
        r["phone"] = next(
            (ph for ph, v in qty_by_phone.items() if v is r), ""
        )
    rows.sort(key=lambda r: (-float(r.get("total_paid") or 0), -int(r.get("qty") or 0)))
    return rows


def list_customer_rows() -> List[Dict[str, object]]:
    if not supabase_domain_enabled():
        from app.services.finance_service import list_charges
        from app.services.staging_dry_run_service import build_customer_rows

        return build_customer_rows(load_customers(), list_charges())

    if runtime_state_enabled():
        payload = load_runtime_state(CUSTOMER_ROWS_STATE_KEY) or {}
        items = payload.get("items")
        if isinstance(items, list):
            return items

    rows = refresh_customer_rows_snapshot()
    return rows


def refresh_customer_rows_snapshot() -> List[Dict[str, object]]:
    if not supabase_domain_enabled():
        from app.services.finance_service import list_charges
        from app.services.staging_dry_run_service import build_customer_rows

        rows_legacy = build_customer_rows(load_customers(), list_charges())
        rows_legacy.sort(
            key=lambda row: (-float(row.get("total_paid") or 0), -int(row.get("qty") or 0), str(row.get("name") or ""))
        )
        return rows_legacy

    # F-036: usa a RPC get_customer_stats que já ordena e agrega corretamente
    # (vs o cálculo anterior via charges, que só contava pacotes confirmados
    # e não expunha total_debt).
    rows = _build_customer_rows_supabase()
    if runtime_state_enabled():
        save_runtime_state(CUSTOMER_ROWS_STATE_KEY, {"items": rows})
    return rows


def list_customer_rows_page(
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
) -> Dict[str, object]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    rows = list_customer_rows()

    normalized_search = (search or "").strip().lower()
    if normalized_search:
        rows = [
            row
            for row in rows
            if normalized_search in str(row.get("name") or "").lower()
            or normalized_search in str(row.get("phone") or "")
        ]

    offset = (page - 1) * page_size
    items = rows[offset: offset + page_size]
    return {
        "items": items,
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "has_prev": page > 1,
        "has_next": offset + page_size < len(rows),
    }


def search_customers_light(q: str, limit: int = 10) -> list[dict]:
    """Busca clientes por nome ou telefone, retorna [{phone, name}].

    Sem suporte a OR no SupabaseRestClient — faz 2 queries e mescla deduplicando por celular.
    """
    q_norm = (q or "").strip()
    if len(q_norm) < 2:
        return []

    from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()

    results: dict[str, dict] = {}

    # Busca por nome (ilike)
    try:
        by_name = client.select(
            "clientes",
            columns="celular,nome",
            filters=[("nome", "ilike", f"*{q_norm}*")],
            limit=limit,
        ) or []
        if isinstance(by_name, list):
            for row in by_name:
                phone = (row.get("celular") or "").strip()
                if phone:
                    results[phone] = {"phone": phone, "name": row.get("nome") or ""}
    except Exception:
        pass

    # Busca por celular (ilike sobre dígitos do q)
    digits = "".join(ch for ch in q_norm if ch.isdigit())
    if digits:
        try:
            by_phone = client.select(
                "clientes",
                columns="celular,nome",
                filters=[("celular", "ilike", f"*{digits}*")],
                limit=limit,
            ) or []
            if isinstance(by_phone, list):
                for row in by_phone:
                    phone = (row.get("celular") or "").strip()
                    if phone and phone not in results:
                        results[phone] = {"phone": phone, "name": row.get("nome") or ""}
        except Exception:
            pass

    return list(results.values())[:limit]
