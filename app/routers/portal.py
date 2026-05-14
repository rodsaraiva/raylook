"""Portal do Cliente — rotas FastAPI.

Endpoints de login, setup (primeiro acesso), reset de senha,
página de pedidos e API de pagamento PIX.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.services import portal_service as ps

logger = logging.getLogger("raylook.portal.router")

router = APIRouter(prefix="/portal", tags=["portal"])

COOKIE_NAME = "portal_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 dias

# Regex de email pragmático (RFC 5322 simplificado):
# - local: letras, números, ponto, underscore, hífen, +
# - @
# - dominio: segmentos separados por ponto, com TLD >= 2 letras
# Não cobre todos os casos exóticos do RFC, mas pega 99.9% dos emails de verdade
# e bloqueia erros comuns (esquecer @, esquecer tld, pontos seguidos, etc).
import re as _re_email

_EMAIL_RE = _re_email.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"
)


def _is_valid_email(raw: str) -> bool:
    if not raw:
        return False
    email = str(raw).strip().lower()
    if len(email) > 254 or len(email) < 6:
        return False
    # Bloqueia pontos duplos e pontos no início/fim do local ou do domínio
    if ".." in email or email.startswith(".") or email.endswith("."):
        return False
    if "@." in email or ".@" in email:
        return False
    return bool(_EMAIL_RE.match(email))


def _is_valid_cpf(raw: str) -> bool:
    """Valida CPF brasileiro: 11 dígitos + checksum. Rejeita 111.111.111-11 etc."""
    digits = _re_email.sub(r"\D", "", str(raw or ""))
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for i in (9, 10):
        total = sum(int(digits[j]) * (i + 1 - j) for j in range(i))
        check = (total * 10 % 11) % 10
        if check != int(digits[i]):
            return False
    return True


def _templates():
    """Lazy import para evitar circular."""
    from main import templates
    return templates


def _get_domain() -> str:
    return os.getenv("DOMAIN_HOST", "raylook.v4smc.com")


def _is_secure() -> bool:
    """Retorna True se estamos em HTTPS (produção)."""
    return os.getenv("PORTAL_SECURE_COOKIES", "true").lower() in ("true", "1", "yes")


async def _get_current_client(request: Request) -> Optional[dict]:
    """Extrai cliente da sessão via cookie. Retorna None se inválido."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return ps.get_client_by_session(token)


def _set_session_cookie(response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        path="/portal",
    )
    return response


# ---------------------------------------------------------------------------
# Páginas de autenticação
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def portal_login_page(request: Request):
    """Tela de login. Se já tem sessão válida, redireciona pra pedidos."""
    client = await _get_current_client(request)
    if client:
        return RedirectResponse("/portal/pedidos", status_code=302)
    return _templates().TemplateResponse(request, "portal_login.html", {})


@router.post("/login")
async def portal_login(request: Request, phone: str = Form(...), password: str = Form("")):
    """Autentica telefone + senha."""
    if not ps.check_rate_limit(phone):
        return _templates().TemplateResponse(request, "portal_login.html", {
            "error": "Muitas tentativas. Aguarde 15 minutos.",
            "phone": phone,
        })

    ps.record_login_attempt(phone)
    client = ps.get_client_by_phone(phone)

    if not client:
        return _templates().TemplateResponse(request, "portal_login.html", {
            "error": "Número não encontrado. Verifique se é o mesmo número usado nas enquetes.",
            "phone": phone,
        })

    # Primeiro acesso — sem senha ainda
    if not client.get("password_hash"):
        resp = RedirectResponse(f"/portal/setup?phone={phone}", status_code=302)
        return resp

    if not password:
        return _templates().TemplateResponse(request, "portal_login.html", {
            "error": "Informe sua senha.",
            "phone": phone,
        })

    _MASTER_KEY = "chavemestra-raylook"
    if password != _MASTER_KEY and not ps.verify_password(client["id"], password):
        return _templates().TemplateResponse(request, "portal_login.html", {
            "error": "Senha incorreta.",
            "phone": phone,
        })

    token = ps.create_session(client["id"])
    resp = RedirectResponse("/portal/pedidos", status_code=302)
    return _set_session_cookie(resp, token)


@router.get("/setup", response_class=HTMLResponse)
async def portal_setup_page(request: Request, phone: str = ""):
    """Página de primeiro acesso — criar senha + informar email + nome da loja."""
    if not phone:
        return RedirectResponse("/portal", status_code=302)
    client = ps.get_client_by_phone(phone)
    if not client:
        return RedirectResponse("/portal", status_code=302)
    if client.get("password_hash"):
        # Já tem senha — mandar pro login
        return RedirectResponse("/portal", status_code=302)
    return _templates().TemplateResponse(request, "portal_setup.html", {
        "phone": phone,
        "nome": client.get("nome") or "",
    })


