"""Backend SQLite com a mesma interface pública do SupabaseRestClient.

Objetivo: rodar o raylook em dev sem Postgres/Supabase. O cliente aqui:
  * Aceita filtros, order, columns, limit, offset no mesmo formato do PostgREST.
  * Traduz filtros (eq/neq/lt/lte/gt/gte/like/ilike/in/is) em WHERE SQL.
  * Resolve embeds simples estilo PostgREST: "alias:fk_col(child_col1,...)".
  * Gera UUID e timestamps quando ausentes (imita defaults do schema Postgres).
  * Expõe rpc() com um pequeno registro de funções reimplementadas em Python
    (get_customer_stats, next_pacote_sequence, close_package).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("raylook.sqlite")


Filter = Tuple[str, str, Any]


# ---------------------------------------------------------------------------
# Constantes de tabelas
# ---------------------------------------------------------------------------

# Tabelas cujo PK é UUID gerado pela app.
_UUID_PK_TABLES = {
    "produtos",
    "clientes",
    "enquetes",
    "enquete_alternativas",
    "webhook_inbox",
    "votos",
    "votos_eventos",
    "pacotes",
    "pacote_clientes",
    "vendas",
    "pagamentos",
}

# Colunas que precisam ser JSON-encoded/decoded.
_JSON_COLUMNS = {
    "webhook_inbox": {"payload_json"},
    "votos_eventos": {"payload_json"},
    "pagamentos": {"payload_json"},
    "app_runtime_state": {"payload_json"},
}

# Tabelas → colunas timestamp que recebem default automático.
# Cada entry lista as colunas presentes naquela tabela; o cliente só preenche
# as que existem, pra não falhar em tabelas como `votos` (sem created_at).
_TIMESTAMP_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "produtos": ("created_at", "updated_at"),
    "clientes": ("created_at", "updated_at"),
    "enquetes": ("created_at", "updated_at"),
    "enquete_alternativas": (),
    "webhook_inbox": ("received_at",),
    "votos": ("voted_at", "updated_at"),
    "votos_eventos": ("occurred_at",),
    "pacotes": ("created_at", "updated_at"),
    "pacote_clientes": ("created_at", "updated_at"),
    "vendas": ("sold_at", "created_at", "updated_at"),
    "pagamentos": ("created_at", "updated_at"),
    "app_runtime_state": ("updated_at",),
}
# Colunas consideradas "created" (preenchidas apenas no insert).
_CREATED_LIKE = {"created_at", "voted_at", "sold_at", "received_at", "occurred_at"}

# Mapa FK → tabela referenciada. Usado pelos embeds.
_FK_TO_TABLE = {
    "produto_id": "produtos",
    "cliente_id": "clientes",
    "enquete_id": "enquetes",
    "alternativa_id": "enquete_alternativas",
    "pacote_id": "pacotes",
    "venda_id": "vendas",
    "voto_id": "votos",
    "pacote_cliente_id": "pacote_clientes",
}

# Colunas booleanas — SQLite guarda como 0/1, callers esperam bool.
_BOOL_COLUMNS = {
    "votos": {"synthetic"},
}


# ---------------------------------------------------------------------------
# Helpers de schema
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "deploy" / "sqlite" / "schema.sql"


def _default_db_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "./data")
    return os.path.join(data_dir, "raylook.db")


# ---------------------------------------------------------------------------
# Parsing de columns (estilo PostgREST)
# ---------------------------------------------------------------------------

def _split_top_level(spec: str) -> List[str]:
    """Divide por vírgula respeitando parênteses aninhados."""
    parts: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in spec:
        if ch == "(" :
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _parse_columns(spec: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Retorna (fields_simples, embeds).

    embeds = [
        {
            "alias": "produto",       # chave que aparecerá no dict de retorno
            "fk_column": "produto_id",  # usado pra resolver FK e tbm aparece no SELECT
            "table": "produtos",
            "child_fields": [ ... lista de strings ... ],
            "child_embeds": [ ... recursivo ... ],
        },
        ...
    ]
    """
    if not spec or spec.strip() == "*":
        return ["*"], []

    fields: List[str] = []
    embeds: List[Dict[str, Any]] = []
    for item in _split_top_level(spec):
        m = re.match(r"^(?:([\w]+):)?([\w]+)\s*(\((.*)\))?$", item, re.DOTALL)
        if not m:
            # Formato não reconhecido — ignorar pra não quebrar.
            continue
        alias, name, _grp, sub = m.groups()
        if sub is not None:
            # Embed: alias:fk_col(...) ou table(...)
            if alias is None:
                # Forma "tabela(...)" — name é a própria tabela, FK inferida pelo padrão.
                alias = name
                fk_column = f"{name.rstrip('s')}_id"  # best-effort
                table = name
            else:
                fk_column = name
                table = _FK_TO_TABLE.get(fk_column, name)
            child_fields, child_embeds = _parse_columns(sub)
            embeds.append(
                {
                    "alias": alias,
                    "fk_column": fk_column,
                    "table": table,
                    "child_fields": child_fields,
                    "child_embeds": child_embeds,
                }
            )
        else:
            # Campo simples — ignoramos alias (retornamos sempre com nome real).
            fields.append(name)
    # Garantir que o FK das embeds está presente em fields (precisamos dele).
    for emb in embeds:
        fk = emb["fk_column"]
        if fields and fk not in fields and "*" not in fields:
            fields.append(fk)
    return fields, embeds


