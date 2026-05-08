from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from app.config import settings

logger = logging.getLogger("raylook.supabase")


Filter = Tuple[str, str, Any]


def _required(value: str | None, env_name: str) -> str:
    if value and value.strip():
        return value.strip()
    raise RuntimeError(f"{env_name} is not configured")


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def supabase_domain_enabled() -> bool:
    # Em dev, DATA_BACKEND=sqlite habilita o "domínio Supabase" apontando para o
    # cliente SQLite local, permitindo que o mesmo código de negócio rode sem
    # precisar de credenciais reais.
    if getattr(settings, "DATA_BACKEND", "supabase") == "sqlite":
        return True
    return bool(
        settings.SUPABASE_DOMAIN_ENABLED
        and settings.SUPABASE_URL
        and settings.SUPABASE_SERVICE_ROLE_KEY
    )


def fetch_project_status() -> Dict[str, Any]:
    if (
        settings.SUPABASE_ACCESS_TOKEN
        and settings.SUPABASE_PROJECT_REF
        and "supabase.co" in str(settings.SUPABASE_URL or "")
    ):
        access_token = _required(settings.SUPABASE_ACCESS_TOKEN, "SUPABASE_ACCESS_TOKEN")
        project_ref = _required(settings.SUPABASE_PROJECT_REF, "SUPABASE_PROJECT_REF")
        url = f"https://api.supabase.com/v1/projects/{project_ref}"
        headers = {"Authorization": f"Bearer {access_token}", "apikey": access_token}

        timeout = httpx.Timeout(10.0, connect=5.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)

        if response.status_code == 401:
            raise RuntimeError("Supabase access token is invalid or expired")
        if response.status_code == 404:
            raise RuntimeError("Supabase project not found for the configured ref")
        if response.status_code != 200:
            raise RuntimeError(f"Supabase API error: {response.status_code}")

        data = response.json()
        return {
            "id": data.get("id"),
            "ref": data.get("ref"),
            "name": data.get("name"),
            "region": data.get("region"),
            "status": data.get("status"),
            "db_host": (data.get("database") or {}).get("host"),
            "backend": "supabase",
        }

    client = SupabaseRestClient.from_settings()
    probe = client.select("app_runtime_state", columns="key", limit=1)
    rows = probe if isinstance(probe, list) else ([probe] if probe else [])
    return {
        "status": "ok",
        "backend": "postgrest",
        "base_url": settings.SUPABASE_URL,
        "schema": settings.SUPABASE_SCHEMA,
        "sample_rows": len(rows),
    }


