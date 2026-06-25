"""Sessões do dashboard: agrupam enquetes por substring do título e definem
o modo de fechamento. 'accumulate' = votos acumulam até o botão 'fechar pacote'
(sem subset-sum 24). Ausência de match = comportamento legado.

Lido pelo backend (ingest/rebuild) e pelo dashboard (aba + filtro)."""
from __future__ import annotations

from typing import Optional

SESSIONS: list[dict] = [
    {"name": "Bernardo", "match": "Bernardo", "mode": "accumulate"},
]


def session_for_title(titulo: Optional[str]) -> Optional[dict]:
    if not titulo:
        return None
    alvo = titulo.casefold()
    for sessao in SESSIONS:
        if sessao["match"].casefold() in alvo:
            return sessao
    return None


def accumulate_session_for_title(titulo: Optional[str]) -> Optional[dict]:
    sessao = session_for_title(titulo)
    if sessao and sessao.get("mode") == "accumulate":
        return sessao
    return None