# ---------------------------------------------------------------------------
# Tradução de filtros / order
# ---------------------------------------------------------------------------

def _translate_filter(field: str, op: str, value: Any) -> Tuple[str, List[Any]]:
    op_norm = op.lower()
    if op_norm == "eq":
        if value is None:
            return f"{field} IS NULL", []
        return f"{field} = ?", [value]
    if op_norm == "neq":
        return f"{field} != ?", [value]
    if op_norm == "lt":
        return f"{field} < ?", [value]
    if op_norm == "lte":
        return f"{field} <= ?", [value]
    if op_norm == "gt":
        return f"{field} > ?", [value]
    if op_norm == "gte":
        return f"{field} >= ?", [value]
    if op_norm == "like":
        return f"{field} LIKE ?", [value]
    if op_norm == "ilike":
        # SQLite: LIKE é case-insensitive pra ASCII por padrão.
        return f"{field} LIKE ?", [value]
    if op_norm == "is":
        v = str(value).strip().lower()
        if v in ("null", "none", ""):
            return f"{field} IS NULL", []
        if v == "true":
            return f"{field} = 1", []
        if v == "false":
            return f"{field} = 0", []
        return f"{field} = ?", [value]
    if op_norm == "in":
        if isinstance(value, (list, tuple, set)):
            values = list(value)
        elif isinstance(value, str):
            values = [v for v in value.split(",") if v]
        else:
            values = [value]
        if not values:
            return "0 = 1", []
        placeholders = ",".join(["?"] * len(values))
        return f"{field} IN ({placeholders})", values
    if op_norm == "not.is":
        v = str(value).strip().lower()
        if v in ("null", "none", ""):
            return f"{field} IS NOT NULL", []
        return f"{field} != ?", [value]
    # Fallback — não deveria chegar aqui.
    raise ValueError(f"Operador não suportado: {op}")


def _translate_order(order: str) -> str:
    """Converte "field.asc" / "field.desc.nullsfirst,field2" em ORDER BY SQL."""
    parts = []
    for item in order.split(","):
        item = item.strip()
        if not item:
            continue
        tokens = item.split(".")
        field = tokens[0]
        direction = "ASC"
        nulls = ""
        for tok in tokens[1:]:
            tl = tok.lower()
            if tl in ("asc", "desc"):
                direction = tl.upper()
            elif tl == "nullsfirst":
                nulls = " NULLS FIRST"
            elif tl == "nullslast":
                nulls = " NULLS LAST"
        parts.append(f"{field} {direction}{nulls}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Row factory / (de)serialização de JSON e bool
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row, table: Optional[str]) -> Dict[str, Any]:
    d = dict(row)
    if table:
        for col in _JSON_COLUMNS.get(table, ()):
            if col in d and isinstance(d[col], str):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        for col in _BOOL_COLUMNS.get(table, ()):
            if col in d and d[col] is not None:
                d[col] = bool(d[col])
    return d


