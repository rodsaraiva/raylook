from __future__ import annotations

from copy import deepcopy

from app.services.whatsapp_domain_service import PackageService


class FakeClient:
    def __init__(self, tables):
        self.tables = {name: [deepcopy(row) for row in rows] for name, rows in tables.items()}
        self._counters = {"pacotes": 0, "pacote_clientes": 0}

    @staticmethod
    def _matches(row, filters):
        for field, op, value in filters or []:
            row_value = row.get(field)
            if op == "eq" and row_value != value:
                return False
            if op == "gt" and not (row_value > value):
                return False
            if op == "in" and row_value not in value:
                return False
        return True

    def select(self, table, *, columns="*", filters=None, limit=None, offset=None, order=None, single=False):
        rows = [deepcopy(row) for row in self.tables.get(table, []) if self._matches(row, filters)]
        if limit is not None:
            rows = rows[:limit]
        if single:
            return rows[0] if rows else None
        return rows

    def insert(self, table, payload, *, upsert=False, on_conflict=None, returning="representation"):
        rows = payload if isinstance(payload, list) else [payload]
        inserted = []
        for original in rows:
            row = deepcopy(original)
            if upsert and table == "pacotes" and on_conflict == "enquete_id,sequence_no":
                existing = next(
                    (
                        current
                        for current in self.tables.get(table, [])
                        if current.get("enquete_id") == row.get("enquete_id")
                        and current.get("sequence_no") == row.get("sequence_no")
                    ),
                    None,
                )
                if existing:
                    existing.update(row)
                    inserted.append(deepcopy(existing))
                    continue
            if "id" not in row:
                self._counters[table] = self._counters.get(table, 0) + 1
                row["id"] = f"{table}-{self._counters[table]}"
            self.tables.setdefault(table, []).append(row)
            inserted.append(deepcopy(row))
        return [] if returning == "minimal" else inserted

    def update(self, table, payload, *, filters=None, returning="representation"):
        updated = []
        for row in self.tables.get(table, []):
            if self._matches(row, filters):
                row.update(deepcopy(payload))
                updated.append(deepcopy(row))
        return [] if returning == "minimal" else updated

    def delete(self, table, *, filters=None):
        kept = []
        for row in self.tables.get(table, []):
            if not self._matches(row, filters):
                kept.append(row)
        self.tables[table] = kept
        return 204

    def rpc(self, fn_name, args=None):
        args = args or {}
        if fn_name == "next_pacote_sequence":
            enquete_id = args.get("p_enquete_id")
            sequence_nos = [
                int(row.get("sequence_no") or 0)
                for row in self.tables.get("pacotes", [])
                if row.get("enquete_id") == enquete_id and int(row.get("sequence_no") or 0) > 0
            ]
            return (max(sequence_nos) + 1) if sequence_nos else 1
        if fn_name == "close_package":
            # Simula a RPC transacional do F-001 dentro do FakeClient.
            # Replica o efeito: insere pacote + pacote_clientes + atualiza votos.
            enquete_id = args.get("p_enquete_id")
            produto_id = args.get("p_produto_id")
            votes = args.get("p_votes") or []
            if not votes:
                return {"status": "no_votes", "pacote_id": None}
            existing = [
                int(row.get("sequence_no") or 0)
                for row in self.tables.get("pacotes", [])
                if row.get("enquete_id") == enquete_id and int(row.get("sequence_no") or 0) > 0
            ]
            seq = (max(existing) + 1) if existing else 1
            import uuid as _uuid
            pacote_id = str(_uuid.uuid4())
            self.tables.setdefault("pacotes", []).append(
                {
                    "id": pacote_id,
                    "enquete_id": enquete_id,
                    "sequence_no": seq,
                    "capacidade_total": args.get("p_capacidade_total", 24),
                    "total_qty": args.get("p_total_qty", 24),
                    "participants_count": len(votes),
                    "status": "closed",
                    "opened_at": args.get("p_opened_at"),
                    "closed_at": args.get("p_closed_at"),
                }
            )
            for v in votes:
                self.tables.setdefault("pacote_clientes", []).append(
                    {
                        "id": str(_uuid.uuid4()),
                        "pacote_id": pacote_id,
                        "cliente_id": v["cliente_id"],
                        "voto_id": v["vote_id"],
                        "produto_id": produto_id,
                        "qty": v["qty"],
                        "unit_price": v["unit_price"],
                        "subtotal": v["subtotal"],
                        "commission_percent": v["commission_percent"],
                        "commission_amount": v["commission_amount"],
                        "total_amount": v["total_amount"],
                        "status": "closed",
                    }
                )
            vote_ids = {v["vote_id"] for v in votes}
            for row in self.tables.get("votos", []):
                if row.get("id") in vote_ids:
                    row["status"] = "in"
            return {
                "status": "ok",
                "pacote_id": pacote_id,
                "sequence_no": seq,
                "participants_count": len(votes),
            }
        raise AssertionError(f"Unexpected RPC call: {fn_name}")


