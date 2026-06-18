"""Rota pública acionada pelo QR da etiqueta térmica: marca um cliente como
enviado. Sem sessão — a autorização é o token HMAC assinado (label_token).
Trade-off aceito: quem tiver a etiqueta física consegue marcar envio; a ação é
idempotente e reversível pelo admin."""
from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.routers.dashboard import _mark_client_shipped
from app.services.label_token import read_ship_token
from app.services.supabase_service import SupabaseRestClient

router = APIRouter()


def _page(title: str, body: str, ok: bool) -> HTMLResponse:
    color = "#16a34a" if ok else "#dc2626"
    icon = "✓" if ok else "⚠"
    # Escapa no sink: `body` pode conter o nome do cliente (pushName do WhatsApp),
    # que não é HTML-escapado em _sanitize_name — sem isso vira XSS na origem.
    title = html.escape(title)
    body = html.escape(body)
    page = (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title></head>"
        '<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px 20px;">'
        f'<div style="font-size:64px;color:{color};">{icon}</div>'
        f'<h1 style="font-size:20px;color:{color};margin:8px 0;">{title}</h1>'
        f'<p style="font-size:16px;color:#333;">{body}</p>'
        "</body></html>"
    )
    return HTMLResponse(page, status_code=200 if ok else 400)


@router.get("/s/{token}")
def mark_shipped_via_qr(token: str, request: Request) -> HTMLResponse:
    parsed = read_ship_token(token)
    if not parsed:
        return _page("Link inválido", "QR não reconhecido ou adulterado.", ok=False)
    pacote_id, cliente_id = parsed

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        return _page("Pacote não encontrado", "Esse pacote não existe mais.", ok=False)
    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        return _page("Cliente não encontrado", "Cliente não está nesse pacote.", ok=False)

    venda = client.select("vendas", filters=[("pacote_cliente_id", "eq", pc["id"])], single=True)
    pag = (
        client.select("pagamentos", filters=[("venda_id", "eq", venda["id"])], single=True)
        if venda else None
    )
    if not pag or (pag.get("status") or "").lower() != "paid":
        return _page("Cliente não pagou", "Não dá pra marcar envio antes do pagamento.", ok=False)

    cli = client.select("clientes", columns="id,nome", filters=[("id", "eq", cliente_id)], single=True) or {}
    nome = cli.get("nome") or "Cliente"

    changed = _mark_client_shipped(client, pkg, pc, role="qr")
    if changed:
        return _page("Enviado!", f"{nome} marcado como enviado.", ok=True)
    return _page("Já enviado", f"{nome} já estava marcado como enviado.", ok=True)
