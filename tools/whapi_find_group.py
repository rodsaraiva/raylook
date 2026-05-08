"""Lista grupos visíveis pelo canal WHAPI configurado e procura por nome.

Uso:
    WHAPI_TOKEN=<seu_token_raylook> python3 tools/whapi_find_group.py
    WHAPI_TOKEN=<seu_token_raylook> python3 tools/whapi_find_group.py "Divisão de Pacotes"

Sem argumento: lista todos os grupos onde o canal está.
Com argumento: filtra por substring no nome (case-insensitive, ignora acentos).
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from typing import Any, Dict, List

import httpx


def _norm(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn"
    ).lower().strip()


def list_groups(token: str, base_url: str = "https://gate.whapi.cloud") -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    groups: List[Dict[str, Any]] = []
    offset = 0
    page_size = 100
    while True:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                f"{base_url.rstrip('/')}/groups",
                headers=headers,
                params={"count": page_size, "offset": offset},
            )
        r.raise_for_status()
        data = r.json() or {}
        page = data.get("groups") or []
        if not isinstance(page, list) or not page:
            break
        groups.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return groups


def main() -> int:
    token = os.getenv("WHAPI_TOKEN", "").strip()
    if not token:
        print("ERRO: WHAPI_TOKEN não configurado. Exporte com:", file=sys.stderr)
        print("  export WHAPI_TOKEN=<seu_token_do_canal_raylook>", file=sys.stderr)
        return 2

    base_url = os.getenv("WHAPI_API_URL", "https://gate.whapi.cloud").strip()
    query = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        groups = list_groups(token, base_url)
    except httpx.HTTPStatusError as exc:
        print(f"ERRO HTTP {exc.response.status_code}: {exc.response.text[:300]}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if not groups:
        print("Nenhum grupo retornado. Verifique se o canal está conectado e dentro de algum grupo.")
        return 0

    if query:
        q = _norm(query)
        groups = [g for g in groups if q in _norm(str(g.get("name") or g.get("subject") or ""))]
        if not groups:
            print(f"Nenhum grupo bate com '{query}'.")
            return 0

    print(f"{len(groups)} grupo(s):\n")
    for g in groups:
        name = g.get("name") or g.get("subject") or "(sem nome)"
        chat_id = g.get("id") or g.get("chat_id") or "(sem id)"
        size = g.get("size") or len(g.get("participants") or [])
        print(f"  {chat_id}")
        print(f"    nome: {name}")
        print(f"    membros: {size}")
        print()

    if query and len(groups) == 1:
        chat_id = groups[0].get("id") or groups[0].get("chat_id")
        print(f"Sugestão pro .env:\n  AUTHORIZED_GROUP_1={chat_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
