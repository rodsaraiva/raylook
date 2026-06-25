"""Router da sessão Bernardo (modo acúmulo) — entrega isolada em /bernardo.

Página standalone + API própria (/api/bernardo/*), sem tocar no dashboard normal.
A lógica de fechamento por acúmulo vive em PackageService.close_accumulated; aqui
só ficam a rota da página, a leitura do acúmulo corrente e o disparo do fechamento.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.sessions import SESSIONS, accumulate_session_for_title
from app.services.supabase_service import SupabaseRestClient
from app.services.whatsapp_domain_service import PackageService

_BERNARDO_ROLES = {"admin", "bernardo"}


def require_bernardo_access(request: Request) -> str:
    """403 a menos que o role seja admin ou bernardo (defesa em profundidade)."""
    role = getattr(request.state, "role", None)
    if role not in _BERNARDO_ROLES:
        raise HTTPException(status_code=403, detail="forbidden")
    return role


router = APIRouter(tags=["bernardo"], dependencies=[Depends(require_bernardo_access)])
templates = Jinja2Templates(directory="templates")


def _session_by_name(session_name: str) -> Optional[Dict[str, Any]]:
    for s in SESSIONS:
        if s["name"].casefold() == session_name.casefold():
            return s
    return None


@router.get("/bernardo", response_class=HTMLResponse)
def bernardo_page(request: Request):
    """Página standalone da sessão Bernardo."""
    return templates.TemplateResponse(request, "bernardo.html", {"settings": settings})


@router.get("/api/bernardo/sessions/{session_name}")
def get_session(session_name: str) -> Dict[str, Any]:
    """Enquetes da sessão (modo acúmulo) + o acúmulo corrente de cada uma."""
    session = _session_by_name(session_name)
    if not session:
        raise HTTPException(404, "sessão não encontrada")
    client = SupabaseRestClient.from_settings()
    svc = PackageService(client)
    enquetes = client.select(
        "enquetes", columns="id,titulo,status,produto_id,fornecedor",
        filters=[("status", "eq", "open")],
    )
    items: List[Dict[str, Any]] = []
    for e in (enquetes if isinstance(enquetes, list) else []):
        match = accumulate_session_for_title(e.get("titulo"))
        if not match or match["name"] != session["name"]:
            continue
        votos = client.select(
            "votos", columns="id,cliente_id,qty,voted_at,status",
            filters=[("enquete_id", "eq", e["id"]), ("status", "neq", "out")],
        )
        active = [
            v for v in (votos if isinstance(votos, list) else [])
            if str(v.get("status") or "").strip().lower() != "out" and int(v.get("qty") or 0) > 0
        ]
        active.sort(key=lambda v: (-int(v.get("qty") or 0), str(v.get("voted_at") or "")))
        pending = svc._accumulate_pending(e["id"], active)
        cids = list({str(v["cliente_id"]) for v in pending})
        nomes: Dict[str, Any] = {}
        if cids:
            crows = client.select("clientes", columns="id,nome", filters=[("id", "in", cids)])
            nomes = {str(c["id"]): c.get("nome") for c in (crows if isinstance(crows, list) else [])}
        participants = [{"nome": nomes.get(str(v["cliente_id"]), "—"), "qty": int(v["qty"])} for v in pending]
        items.append({
            "enquete_id": e["id"], "titulo": e.get("titulo"),
            "total_qty": sum(int(v["qty"]) for v in pending),
            "participants_count": len(pending), "participants": participants,
        })
    return {"session": session["name"], "enquetes": items}


@router.post("/api/bernardo/sessions/{session_name}/close")
async def close_session_package(session_name: str, request: Request) -> Dict[str, Any]:
    """Fecha o pacote acumulado de UMA enquete da sessão (botão 'fechar pacote')."""
    session = _session_by_name(session_name)
    if not session:
        raise HTTPException(404, "sessão não encontrada")
    body = await request.json()
    enquete_id = (body or {}).get("enquete_id")
    if not enquete_id:
        raise HTTPException(400, "enquete_id obrigatório")
    client = SupabaseRestClient.from_settings()
    rows = client.select("enquetes", columns="id,titulo", filters=[("id", "eq", enquete_id)], limit=1)
    enq = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(enq, dict):
        raise HTTPException(404, "enquete não encontrada")
    match = accumulate_session_for_title(enq.get("titulo"))
    if not match or match["name"] != session["name"]:
        raise HTTPException(400, "enquete não pertence a esta sessão")
    return PackageService(client).close_accumulated(enquete_id)
