"""Backfill polls + votos do grupo oficial via WHAPI API direto no SQLite local.

Não usa o endpoint HTTP /webhook/whatsapp — chama o WebhookIngestionService
diretamente. Sandbox lockout (DATA_BACKEND=sqlite) garante que tudo escreve
no SQLite local, nunca no Postgres.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List

import dotenv
import requests

dotenv.load_dotenv()

# Garantir que o ingest path está habilitado pelo backend SQLite
os.environ.setdefault("DATA_BACKEND", "sqlite")
os.environ.setdefault("RAYLOOK_SANDBOX", "true")

WHAPI_TOKEN = os.environ["WHAPI_TOKEN"]
WHAPI_URL = os.environ.get("WHAPI_API_URL", "https://gate.whapi.cloud").rstrip("/")
GROUP_ID = os.environ["OFFICIAL_GROUP_CHAT_ID"]


def fetch_all_messages(group_id: str) -> List[Dict[str, Any]]:
    """Pagina /messages/list até esgotar."""
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    out: List[Dict[str, Any]] = []
    offset = 0
    page_size = 200
    while True:
        r = requests.get(
            f"{WHAPI_URL}/messages/list/{group_id}",
            params={"count": page_size, "offset": offset},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        msgs = data.get("messages", [])
        if not msgs:
            break
        out.extend(msgs)
        total = data.get("total", 0)
        print(f"  fetched {len(out)}/{total} (offset={offset})")
        if len(msgs) < page_size or len(out) >= total:
            break
        offset += page_size
        time.sleep(0.1)
    return out


def main() -> int:
    print(f">> fetching messages from {GROUP_ID}")
    msgs = fetch_all_messages(GROUP_ID)
    print(f">> total fetched: {len(msgs)}")

    polls = [m for m in msgs if m.get("type") == "poll"]
    actions = [m for m in msgs if m.get("type") == "action" and (m.get("action") or {}).get("type") == "vote"]
    print(f">> polls: {len(polls)} | vote actions: {len(actions)}")

    poll_index = {p["id"]: p for p in polls}

    from app.services.supabase_service import SupabaseRestClient
    from app.services.whatsapp_domain_service import WebhookIngestionService

    client = SupabaseRestClient.from_settings()
    print(f">> ingest backend: {type(client).__name__}")
    ingester = WebhookIngestionService(client)

    print(">> ingesting polls...")
    poll_stats = {"processed": 0, "duplicates": 0, "ignored": 0, "errors": 0}
    for poll in polls:
        try:
            res = ingester.ingest({"messages": [poll]})
            poll_stats["processed"] += res.get("processed", 0)
            poll_stats["duplicates"] += res.get("duplicates", 0)
            poll_stats["ignored"] += res.get("ignored", 0)
            if res.get("errors"):
                poll_stats["errors"] += len(res["errors"])
        except Exception as exc:
            poll_stats["errors"] += 1
            print(f"  poll {poll.get('id')} failed: {exc}")
    print(f">> polls ingested: {poll_stats}")

    print(">> ingesting votes...")
    vote_stats = {"processed": 0, "duplicates": 0, "ignored": 0, "errors": 0, "skipped_no_poll": 0}
    for act in actions:
        action = act.get("action") or {}
        target = action.get("target")
        poll_msg = poll_index.get(target)
        if not poll_msg:
            vote_stats["skipped_no_poll"] += 1
            continue
        results = (poll_msg.get("poll") or {}).get("results") or []
        update_payload = {
            "messages_updates": [
                {
                    "id": target,
                    "event_id": act.get("id"),
                    "timestamp": act.get("timestamp"),
                    "trigger": {
                        "id": act.get("id"),
                        "chat_id": act.get("chat_id"),
                        "from": act.get("from"),
                        "from_name": act.get("from_name"),
                        "action": action,
                    },
                    "after_update": {
                        "poll": {"results": results},
                    },
                }
            ]
        }
        try:
            res = ingester.ingest(update_payload)
            vote_stats["processed"] += res.get("processed", 0)
            vote_stats["duplicates"] += res.get("duplicates", 0)
            vote_stats["ignored"] += res.get("ignored", 0)
            if res.get("errors"):
                vote_stats["errors"] += len(res["errors"])
        except Exception as exc:
            vote_stats["errors"] += 1
            print(f"  vote on {target} failed: {exc}")
    print(f">> votes ingested: {vote_stats}")

    import sqlite3
    db = sqlite3.connect("data/raylook.db")
    print()
    print(">> SQLite final counts:")
    for t in ("webhook_inbox", "enquetes", "enquete_alternativas", "votos", "votos_eventos", "clientes"):
        n = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"   {t}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
