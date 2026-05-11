"""FakeSupabaseClient — drop-in para SupabaseRestClient em testes.

Implementa a interface mínima usada pelo dashboard router:
- select(table, *, columns, filters, limit, offset, order, single)
- select_all(table, *, columns, filters, order, page_size)
- insert(table, values) -> dict
- update(table, values, *, filters) -> int
- delete(table, *, filters) -> int
- now_iso() -> str (timestamp congelado)

Filtros são tuplas (field, op, value), mesma interface do PostgREST wrapper.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

Filter = Tuple[str, str, Any]
FROZEN_NOW = "2026-05-11T15:30:00+00:00"


def apply_filters(rows: List[Dict[str, Any]],
                  filters: Optional[Sequence[Filter]]) -> List[Dict[str, Any]]:
    if not filters:
        return list(rows)
    out: List[Dict[str, Any]] = []
    for row in rows:
        ok = True
        for field, op, value in filters:
            v = row.get(field)
            if op == "eq" and v != value:
                ok = False
            elif op == "neq" and v == value:
                ok = False
            elif op == "gte" and (v is None or v < value):
                ok = False
            elif op == "lte" and (v is None or v > value):
                ok = False
            elif op == "gt" and (v is None or v <= value):
                ok = False
            elif op == "lt" and (v is None or v >= value):
                ok = False
            elif op == "in":
                if v not in (value if isinstance(value, (list, tuple, set)) else [value]):
                    ok = False
            if not ok:
                break
        if ok:
            out.append(row)
    return out


class FakeSupabaseClient:
    def __init__(self, tables: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self.tables: Dict[str, List[Dict[str, Any]]] = tables or {}
        self._frozen_now = FROZEN_NOW

    def set_now(self, iso: str) -> None:
        self._frozen_now = iso

    def now_iso(self) -> str:
        return self._frozen_now

    def select(self, table, *, columns="*", filters=None, limit=None,
               offset=None, order=None, single=False):
        rows = apply_filters(self.tables.get(table, []), filters)
        if order:
            field, _, direction = order.partition(".")
            rows = sorted(rows, key=lambda r: r.get(field) or "",
                          reverse=(direction == "desc"))
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        if single:
            return rows[0] if rows else None
        return rows

    def select_all(self, table, *, columns="*", filters=None, order=None, page_size=1000):
        return self.select(table, columns=columns, filters=filters, order=order)

    def insert(self, table, values):
        if "id" not in values:
            values = {**values, "id": str(uuid.uuid4())}
        self.tables.setdefault(table, []).append(values)
        return values

    def update(self, table, values, *, filters=None):
        rows = apply_filters(self.tables.get(table, []), filters)
        for r in rows:
            r.update(values)
        return len(rows)

    def delete(self, table, *, filters=None):
        rows = self.tables.get(table, [])
        to_remove = apply_filters(rows, filters)
        # Identidade por id quando existir, senão por igualdade.
        ids = {id(r) for r in to_remove}
        keep = [r for r in rows if id(r) not in ids]
        self.tables[table] = keep
        return len(rows) - len(keep)


def install_fake(monkeypatch, fake: FakeSupabaseClient) -> None:
    """Patch SupabaseRestClient.from_settings em todos os módulos que importam."""
    monkeypatch.setattr(
        "app.routers.dashboard.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake),
    )


def empty_tables() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "pacotes": [],
        "vendas": [],
        "pagamentos": [],
        "pacote_clientes": [],
        "enquetes": [],
        "produtos": [],
        "clientes": [],
        "votos": [],
    }