def test_rebuild_for_poll_reopens_when_vote_change_breaks_closed_package():
    client = FakeClient(
        {
            "votos": [
                {"id": "vote-1", "enquete_id": "poll-1", "cliente_id": "cli-1", "alternativa_id": "alt-12", "qty": 12, "voted_at": "2026-04-01T15:00:00+00:00"},
                {"id": "vote-2", "enquete_id": "poll-1", "cliente_id": "cli-2", "alternativa_id": "alt-9", "qty": 9, "voted_at": "2026-04-01T15:05:00+00:00"},
            ],
            "enquetes": [
                {"id": "poll-1", "produto_id": "prod-1", "produtos": {"id": "prod-1", "valor_unitario": 10.0}},
            ],
            "pacotes": [
                {"id": "pkg-stale", "enquete_id": "poll-1", "sequence_no": 1, "status": "closed", "total_qty": 24},
            ],
            "pacote_clientes": [
                {"id": "pkg-cli-1", "pacote_id": "pkg-stale", "cliente_id": "cli-1", "voto_id": "vote-1", "qty": 12},
                {"id": "pkg-cli-2", "pacote_id": "pkg-stale", "cliente_id": "cli-2", "voto_id": "vote-2", "qty": 12},
            ],
        }
    )

    result = PackageService(client).rebuild_for_poll("poll-1")

    assert result == {"closed_count": 0, "open_qty": 21}
    assert client.tables["pacote_clientes"] == []
    assert len(client.tables["pacotes"]) == 1
    rebuilt = client.tables["pacotes"][0]
    assert rebuilt["status"] == "open"
    assert rebuilt["sequence_no"] == 0
    assert rebuilt["total_qty"] == 21


def test_rebuild_for_poll_preserves_approved_package_votes_outside_new_assembly():
    client = FakeClient(
        {
            "votos": [
                {"id": "vote-approved", "enquete_id": "poll-1", "cliente_id": "cli-approved", "alternativa_id": "alt-12", "qty": 12, "voted_at": "2026-04-01T15:00:00+00:00"},
                {"id": "vote-2", "enquete_id": "poll-1", "cliente_id": "cli-2", "alternativa_id": "alt-12", "qty": 12, "voted_at": "2026-04-01T15:05:00+00:00"},
                {"id": "vote-3", "enquete_id": "poll-1", "cliente_id": "cli-3", "alternativa_id": "alt-12", "qty": 12, "voted_at": "2026-04-01T15:10:00+00:00"},
            ],
            "enquetes": [
                {"id": "poll-1", "produto_id": "prod-1", "produtos": {"id": "prod-1", "valor_unitario": 10.0}},
            ],
            "pacotes": [
                {"id": "pkg-approved", "enquete_id": "poll-1", "sequence_no": 1, "status": "approved", "total_qty": 24},
                {"id": "pkg-open-stale", "enquete_id": "poll-1", "sequence_no": 0, "status": "open", "total_qty": 12},
            ],
            "pacote_clientes": [
                {"id": "pc-approved", "pacote_id": "pkg-approved", "cliente_id": "cli-approved", "voto_id": "vote-approved", "qty": 12},
                {"id": "pc-open", "pacote_id": "pkg-open-stale", "cliente_id": "cli-2", "voto_id": "vote-2", "qty": 12},
            ],
        }
    )

    result = PackageService(client).rebuild_for_poll("poll-1")

    assert result == {"closed_count": 1, "open_qty": 0}
    remaining_packages = {row["id"]: row for row in client.tables["pacotes"]}
    assert "pkg-approved" in remaining_packages
    assert remaining_packages["pkg-approved"]["status"] == "approved"
    rebuilt_packages = [row for row in client.tables["pacotes"] if row["status"] == "closed"]
    assert len(rebuilt_packages) == 1
    rebuilt_package_id = rebuilt_packages[0]["id"]
    rebuilt_vote_ids = {
        row["voto_id"]
        for row in client.tables["pacote_clientes"]
        if row.get("pacote_id") == rebuilt_package_id
    }
    assert rebuilt_vote_ids == {"vote-2", "vote-3"}
