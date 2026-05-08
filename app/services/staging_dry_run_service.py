from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings


PACKAGE_SECTIONS = ("open", "closed_today", "closed_week", "confirmed_today", "rejected_today")


def is_staging_dry_run() -> bool:
    return bool(settings.TEST_MODE and settings.STAGING_DRY_RUN)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clone_metrics(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cloned = deepcopy(data or {})
    votos = cloned.setdefault("votos", {})
    packages = votos.setdefault("packages", {})
    for section in PACKAGE_SECTIONS:
        packages.setdefault(section, [])
    cloned["generated_at"] = _now_iso()
    return cloned


def _iter_packages(packages: Dict[str, Any]) -> Iterable[tuple[str, Dict[str, Any]]]:
    for section in PACKAGE_SECTIONS:
        rows = packages.get(section) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield section, row


def _package_matches(row: Dict[str, Any], package_id: str, source_package_id: Optional[str] = None) -> bool:
    wanted = {str(package_id or "").strip()}
    if source_package_id:
        wanted.add(str(source_package_id).strip())
    return str(row.get("id") or "").strip() in wanted or str(row.get("source_package_id") or "").strip() in wanted


def simulate_confirm_package(
    data: Dict[str, Any],
    package_id: str,
    *,
    source_package_id: Optional[str] = None,
    tag: Optional[str] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    cloned = _clone_metrics(data)
    packages = cloned["votos"]["packages"]
    moved: Optional[Dict[str, Any]] = None

    for section in ("open", "closed_today", "closed_week", "rejected_today"):
        rows = packages.get(section) or []
        kept = []
        for row in rows:
            if moved is None and _package_matches(row, package_id, source_package_id):
                moved = deepcopy(row)
                continue
            kept.append(row)
        packages[section] = kept

    if moved is None:
        for _, row in _iter_packages(packages):
            if _package_matches(row, package_id, source_package_id):
                moved = deepcopy(row)
                break
    if moved is None:
        return cloned, None

    if tag is not None:
        moved["tag"] = tag
    moved["status"] = "approved"
    moved["confirmed_at"] = _now_iso()
    moved.setdefault("closed_at", moved["confirmed_at"])
    packages["confirmed_today"] = [moved] + [row for row in (packages.get("confirmed_today") or []) if not _package_matches(row, package_id, source_package_id)]
    return cloned, moved


def simulate_reject_package(
    data: Dict[str, Any],
    package_id: str,
    *,
    source_package_id: Optional[str] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    cloned = _clone_metrics(data)
    packages = cloned["votos"]["packages"]
    moved: Optional[Dict[str, Any]] = None

    for section in ("open", "closed_today", "closed_week", "confirmed_today"):
        rows = packages.get(section) or []
        kept = []
        for row in rows:
            if moved is None and _package_matches(row, package_id, source_package_id):
                moved = deepcopy(row)
                continue
            kept.append(row)
        packages[section] = kept

    if moved is None:
        return cloned, None

    moved["status"] = "cancelled"
    moved["rejected_at"] = _now_iso()
    packages["rejected_today"] = [moved] + (packages.get("rejected_today") or [])
    return cloned, moved


def simulate_tag_package(
    data: Dict[str, Any],
    package_id: str,
    *,
    tag: Optional[str],
) -> tuple[Dict[str, Any], bool]:
    cloned = _clone_metrics(data)
    packages = cloned["votos"]["packages"]
    for section, row in _iter_packages(packages):
        if _package_matches(row, package_id):
            row["tag"] = tag
            return cloned, True
    return cloned, False


def simulate_update_confirmed_package_votes(
    data: Dict[str, Any],
    package_id: str,
    *,
    votes: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], bool]:
    cloned = _clone_metrics(data)
    packages = cloned["votos"]["packages"]
    normalized_votes = [
        {
            "phone": str(v.get("phone") or ""),
            "name": str(v.get("name") or "Cliente"),
            "qty": int(v.get("qty") or 0),
        }
        for v in votes
    ]
    total_qty = sum(int(v.get("qty") or 0) for v in normalized_votes)
    for section, row in _iter_packages(packages):
        if _package_matches(row, package_id):
            row["votes"] = normalized_votes
            row["qty"] = total_qty
            row["updated_at"] = _now_iso()
            row["pdf_status"] = "queued"
            row["pdf_attempts"] = 0
            row["pdf_file_name"] = None
            return cloned, True
    return cloned, False


def simulate_manual_confirm_package(data: Dict[str, Any], package_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    cloned = _clone_metrics(data)
    packages = cloned["votos"]["packages"]
    moved = deepcopy(package_snapshot)
    moved["confirmed_at"] = _now_iso()
    moved["status"] = "approved"
    packages["confirmed_today"] = [moved] + (packages.get("confirmed_today") or [])
    return cloned


def build_customer_rows(customers: Dict[str, str], charges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats = defaultdict(lambda: {"qty": 0, "total_paid": 0.0})
    for charge in charges:
        phone = str(charge.get("customer_phone") or "")
        if not phone:
            continue
        stats[phone]["qty"] += int(charge.get("quantity") or 0)
        if charge.get("status") == "paid":
            stats[phone]["total_paid"] += float(charge.get("total_amount") or 0.0)

    rows = []
    for phone, name in customers.items():
        row = stats.get(phone, {"qty": 0, "total_paid": 0.0})
        rows.append(
            {
                "phone": phone,
                "name": name,
                "qty": row["qty"],
                "total_paid": round(row["total_paid"], 2),
            }
        )
    return rows


def simulate_customer_rows(
    customers: Dict[str, str],
    charges: List[Dict[str, Any]],
    *,
    phone: str,
    name: str,
) -> List[Dict[str, Any]]:
    cloned_customers = dict(customers)
    cloned_customers[str(phone)] = str(name)
    return build_customer_rows(cloned_customers, charges)


def simulate_delete_charge(charges: List[Dict[str, Any]], charge_id: str) -> List[Dict[str, Any]]:
    return [deepcopy(charge) for charge in charges if str(charge.get("id")) != str(charge_id)]