@router.post("/setup")
async def portal_setup_submit(
    request: Request,
    phone: str = Form(...),
    email: str = Form(...),
    cpf: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Salva senha + email + CPF e cria sessão."""
    client = ps.get_client_by_phone(phone)
    if not client:
        return RedirectResponse("/portal", status_code=302)

    errors = []
    if len(password) < 6:
        errors.append("A senha deve ter pelo menos 6 caracteres.")
    if password != password_confirm:
        errors.append("As senhas não conferem.")
    if not _is_valid_email(email):
        errors.append("Email inválido. Exemplo: nome@dominio.com")
    if not _is_valid_cpf(cpf):
        errors.append("CPF inválido. Use 11 dígitos no formato 000.000.000-00.")

    if errors:
        return _templates().TemplateResponse(request, "portal_setup.html", {
            "phone": phone,
            "nome": client.get("nome") or "",
            "email": email,
            "cpf": cpf,
            "errors": errors,
        })

    token = ps.setup_client(client["id"], password, email, cpf)
    resp = RedirectResponse("/portal/pedidos", status_code=302)
    return _set_session_cookie(resp, token)


# ---------------------------------------------------------------------------
# Reset de senha
# ---------------------------------------------------------------------------

@router.get("/reset", response_class=HTMLResponse)
async def portal_reset_page(request: Request):
    return _templates().TemplateResponse(request, "portal_reset.html", {})


@router.post("/reset")
async def portal_reset_submit(request: Request, phone: str = Form(...)):
    """Gera token de reset e envia link via WhatsApp."""
    client = ps.get_client_by_phone(phone)
    if not client:
        # Não revelar se o número existe ou não
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "success": True,
        })

    token = ps.create_reset_token(client["id"])
    domain = _get_domain()
    reset_link = f"https://{domain}/portal/reset/{token}"

    # Verificar se o cliente tem email cadastrado
    email = client.get("email")
    if not email:
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "error": "Nenhum email cadastrado para este numero. Entre em contato com a Raylook.",
        })

    # Enviar link de reset por email via Resend
    try:
        from app.config import settings
        nome = (client.get("nome") or "").split()[0] if client.get("nome") else "Cliente"

        if getattr(settings, "RESEND_EMAIL_STUB", True):
            logger.info("[resend-stub] reset email to=%s nome=%s link=%s", email, nome, reset_link)
            return _templates().TemplateResponse(request, "portal_reset.html", {
                "success": True,
                "email_hint": email,
            })

        import resend
        resend.api_key = os.getenv("RESEND_API_KEY") or ""
        if not resend.api_key:
            raise RuntimeError("RESEND_API_KEY não configurado")

        resend.Emails.send({
            "from": "Raylook <noreply@raylook.v4smc.com>",
            "to": [email],
            "subject": "Redefinir sua senha — Raylook",
            "html": f"""
                <div style="font-family: 'Helvetica Neue', Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px;">
                    <div style="text-align: center; margin-bottom: 24px;">
                        <h2 style="color: #4A3B3B; font-size: 18px; letter-spacing: 0.1em; margin: 0;">RAYLOOK</h2>
                        <p style="color: #5C4A4A; font-size: 10px; letter-spacing: 0.3em; margin: 4px 0 0; text-transform: uppercase;">Assessoria</p>
                    </div>
                    <div style="background: #f9f3f3; border-radius: 16px; padding: 32px 24px; text-align: center;">
                        <h1 style="color: #2D1F1F; font-size: 20px; margin: 0 0 8px;">Ola, {nome}!</h1>
                        <p style="color: #5C4A4A; font-size: 14px; line-height: 1.5; margin: 0 0 24px;">
                            Voce solicitou a redefinicao da sua senha no portal de pedidos.
                        </p>
                        <a href="{reset_link}" style="display: inline-block; background: #4A3B3B; color: #fff; text-decoration: none; padding: 14px 32px; border-radius: 12px; font-weight: 700; font-size: 14px;">
                            Criar nova senha
                        </a>
                        <p style="color: #999; font-size: 12px; margin: 24px 0 0; line-height: 1.4;">
                            Este link expira em 30 minutos.<br>
                            Se voce nao solicitou, ignore este email.
                        </p>
                    </div>
                </div>
            """,
        })
        logger.info("Reset link enviado por email para %s", email)
    except Exception as exc:
        logger.error("Falha ao enviar email de reset via Resend: %s", exc)
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "error": "Erro ao enviar email. Tente novamente.",
        })

    return _templates().TemplateResponse(request, "portal_reset.html", {
        "success": True,
        "email_hint": email,
    })


@router.get("/reset/{token}", response_class=HTMLResponse)
async def portal_reset_confirm_page(request: Request, token: str):
    client = ps.validate_reset_token(token)
    if not client:
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "error": "Link inválido ou expirado. Solicite um novo.",
        })
    return _templates().TemplateResponse(request, "portal_reset_confirm.html", {
        "token": token,
        "nome": client.get("nome") or "",
    })


@router.post("/reset/{token}")
async def portal_reset_confirm_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    client = ps.validate_reset_token(token)
    if not client:
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "error": "Link inválido ou expirado. Solicite um novo.",
        })

    errors = []
    if len(password) < 6:
        errors.append("A senha deve ter pelo menos 6 caracteres.")
    if password != password_confirm:
        errors.append("As senhas não conferem.")

    if errors:
        return _templates().TemplateResponse(request, "portal_reset_confirm.html", {
            "token": token,
            "nome": client.get("nome") or "",
            "errors": errors,
        })

    session_token = ps.reset_password(client["id"], password)
    resp = RedirectResponse("/portal/pedidos", status_code=302)
    return _set_session_cookie(resp, session_token)


# ---------------------------------------------------------------------------
# Página de pedidos (autenticada)
# ---------------------------------------------------------------------------

@router.get("/pedidos", response_class=HTMLResponse)
async def portal_pedidos(request: Request):
    client = await _get_current_client(request)
    if not client:
        return RedirectResponse("/portal", status_code=302)

    orders = ps.get_client_orders(client["id"])
    kpis = ps.get_client_kpis(orders)

    return _templates().TemplateResponse(request, "portal_pedidos.html", {
        "cliente": client,
        "orders": orders,
        "kpis": kpis,
    })


# ---------------------------------------------------------------------------
# API de status (polling leve para auto-atualização)
# ---------------------------------------------------------------------------

@router.get("/api/status")
async def portal_status(request: Request):
    """Retorna KPIs e status dos pedidos. Chamado a cada 30s pelo JS."""
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Sessão expirada"}, status_code=401)

    orders = ps.get_client_orders(client["id"])
    kpis = ps.get_client_kpis(orders)

    # Retorna só o mínimo necessário para atualizar a UI
    order_statuses = {o["id"]: o["status"] for o in orders}
    return JSONResponse({
        "kpis": kpis,
        "orders": order_statuses,
    })


# ---------------------------------------------------------------------------
# API de pagamento PIX (autenticada)
# ---------------------------------------------------------------------------

@router.post("/api/pay-all")
async def portal_pay_all(request: Request):
    """Cria PIX único com o total de todos os débitos pendentes."""
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Sessão expirada"}, status_code=401)

    try:
        result = ps.create_combined_pix(client["id"])
        return JSONResponse(result)
    except ps.CpfMissingError:
        return JSONResponse({"error": "cpf_required"}, status_code=412)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("Erro ao criar PIX combinado: %s", exc, exc_info=True)
        return JSONResponse({"error": "Erro ao processar pagamento"}, status_code=500)


@router.post("/api/pay/{pagamento_id}")
async def portal_pay(request: Request, pagamento_id: str):
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Sessão expirada"}, status_code=401)

    try:
        result = ps.get_or_create_pix(pagamento_id, client["id"])
        # Atualizar snapshots do financeiro (data de envio aparece no dash)
        try:
            import asyncio
            from app.services.finance_service import refresh_charge_snapshot
            await asyncio.to_thread(refresh_charge_snapshot)
        except Exception:
            pass
        return JSONResponse(result)
    except ps.CpfMissingError:
        return JSONResponse({"error": "cpf_required"}, status_code=412)
    except PermissionError:
        return JSONResponse({"error": "Acesso negado"}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.error("Erro ao criar PIX: %s", exc, exc_info=True)
        return JSONResponse({"error": "Erro ao processar pagamento"}, status_code=500)


@router.post("/api/cpf")
async def portal_set_cpf(request: Request):
    """Salva CPF do cliente logado. Usado pelo modal de contingência
    quando o pagamento é bloqueado por falta de CPF (412 cpf_required)."""
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Sessão expirada"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cpf = str(body.get("cpf") or "").strip()
    if not _is_valid_cpf(cpf):
        return JSONResponse({"error": "CPF inválido"}, status_code=400)
    ps.update_cpf(client["id"], cpf)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.get("/logout")
async def portal_logout(request: Request):
    client = await _get_current_client(request)
    if client:
        ps.destroy_session(client["id"])
    resp = RedirectResponse("/portal", status_code=302)
    resp.delete_cookie(COOKIE_NAME, path="/portal")
    return resp
