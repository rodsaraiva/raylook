"""Backfill polls + votos do grupo oficial via WHAPI API.

Chama o WebhookIngestionService direto (sem passar pelo HTTP). Backend
respeita DATA_BACKEND do env — sqlite em dev, postgres em prod.

Args:
    --since=<epoch>   Pega só mensagens com timestamp >= epoch.
                      Aceita também: today | yesterday | 7d | 30d | all.
                      Default: today.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from typing import Any, Dict, List

import dotenv
import requests

dotenv.load_dotenv()


def parse_since(value: str) -> int:
    if not value or value.lower() == "all":
        return 0
    if value.lower() == "today":
        return int(_dt.datetime.combine(_dt.date.today(), _dt.time.min,
                                        tzinfo=_dt.timezone.utc).timestamp())
    if value.lower() == "yesterday":
        return int(_dt.datetime.combine(_dt.date.today() - _dt.timedelta(days=1),
                                        _dt.time.min, tzinfo=_dt.timezone.utc).timestamp())
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
        return int((_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).timestamp())
    return int(value)

# Garantir que o ingest path está habilitado pelo backend SQLite
os.environ.setdefault("DATA_BACKEND", "sqlite")
os.environ.setdefault("RAYLOOK_SANDBOX", "true")

WHAPI_TOKEN = os.environ["WHAPI_TOKEN"]
WHAPI_URL = os.environ.get("WHAPI_API_URL", "https://gate.whapi.cloud").rstrip("/")
GROUP_ID = os.environ["OFFICIAL_GROUP_CHAT_ID"]


def fetch_all_messages(group_id: str, time_from: int = 0) -> List[Dict[str, Any]]:
    """Pagina /messages/list até esgotar. time_from=epoch filtra no servidor."""
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    out: List[Dict[str, Any]] = []
    offset = 0
    page_size = 200
    while True:
        params = {"count": page_size, "offset": offset}
        if time_from:
            params["time_from"] = time_from
        r = requests.get(
            f"{WHAPI_URL}/messages/list/{group_id}",
            params=params,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        msgs = data.get("messages", [])
        if not msgs:
            break
        # Filtro local (caso WHAPI ignore time_from)
        if time_from:
            msgs = [m for m in msgs if int(m.get("timestamp") or 0) >= time_from]
        out.extend(msgs)
        total = data.get("total", 0)
        print(f"  fetched {len(out)}/{total} (offset={offset})")
        if len(msgs) < page_size or len(out) >= total:
            break
        offset += page_size
        time.sleep(0.1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="today",
                        help="today | yesterday | 7d | 30d | all | <epoch>")
    args = parser.parse_args()
    time_from = parse_since(args.since)
    label = args.since if not time_from else f"since={args.since} (epoch={time_from})"
    print(f">> fetching messages from {GROUP_ID} [{label}]")
    msgs = fetch_all_messages(GROUP_ID, time_from=time_from)
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

    print()
    print(">> final counts (via client API):")
    for t in ("webhook_inbox", "enquetes", "enquete_alternativas", "votos", "votos_eventos", "clientes"):
        try:
            rows = client.select(t, columns="id", limit=10000) or []
            print(f"   {t}: {len(rows)}")
        except Exception as exc:
            print(f"   {t}: erro {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