def _prepare_payload(table: str, payload: Dict[str, Any], *, is_insert: bool) -> Dict[str, Any]:
    """Aplica defaults (uuid, timestamps) e serializa JSON/bool."""
    out = dict(payload)

    # Gera UUID pro PK se ausente no insert.
    if is_insert and table in _UUID_PK_TABLES and "id" not in out:
        out["id"] = str(uuid.uuid4())

    # Preenche timestamps existentes naquela tabela.
    ts_cols = _TIMESTAMP_COLUMNS.get(table, ())
    if ts_cols:
        now = _now_iso()
        for col in ts_cols:
            if is_insert:
                out.setdefault(col, now)
            elif col not in _CREATED_LIKE:
                out.setdefault(col, now)

    # Normaliza bool → 0/1.
    for col in _BOOL_COLUMNS.get(table, ()):
        if col in out and isinstance(out[col], bool):
            out[col] = 1 if out[col] else 0

    # Serializa JSON columns.
    for col in _JSON_COLUMNS.get(table, ()):
        if col in out and not isinstance(out[col], str):
            out[col] = json.dumps(out[col], ensure_ascii=False)

    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Cliente SQLite
# ---------------------------------------------------------------------------

class SQLiteRestClient:
    _init_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None, schema_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self.schema_path = schema_path or str(_SCHEMA_PATH)
        self._ensure_db()

    # -------- schema / conexão --------

    def _ensure_db(self) -> None:
        with self._init_lock:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            exists = os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0
            if not exists:
                logger.info("raylook: criando DB SQLite em %s", self.db_path)
                conn = sqlite3.connect(self.db_path)
                try:
                    with open(self.schema_path, encoding="utf-8") as f:
                        conn.executescript(f.read())
                    conn.commit()
                finally:
                    conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit via BEGIN explícito
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -------- API pública (compatível com SupabaseRestClient) --------

    @classmethod
    def from_settings(cls) -> "SQLiteRestClient":
        return cls()

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Sequence[Filter]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        single: bool = False,
    ) -> Dict[str, Any] | List[Dict[str, Any]] | None:
        fields, embeds = _parse_columns(columns)
        rows = self._select_rows(
            table,
            fields=fields,
            filters=filters,
            limit=limit,
            offset=offset,
            order=order,
        )
        self._resolve_embeds(rows, embeds)
        if single:
            return rows[0] if rows else None
        return rows

    def select_all(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Sequence[Filter]] = None,
        order: Optional[str] = None,
        page_size: int = 1000,  # ignorado: SQLite retorna tudo
    ) -> List[Dict[str, Any]]:
        result = self.select(table, columns=columns, filters=filters, order=order)
        if isinstance(result, list):
            return result
        return [result] if result else []

    def insert(
        self,
        table: str,
        payload: Dict[str, Any] | List[Dict[str, Any]],
        *,
        upsert: bool = False,
        on_conflict: Optional[str] = None,
        returning: str = "representation",
    ) -> List[Dict[str, Any]]:
        rows_in = payload if isinstance(payload, list) else [payload]
        inserted_ids: List[Any] = []
        with self._connect() as conn:
            for row in rows_in:
                prepared = _prepare_payload(table, row, is_insert=True)
                columns = list(prepared.keys())
                placeholders = ",".join(["?"] * len(columns))
                col_sql = ",".join(columns)
                if upsert and on_conflict:
                    conflict_cols = on_conflict
                    update_cols = [c for c in columns if c not in on_conflict.split(",")]
                    set_sql = ",".join([f"{c}=excluded.{c}" for c in update_cols])
                    sql = (
                        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
                        f"ON CONFLICT({conflict_cols}) DO UPDATE SET {set_sql}"
                    )
                else:
                    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
                conn.execute(sql, [prepared[c] for c in columns])
                inserted_ids.append(prepared.get("id") or prepared.get("key"))
        if returning != "representation":
            return []
        # Retornar as linhas inseridas.
        pk_col = "key" if table == "app_runtime_state" else "id"
        return self._select_rows(
            table,
            fields=["*"],
            filters=[(pk_col, "in", inserted_ids)],
        )

    def update(
        self,
        table: str,
        payload: Dict[str, Any],
        *,
        filters: Optional[Sequence[Filter]] = None,
        returning: str = "representation",
    ) -> List[Dict[str, Any]]:
        prepared = _prepare_payload(table, payload, is_insert=False)
        if not prepared:
            return []
        cols = list(prepared.keys())
        set_sql = ", ".join([f"{c} = ?" for c in cols])
        values = [prepared[c] for c in cols]
        where_sql, where_values = self._build_where(filters)
        sql = f"UPDATE {table} SET {set_sql}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        with self._connect() as conn:
            conn.execute(sql, values + where_values)
        if returning != "representation":
            return []
        return self._select_rows(table, fields=["*"], filters=filters)

    def delete(self, table: str, *, filters: Optional[Sequence[Filter]] = None) -> int:
        where_sql, where_values = self._build_where(filters)
        sql = f"DELETE FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        with self._connect() as conn:
            cur = conn.execute(sql, where_values)
            return cur.rowcount or 0

    def rpc(self, fn_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        fn = _RPC_REGISTRY.get(fn_name)
        if fn is None:
            raise RuntimeError(f"RPC não implementado no backend SQLite: {fn_name}")
        return fn(self, args or {})

    def upsert_one(
        self,
        table: str,
        payload: Dict[str, Any],
        *,
        on_conflict: str,
    ) -> Dict[str, Any]:
        rows = self.insert(table, payload, upsert=True, on_conflict=on_conflict)
        if not rows:
            raise RuntimeError(f"Empty upsert response for table {table}")
        return rows[0]

    @staticmethod
    def now_iso() -> str:
        return _now_iso()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        payload: Any = None,
        extra_headers: Optional[Dict[str, str]] = None,
        prefer: Optional[str] = None,
        accept_object: bool = False,
    ) -> "_FakeResponse":
        """Emula a interface de httpx.Response do SupabaseRestClient.

        Aceita paths PostgREST (/rest/v1/<table> ou /rest/v1/rpc/<fn>) e traduz
        params/headers pros métodos select/rpc do próprio cliente SQLite. Usado
        pelos módulos de métricas que montam URLs PostgREST na mão.
        """
        method_up = method.upper()
        # Normaliza path: remove prefixo e query string (alguns callers mandam query direto no path).
        raw_path = path
        query_from_path: List[Tuple[str, str]] = []
        if "?" in raw_path:
            raw_path, qs = raw_path.split("?", 1)
            from urllib.parse import parse_qsl
            query_from_path = parse_qsl(qs, keep_blank_values=True)
        if raw_path.startswith("/rest/v1/"):
            rest_tail = raw_path[len("/rest/v1/"):]
        elif raw_path.startswith("/"):
            rest_tail = raw_path.lstrip("/")
        else:
            rest_tail = raw_path

        # ---- RPC ----
        if rest_tail.startswith("rpc/"):
            fn_name = rest_tail.split("/", 1)[1]
            args = payload if isinstance(payload, dict) else {}
            try:
                result = self.rpc(fn_name, args)
                return _FakeResponse(200, result)
            except Exception as exc:
                return _FakeResponse(500, {"error": str(exc)})

        # ---- Tabela ----
        table = rest_tail.split("/", 1)[0]

        # Normaliza params em lista de tuplas (pode vir dict, list-of-tuples ou None).
        param_items: List[Tuple[str, str]] = list(query_from_path)
        if params:
            if isinstance(params, dict):
                param_items.extend((k, str(v)) for k, v in params.items())
            else:
                param_items.extend((str(k), str(v)) for k, v in params)

        columns = "*"
        order = None
        limit = None
        offset = None
        on_conflict = None
        filters: List[Filter] = []
        for key, raw_value in param_items:
            if key == "select":
                columns = raw_value
            elif key == "order":
                order = raw_value
            elif key == "limit":
                try:
                    limit = int(raw_value)
                except (TypeError, ValueError):
                    limit = None
            elif key == "offset":
                try:
                    offset = int(raw_value)
                except (TypeError, ValueError):
                    offset = None
            elif key == "on_conflict":
                on_conflict = raw_value
            else:
                # campo=op.value  ou  campo=op.(v1,v2)
                op, value = _parse_pgrst_value(raw_value)
                filters.append((key, op, value))

        # Range header → limit/offset.
        if extra_headers:
            range_header = extra_headers.get("Range") or extra_headers.get("range")
            if range_header and "-" in range_header:
                try:
                    start_s, end_s = range_header.split("-", 1)
                    start_i = int(start_s)
                    end_i = int(end_s)
                    offset = start_i
                    limit = max(end_i - start_i + 1, 0)
                except ValueError:
                    pass

        # count=exact? → computar total
        wants_count = False
        if prefer and "count=exact" in prefer:
            wants_count = True
        if extra_headers:
            pref = extra_headers.get("Prefer") or extra_headers.get("prefer") or ""
            if "count=exact" in pref:
                wants_count = True

        if method_up == "GET":
            rows = self.select(
                table,
                columns=columns,
                filters=filters or None,
                limit=limit,
                offset=offset,
                order=order,
                single=accept_object,
            )
            headers: Dict[str, str] = {}
            if wants_count:
                total_rows = self._select_rows(table, fields=["id"], filters=filters or None)
                total = len(total_rows)
                start_i = offset or 0
                end_i = start_i + (len(rows) if isinstance(rows, list) else 1 if rows else 0) - 1
                if end_i < start_i:
                    end_i = start_i
                headers["content-range"] = f"{start_i}-{end_i}/{total}"
            return _FakeResponse(200, rows, headers=headers)

        if method_up == "POST":
            # PostgREST POST /<table> é insert. Prefer=resolution=merge-duplicates → upsert.
            is_upsert = False
            if prefer and "resolution=merge-duplicates" in prefer:
                is_upsert = True
            if extra_headers:
                pref = extra_headers.get("Prefer") or extra_headers.get("prefer") or ""
                if "resolution=merge-duplicates" in pref:
                    is_upsert = True
            rows = self.insert(table, payload or {}, upsert=is_upsert, on_conflict=on_conflict)
            return _FakeResponse(201, rows)

        if method_up == "PATCH":
            rows = self.update(table, payload or {}, filters=filters or None)
            return _FakeResponse(200, rows)

        if method_up == "DELETE":
            self.delete(table, filters=filters or None)
            return _FakeResponse(204, None)

        return _FakeResponse(405, {"error": f"Method {method_up} not supported"})

    # -------- helpers internos --------

    def _build_where(
        self, filters: Optional[Sequence[Filter]]
    ) -> Tuple[str, List[Any]]:
        if not filters:
            return "", []
        clauses: List[str] = []
        values: List[Any] = []
        for field, op, value in filters:
            clause, vals = _translate_filter(field, op, value)
            clauses.append(clause)
            values.extend(vals)
        return " AND ".join(clauses), values

    def _select_rows(
        self,
        table: str,
        *,
        fields: List[str],
        filters: Optional[Sequence[Filter]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        col_sql = "*" if "*" in fields or not fields else ",".join(fields)
        where_sql, where_values = self._build_where(filters)
        sql = f"SELECT {col_sql} FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        if order:
            sql += f" ORDER BY {_translate_order(order)}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"
        with self._connect() as conn:
            try:
                cur = conn.execute(sql, where_values)
            except sqlite3.OperationalError as exc:
                # Query com colunas inválidas: fallback para * pra não quebrar o caller.
                logger.warning("SQLite: %s — retry com SELECT * (query=%s)", exc, sql)
                fallback = f"SELECT * FROM {table}"
                if where_sql:
                    fallback += f" WHERE {where_sql}"
                if order:
                    fallback += f" ORDER BY {_translate_order(order)}"
                if limit is not None:
                    fallback += f" LIMIT {int(limit)}"
                if offset is not None:
                    fallback += f" OFFSET {int(offset)}"
                cur = conn.execute(fallback, where_values)
            rows = cur.fetchall()
        return [_row_to_dict(r, table) for r in rows]

    def _resolve_embeds(
        self, rows: List[Dict[str, Any]], embeds: List[Dict[str, Any]]
    ) -> None:
        if not rows or not embeds:
            return
        for emb in embeds:
            alias = emb["alias"]
            fk_column = emb["fk_column"]
            child_table = emb["table"]
            child_fields = emb["child_fields"]
            child_embeds = emb["child_embeds"]
            # Coletar ids FK distintos.
            fk_values = {r.get(fk_column) for r in rows if r.get(fk_column) is not None}
            if not fk_values:
                for r in rows:
                    r[alias] = None
                continue
            # Garantir "id" nos child_fields pra conseguir mapear child_rows[id] → row.
            effective_child_fields = child_fields or ["*"]
            if effective_child_fields != ["*"] and "id" not in effective_child_fields:
                effective_child_fields = effective_child_fields + ["id"]
            child_rows = self._select_rows(
                child_table,
                fields=effective_child_fields,
                filters=[("id", "in", list(fk_values))],
            )
            self._resolve_embeds(child_rows, child_embeds)
            by_id = {r["id"]: r for r in child_rows if "id" in r}
            for r in rows:
                fk_val = r.get(fk_column)
                r[alias] = by_id.get(fk_val)


# ---------------------------------------------------------------------------
# Registro de RPCs reimplementadas em Python
# ---------------------------------------------------------------------------

def _rpc_next_pacote_sequence(client: SQLiteRestClient, args: Dict[str, Any]) -> int:
    enquete_id = args.get("p_enquete_id") or args.get("enquete_id")
    with client._connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM pacotes "
            "WHERE enquete_id = ? AND sequence_no > 0",
            [enquete_id],
        ).fetchone()
    return int(row[0]) if row else 1


def _rpc_get_customer_stats(client: SQLiteRestClient, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    sql = """
    WITH pecas AS (
        SELECT pc.cliente_id, SUM(pc.qty) AS qty
        FROM pacote_clientes pc
        JOIN pacotes p ON p.id = pc.pacote_id
        WHERE p.status IN ('closed', 'approved')
        GROUP BY pc.cliente_id
    ),
    debitos AS (
        SELECT v.cliente_id, SUM(v.total_amount) AS total_debt
        FROM vendas v
        JOIN pagamentos pg ON pg.venda_id = v.id
        WHERE pg.status IN ('created', 'sent')
        GROUP BY v.cliente_id
    ),
    pagos AS (
        SELECT v.cliente_id, SUM(v.total_amount) AS total_paid
        FROM vendas v
        JOIN pagamentos pg ON pg.venda_id = v.id
        WHERE pg.status = 'paid'
        GROUP BY v.cliente_id
    )
    SELECT
        c.id AS cliente_id,
        c.celular,
        c.nome,
        COALESCE(pecas.qty, 0) AS qty,
        COALESCE(debitos.total_debt, 0) AS total_debt,
        COALESCE(pagos.total_paid, 0) AS total_paid
    FROM clientes c
    LEFT JOIN pecas   ON pecas.cliente_id   = c.id
    LEFT JOIN debitos ON debitos.cliente_id = c.id
    LEFT JOIN pagos   ON pagos.cliente_id   = c.id
    ORDER BY COALESCE(pagos.total_paid, 0) DESC,
             COALESCE(pecas.qty, 0) DESC,
             c.nome ASC
    """
    with client._connect() as conn:
        cur = conn.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def _rpc_close_package(client: SQLiteRestClient, args: Dict[str, Any]) -> Dict[str, Any]:
    enquete_id = args["p_enquete_id"]
    produto_id = args["p_produto_id"]
    votes = args["p_votes"] or []
    opened_at = args.get("p_opened_at")
    closed_at = args.get("p_closed_at")
    cap_total = int(args.get("p_capacidade_total", 24))
    total_qty = int(args.get("p_total_qty", 24))

    if not votes:
        return {"status": "no_votes", "pacote_id": None}

    now = _now_iso()

    with client._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM pacotes "
                "WHERE enquete_id = ? AND sequence_no > 0",
                [enquete_id],
            ).fetchone()
            sequence_no = int(row[0]) if row else 1
            pacote_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO pacotes (
                    id, enquete_id, sequence_no, capacidade_total, total_qty,
                    participants_count, status, opened_at, closed_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?)
                """,
                [
                    pacote_id, enquete_id, sequence_no, cap_total, total_qty,
                    len(votes), opened_at, closed_at, now, now,
                ],
            )
            for v in votes:
                conn.execute(
                    """
                    INSERT INTO pacote_clientes (
                        id, pacote_id, cliente_id, voto_id, produto_id, qty,
                        unit_price, subtotal, commission_percent, commission_amount,
                        total_amount, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?)
                    ON CONFLICT(pacote_id, cliente_id) DO UPDATE SET
                        qty=excluded.qty,
                        voto_id=excluded.voto_id,
                        unit_price=excluded.unit_price,
                        subtotal=excluded.subtotal,
                        commission_percent=excluded.commission_percent,
                        commission_amount=excluded.commission_amount,
                        total_amount=excluded.total_amount,
                        updated_at=excluded.updated_at
                    """,
                    [
                        str(uuid.uuid4()), pacote_id, v["cliente_id"], v["vote_id"],
                        produto_id, v["qty"], v["unit_price"], v["subtotal"],
                        v["commission_percent"], v["commission_amount"], v["total_amount"],
                        now, now,
                    ],
                )
            vote_ids = [v["vote_id"] for v in votes]
            placeholders = ",".join(["?"] * len(vote_ids))
            conn.execute(
                f"UPDATE votos SET status='in', updated_at=? WHERE id IN ({placeholders})",
                [now] + vote_ids,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {
        "status": "ok",
        "pacote_id": pacote_id,
        "sequence_no": sequence_no,
        "participants_count": len(votes),
    }


_RPC_REGISTRY: Dict[str, Callable[[SQLiteRestClient, Dict[str, Any]], Any]] = {
    "next_pacote_sequence": _rpc_next_pacote_sequence,
    "get_customer_stats": _rpc_get_customer_stats,
    "close_package": _rpc_close_package,
}


# ---------------------------------------------------------------------------
# Suporte a PostgREST-style _request (usado pelo módulo metrics)
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Mínimo pra se passar por httpx.Response nos callers do SupabaseRestClient."""

    def __init__(
        self,
        status_code: int,
        body: Any,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: Dict[str, str] = headers or {}

    @property
    def text(self) -> str:
        if self._body is None:
            return ""
        if isinstance(self._body, str):
            return self._body
        return json.dumps(self._body, ensure_ascii=False, default=str)

    def json(self) -> Any:
        return self._body


def _parse_pgrst_value(raw: str) -> Tuple[str, Any]:
    """Converte "eq.X" / "in.(a,b)" / "is.null" em (op, value) pros filtros."""
    if raw is None:
        return "eq", None
    raw = str(raw)
    if not raw:
        return "eq", raw
    if "." not in raw:
        return "eq", raw
    op, rest = raw.split(".", 1)
    op_l = op.lower()
    # not.is.null / not.eq.X → simplificação: suporte apenas not.is
    if op_l == "not":
        if "." in rest:
            inner_op, inner_val = rest.split(".", 1)
            if inner_op.lower() == "is":
                return "not.is", inner_val
            # fallback: traduz "not" como "neq" pra outros ops
            return "neq", inner_val
        return "neq", rest
    if op_l == "in":
        val = rest
        if val.startswith("(") and val.endswith(")"):
            val = val[1:-1]
        values = [v.strip() for v in val.split(",") if v.strip()]
        return "in", values
    if op_l == "is":
        return "is", rest
    return op_l, rest
