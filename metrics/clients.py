import json
import time
from typing import List, Dict, Any

import httpx

from app.config import settings

API_URL = settings.baserow_api_url
TOKEN = settings.BASEROW_API_TOKEN

def fetch_rows_filtered(table_id: str, extra_params: Dict[str, Any], size: int = 200) -> List[Dict[str, Any]]:
    """Fetch rows from a Baserow table using server-side query filters.

    Example:
      fetch_rows_filtered("17", {"filter__field_158__equal": poll_id, "filter__field_160__equal": phone})
    """
    rows: Dict[int, Dict[str, Any]] = {}
    if not TOKEN:
        raise RuntimeError(
            "Baserow API token not configured (BASEROW_API_TOKEN). "
            "Set it in the environment or in .env and restart the app."
        )

    url = f"{API_URL}/api/database/rows/table/{table_id}/"
    headers = {"Authorization": f"Token {TOKEN}"}
    timeout = httpx.Timeout(10.0, connect=5.0)
    max_retries = 3

    params: Dict[str, Any] = {"user_field_names": "true", "size": size}
    params.update(extra_params or {})
    first_request = True

    while url:
        attempt = 0
        last_exc: Exception | None = None
        while attempt < max_retries:
            attempt += 1
            try:
                with httpx.Client(timeout=timeout) as client:
                    if first_request:
                        resp = client.get(url, headers=headers, params=params, follow_redirects=True)
                    else:
                        resp = client.get(url, headers=headers, follow_redirects=True)
                if resp.status_code == 200:
                    data = resp.json()
                    for row in data.get("results", []):
                        rows[row["id"]] = row
                    url = data.get("next")
                    first_request = False
                    break
                if resp.status_code == 401:
                    raise RuntimeError(
                        f"Unauthorized (401) fetching table {table_id}. Verify BASEROW_API_TOKEN."
                    )
                raise RuntimeError(f"Error fetching table {table_id}: {resp.status_code} - {resp.text}")
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise RuntimeError(f"Network error fetching table {table_id}: {exc}") from exc
        else:
            if last_exc:
                raise RuntimeError(f"Failed fetching table {table_id}: {last_exc}") from last_exc

    return list(rows.values())


def create_row(table_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Create a single row in a Baserow table (POST). Uses user_field_names=true."""
    if not TOKEN:
        raise RuntimeError(
            "Baserow API token not configured (BASEROW_API_TOKEN). "
            "Set it in the environment or in .env and restart the app."
        )
    url = f"{API_URL}/api/database/rows/table/{table_id}/"
    headers = {"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"}
    params = {"user_field_names": "true"}
    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, params=params, json=fields, follow_redirects=True)
    if resp.status_code == 401:
        raise RuntimeError(
            f"Unauthorized (401) creating row in table {table_id}. Verify BASEROW_API_TOKEN."
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Error creating row in table {table_id}: {resp.status_code} - {resp.text}"
        )
    return resp.json()


def fetch_all_rows(table_id: str) -> List[Dict[str, Any]]:
    """Fetch all rows from a Baserow table with basic pagination handling.

    Uses httpx with a simple retry/backoff strategy for robustness.
    """
    rows: Dict[int, Dict[str, Any]] = {}
    if not TOKEN:
        raise RuntimeError(
            "Baserow API token not configured (BASEROW_API_TOKEN). "
            "Set it in the environment or in .env and restart the app."
        )

    url = f"{API_URL}/api/database/rows/table/{table_id}/?user_field_names=true"
    headers = {"Authorization": f"Token {TOKEN}"}

    timeout = httpx.Timeout(10.0, connect=5.0)
    max_retries = 3

    while url:
        attempt = 0
        last_exc: Exception | None = None
        while attempt < max_retries:
            attempt += 1
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(url, headers=headers, follow_redirects=True)
                if resp.status_code == 200:
                    data = resp.json()
                    for row in data.get("results", []):
                        rows[row["id"]] = row
                    url = data.get("next")
                    break
                if resp.status_code == 401:
                    raise RuntimeError(
                        f"Unauthorized (401) fetching table {table_id}. Verify BASEROW_API_TOKEN."
                    )
                raise RuntimeError(f"Error fetching table {table_id}: {resp.status_code} - {resp.text}")
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise RuntimeError(f"Network error fetching table {table_id}: {exc}") from exc
        else:
            # exhausted retries for this page
            if last_exc:
                raise RuntimeError(f"Failed fetching table {table_id}: {last_exc}") from last_exc

    return list(rows.values())

