"""Atribuição de friendly_id (PAC{NNN}/{DDMM}) aos pacotes.

A sequência reseta por dia (ordem de fechamento). A RPC
`assign_pacote_friendly_id` cuida da atomicidade no backend; aqui
ficam só a normalização do dia (timezone São Paulo) e o entrypoint
para o resto do código.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("raylook.services.friendly_id")

_TZ_SP = timezone(timedelta(hours=-3))


def _ddmm_for(when: Optional[datetime] = None) -> str:
    moment = when or datetime.now(_TZ_SP)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(_TZ_SP)
    return f"{local.day:02d}{local.month:02d}"


def assign_friendly_id(client, pacote_id: str, when: Optional[datetime] = None) -> Optional[str]:
    """Garante que o pacote tem friendly_id. Retorna o ID (existente ou novo)."""
    if not pacote_id:
        return None
    ddmm = _ddmm_for(when)
    try:
        result = client.rpc(
            "assign_pacote_friendly_id",
            {"p_pacote_id": pacote_id, "p_ddmm": ddmm},
        )
    except Exception:
        logger.exception("Falha ao atribuir friendly_id pacote=%s", pacote_id)
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("assign_pacote_friendly_id") or result.get("friendly_id")
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return first.get("assign_pacote_friendly_id") or first.get("friendly_id")
        if isinstance(first, str):
            return first
    return None
