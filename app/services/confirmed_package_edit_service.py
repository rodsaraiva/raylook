from typing import Any, Dict, Iterable, List, Optional, Tuple

PAID_STATUSES = {"paid", "received", "confirmed", "completed"}


def _clean_phone(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def normalize_votes_payload(votes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for vote in votes or []:
        phone = _clean_phone(vote.get("phone"))
        if not phone:
            continue
        try:
            qty = int(vote.get("qty", 0))
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        normalized.append(
            {
                "phone": phone,
                "name": str(vote.get("name") or "Cliente").strip() or "Cliente",
                "qty": qty,
            }
        )
    return normalized


def validate_package_total(votes: Iterable[Dict[str, Any]]) -> Optional[int]:
    total = 0
    for vote in votes or []:
        try:
            total += int(vote.get("qty", 0))
        except Exception:
            continue
    if total != 24:
        return None
    return total


def split_added_removed_votes(
    current_votes: Iterable[Dict[str, Any]],
    new_votes: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    current_list = normalize_votes_payload(current_votes or [])
    new_list = normalize_votes_payload(new_votes or [])

    current_by_phone = {_clean_phone(v.get("phone")): v for v in current_list}
    new_by_phone = {_clean_phone(v.get("phone")): v for v in new_list}

    added = [vote for phone, vote in new_by_phone.items() if phone not in current_by_phone]
    removed = [vote for phone, vote in current_by_phone.items() if phone not in new_by_phone]
    return added, removed


def diff_votes_by_phone(
    current_votes: Iterable[Dict[str, Any]],
    new_votes: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Retorna dict {added, removed, changed, unchanged} com votos por mudança.

    `changed` = telefone presente em ambos mas com qty diferente.
    """
    current_list = normalize_votes_payload(current_votes or [])
    new_list = normalize_votes_payload(new_votes or [])

    current_by_phone = {_clean_phone(v["phone"]): v for v in current_list}
    new_by_phone = {_clean_phone(v["phone"]): v for v in new_list}

    added = [v for p, v in new_by_phone.items() if p not in current_by_phone]
    removed = [v for p, v in current_by_phone.items() if p not in new_by_phone]
    changed: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    for p, new_v in new_by_phone.items():
        if p in current_by_phone:
            old_v = current_by_phone[p]
            if int(new_v["qty"]) != int(old_v["qty"]):
                changed.append({"phone": p, "old_qty": old_v["qty"], "new_qty": new_v["qty"], "name": new_v.get("name")})
            else:
                unchanged.append(new_v)
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def build_edit_columns(
    package: Dict[str, Any],
    active_votes: Iterable[Dict[str, Any]],
    confirmed_packages: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    selected = normalize_votes_payload(package.get("votes") or [])
    selected_phones = {_clean_phone(v.get("phone")) for v in selected}

    busy_phones = set()
    for pkg in confirmed_packages or []:
        if pkg.get("id") == package.get("id"):
            continue
        for vote in pkg.get("votes") or []:
            phone = _clean_phone(vote.get("phone"))
            if phone:
                busy_phones.add(phone)

    available: List[Dict[str, Any]] = []
    for vote in normalize_votes_payload(active_votes or []):
        phone = _clean_phone(vote.get("phone"))
        if not phone:
            continue
        if phone in selected_phones:
            continue
        if phone in busy_phones:
            continue
        available.append(vote)

    return available, selected
