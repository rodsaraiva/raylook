"""WHAPI Cloud client for media retrieval."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger("raylook.integrations.whapi")


class WHAPIClient:
    """Thin client for WHAPI Cloud REST API."""

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or settings.WHAPI_TOKEN
        self.base_url = (base_url or settings.WHAPI_API_URL).rstrip("/")
        if not self.token:
            raise RuntimeError(
                "WHAPI_TOKEN not configured. Set it as an env var."
            )
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        self._timeout = httpx.Timeout(15.0, connect=5.0)

    def get_recent_messages(
        self,
        chat_id: str,
        time_to: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve recent messages from a chat, sorted descending.

        Args:
            chat_id: WhatsApp chat ID (e.g. '120363403901156886@g.us')
            time_to: Unix timestamp — fetch messages up to this time
            limit: max messages to return

        Returns:
            List of message dicts from WHAPI (field 'messages')
        """
        url = f"{self.base_url}/messages/list/{chat_id}"
        params: Dict[str, Any] = {"limit": limit, "sort": "desc"}
        if time_to:
            params["time_to"] = time_to
            params["until"] = time_to

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers, params=params, follow_redirects=True)
        if resp.status_code != 200:
            logger.error("WHAPI get_recent_messages error %s: %s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
        data = resp.json()
        return data.get("messages", [])

    def download_media(self, media_id: str) -> bytes:
        """
        Download raw media bytes for a given WHAPI media_id.

        Args:
            media_id: WHAPI media identifier (from message.image.id)

        Returns:
            Raw bytes of the media file
        """
        url = f"{self.base_url}/media/{media_id}"
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = client.get(url, headers=self._headers, follow_redirects=True)
        if resp.status_code != 200:
            logger.error("WHAPI download_media error %s: %s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
        return resp.content

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single message object by id."""
        url = f"{self.base_url}/messages/{message_id}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers, follow_redirects=True)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.error("WHAPI get_message error %s: %s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None

    def get_messages_page(
        self,
        chat_id: str,
        count: int = 100,
        time_from: Optional[int] = None,
        time_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Paginates /messages/list with optional time window."""
        url = f"{self.base_url}/messages/list/{chat_id}"
        params: Dict[str, Any] = {"count": count, "sort": "desc"}
        if time_from:
            params["time_from"] = time_from
        if time_to:
            params["time_to"] = time_to
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers, params=params, follow_redirects=True)
        if resp.status_code != 200:
            logger.error("WHAPI get_messages_page error %s: %s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
        data = resp.json()
        return data.get("messages", [])

    def get_poll_current_state(
        self,
        chat_id: str,
        poll_id: str,
        created_at_unix: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Busca estado atual do poll na WHAPI buscando em messages/list.

        Retorna dict com:
          {
            "poll_id": str,
            "title": str,
            "total": int,
            "results": [
              {"id": str, "name": str, "count": int, "voters": [phone_str, ...]}
            ]
          }
        ou None se não encontrado.
        """
        # Estratégia: páginas de 100 mensagens a partir da criação da poll.
        # WHAPI ordena desc (mais recentes primeiro), então começamos agora
        # e vamos paginar até encontrar a mensagem da poll.
        # Otimização: se temos created_at_unix, o poll estará perto desse timestamp.
        page_limit = 5  # max 5 páginas = 500 mensagens antes de desistir
        time_cursor: Optional[int] = None  # começa do topo (mais recente)

        for _page in range(page_limit):
            msgs = self.get_messages_page(
                chat_id,
                count=100,
                time_to=time_cursor,
            )
            if not msgs:
                break

            for msg in msgs:
                if msg.get("id") == poll_id and msg.get("type") == "poll":
                    poll = msg.get("poll") or {}
                    results_raw = poll.get("results") or []
                    results = []
                    for r in results_raw:
                        voters_raw = r.get("voters") or []
                        # voters pode ser lista de strings (phones) ou lista de dicts
                        voters: List[str] = []
                        for v in voters_raw:
                            if isinstance(v, str):
                                voters.append(v)
                            elif isinstance(v, dict):
                                phone = str(v.get("phone") or v.get("id") or "").strip()
                                if phone:
                                    voters.append(phone)
                        results.append({
                            "id": r.get("id"),
                            "name": str(r.get("name") or "").strip(),
                            "count": int(r.get("count") or len(voters)),
                            "voters": voters,
                        })
                    return {
                        "poll_id": poll_id,
                        "title": str(poll.get("title") or msg.get("text") or "").strip(),
                        "total": int(poll.get("total") or sum(r["count"] for r in results)),
                        "results": results,
                    }

            # Avança cursor pra timestamp da mensagem mais antiga desta página
            oldest = msgs[-1].get("timestamp")
            if oldest:
                time_cursor = int(oldest) - 1
            else:
                break

            # Se já passamos do created_at e não achamos, inutile continuar
            if created_at_unix and time_cursor and time_cursor < created_at_unix - 86400:
                break

        logger.warning(
            "get_poll_current_state: poll %s not found in chat %s after %d pages",
            poll_id, chat_id, page_limit,
        )
        return None

    def find_image_before_poll(
        self, messages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Given a list of messages (sorted desc by time), find the first image
        that appears right before (in time) a poll message.

        Replicates the n8n 'Code - Filtrar a última imagem' logic.
        """
        poll_index = next(
            (i for i, m in enumerate(messages) if m.get("type") == "poll"), None
        )
        if poll_index is None:
            logger.info("find_image_before_poll: no poll found in messages")
            return None
        # messages after poll_index are older (desc order)
        for m in messages[poll_index + 1 :]:
            if m.get("type") == "image":
                return m
        logger.info("find_image_before_poll: no image found before poll")
        return None
