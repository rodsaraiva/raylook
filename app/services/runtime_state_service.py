from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled


DASHBOARD_METRICS_STATE_KEY = "dashboard_metrics"
RECENT_IMAGES_STATE_KEY = "recent_images"
FINANCE_CHARGES_STATE_KEY = "finance_charges"
FINANCE_STATS_STATE_KEY = "finance_stats"
CUSTOMER_ROWS_STATE_KEY = "customer_rows"


def runtime_state_enabled() -> bool:
    return supabase_domain_enabled()


def load_runtime_state(key: str) -> Optional[Dict[str, Any]]:
    if not runtime_state_enabled():
        return None
    row = SupabaseRestClient.from_settings().select(
        "app_runtime_state",
        columns="key,payload_json,updated_at",
        filters=[("key", "eq", key)],
        single=True,
    )
    if not row:
        return None
    payload = row.get("payload_json")
    return payload if isinstance(payload, dict) else None


def load_runtime_state_metadata(keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    if not runtime_state_enabled():
        return {}

    normalized_keys = [str(key).strip() for key in keys if str(key).strip()]
    if not normalized_keys:
        return {}

    rows = SupabaseRestClient.from_settings().select_all(
        "app_runtime_state",
        columns="key,updated_at",
        filters=[("key", "in", sorted(set(normalized_keys)))],
    )
    metadata: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        metadata[key] = {
            "updated_at": row.get("updated_at"),
        }
    return metadata


def save_runtime_state(key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not runtime_state_enabled():
        return payload
    row = SupabaseRestClient.from_settings().upsert_one(
        "app_runtime_state",
        {"key": key, "payload_json": payload, "updated_at": SupabaseRestClient.now_iso()},
        on_conflict="key",
    )
    stored = row.get("payload_json")
    return stored if isinstance(stored, dict) else payload


def delete_runtime_state(key: str) -> None:
    if not runtime_state_enabled():
        return
    SupabaseRestClient.from_settings().delete(
        "app_runtime_state",
        filters=[("key", "eq", key)],
    )