class SupabaseRestClient:
    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        schema: str = "public",
        rest_path: str = "/rest/v1",
        timeout: float = 20.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.schema = schema
        self.rest_path = str(rest_path or "").strip()
        self.timeout = timeout

    @classmethod
    def from_settings(cls):  # pode retornar SQLiteRestClient quando DATA_BACKEND=sqlite
        # DATA_BACKEND escolhe banco. RAYLOOK_SANDBOX é só pra integrações
        # (Asaas/Resend/etc) — não força mais SQLite. Assim prod pode usar
        # Postgres dedicado via PostgREST mantendo integrações em stub enquanto
        # cada uma é configurada.
        backend = (getattr(settings, "DATA_BACKEND", "sqlite") or "sqlite").strip().lower()
        if backend == "sqlite":
            from app.services.sqlite_service import SQLiteRestClient
            return SQLiteRestClient.from_settings()
        url = _required(settings.SUPABASE_URL, "SUPABASE_URL")
        key = _required(settings.SUPABASE_SERVICE_ROLE_KEY, "SUPABASE_SERVICE_ROLE_KEY")
        return cls(
            url=url,
            service_role_key=key,
            schema=settings.SUPABASE_SCHEMA,
            rest_path=getattr(settings, "SUPABASE_REST_PATH", "/rest/v1"),
        )

    def _headers(self, *, accept_object: bool = False, prefer: str | None = None) -> Dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
        }
        if accept_object:
            headers["Accept"] = "application/vnd.pgrst.object+json"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    @staticmethod
    def _filter_value(op: str, value: Any) -> str:
        if op == "in":
            if isinstance(value, (list, tuple, set)):
                return f"in.({','.join(str(v) for v in value)})"
            return f"in.({value})"
        if op == "is":
            return f"is.{str(value).lower()}"
        return f"{op}.{value}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,  # dict ou list of tuples — httpx aceita ambos
        payload: Any = None,
        accept_object: bool = False,
        prefer: str | None = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        if path.startswith("/rest/v1"):
            suffix = path[len("/rest/v1"):]
            normalized_path = f"{self.rest_path.rstrip('/')}{suffix}" if self.rest_path else suffix or "/"
        else:
            normalized_path = f"{self.rest_path.rstrip('/')}/{path.lstrip('/')}" if self.rest_path else path
        url = f"{self.url}{normalized_path}"
        headers = self._headers(accept_object=accept_object, prefer=prefer)
        if extra_headers:
            headers.update(extra_headers)
        timeout = httpx.Timeout(self.timeout, connect=5.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, headers=headers, params=params, json=payload)

        if response.status_code == 406 and accept_object:
            try:
                error = response.json()
            except Exception:
                error = {}
            if (
                error.get("code") == "PGRST116"
                and "0 rows" in str(error.get("details") or error.get("message") or "")
            ):
                return httpx.Response(204, request=response.request)

        if response.status_code >= 400:
            message = response.text[:1000]
            raise RuntimeError(f"Supabase REST error {response.status_code} for {path}: {message}")
        return response

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
        params: Dict[str, Any] = {"select": columns}
        for field, op, value in filters or []:
            params[field] = self._filter_value(op, value)
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        if order:
            params["order"] = order

        response = self._request(
            "GET",
            f"/rest/v1/{table}",
            params=params,
            accept_object=single,
        )

        if response.status_code == 204:
            return None
        if not response.text:
            return None
        if single:
            return response.json()
        return response.json()

    def select_all(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Sequence[Filter]] = None,
        order: Optional[str] = None,
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        # Usar lista de tuplas pra suportar múltiplos filtros no mesmo campo
        # (ex: approved_at gte X AND approved_at lte Y).
        # httpx aceita params como list of tuples sem fazer dedup de chaves.
        params_list: List[tuple] = [("select", columns)]
        for field, op, value in filters or []:
            params_list.append((field, self._filter_value(op, value)))
        if order:
            params_list.append(("order", order))

        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            response = self._request(
                "GET",
                f"/rest/v1/{table}",
                params=params_list,
                extra_headers={"Range": f"{offset}-{offset + page_size - 1}"},
            )
            batch = response.json() if response.text else []
            if not isinstance(batch, list):
                batch = []
            rows.extend(batch)
            if len(batch) < page_size:
                return rows
            offset += page_size

    def insert(
        self,
        table: str,
        payload: Dict[str, Any] | List[Dict[str, Any]],
        *,
        upsert: bool = False,
        on_conflict: str | None = None,
        returning: str = "representation",
    ) -> List[Dict[str, Any]]:
        prefer = f"return={returning}"
        if upsert:
            prefer = f"resolution=merge-duplicates,{prefer}"
        params: Dict[str, Any] = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        response = self._request(
            "POST",
            f"/rest/v1/{table}",
            params=params,
            payload=payload,
            prefer=prefer,
        )
        if not response.text:
            return []
        data = response.json()
        if isinstance(data, list):
            return data
        return [data]

    def update(
        self,
        table: str,
        payload: Dict[str, Any],
        *,
        filters: Optional[Sequence[Filter]] = None,
        returning: str = "representation",
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        for field, op, value in filters or []:
            params[field] = self._filter_value(op, value)
        response = self._request(
            "PATCH",
            f"/rest/v1/{table}",
            params=params,
            payload=payload,
            prefer=f"return={returning}",
        )
        if not response.text:
            return []
        data = response.json()
        if isinstance(data, list):
            return data
        return [data]

    def delete(self, table: str, *, filters: Optional[Sequence[Filter]] = None) -> int:
        params: Dict[str, Any] = {}
        for field, op, value in filters or []:
            params[field] = self._filter_value(op, value)
        response = self._request(
            "DELETE",
            f"/rest/v1/{table}",
            params=params,
            prefer="return=minimal",
        )
        return response.status_code

    def rpc(self, fn_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        response = self._request("POST", f"/rest/v1/rpc/{fn_name}", payload=args or {})
        if not response.text:
            return None
        return response.json()

    def upsert_one(
        self,
        table: str,
        payload: Dict[str, Any],
        *,
        on_conflict: str,
    ) -> Dict[str, Any]:
        rows = self.insert(table, payload, upsert=True, on_conflict=on_conflict, returning="representation")
        if not rows:
            raise RuntimeError(f"Empty upsert response for table {table}")
        return rows[0]

    @staticmethod
    def now_iso() -> str:
        return _to_iso(datetime.now(timezone.utc)) or datetime.now(timezone.utc).isoformat()
