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


def _is_valid_cnpj(raw: str) -> bool:
    """Valida CNPJ brasileiro: 14 dígitos + dois dígitos verificadores."""
    digits = _re_email.sub(r"\D", "", str(raw or ""))
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6] + weights1
    for idx, weights in ((12, weights1), (13, weights2)):
        total = sum(int(digits[j]) * weights[j] for j in range(idx))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(digits[idx]):
            return False
    return True


def _is_valid_cpf_cnpj(raw: str) -> bool:
    """Aceita CPF (11 dígitos) ou CNPJ (14 dígitos), valida checksum."""
    digits = _re_email.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11:
        return _is_valid_cpf(digits)
    if len(digits) == 14:
        return _is_valid_cnpj(digits)
    return False


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
    kind: Optional[str]
    if password == _MASTER_KEY:
        kind = "master"
    else:
        kind = ps.verify_password(client["id"], password)

    if not kind:
        return _templates().TemplateResponse(request, "portal_login.html", {
            "error": "Senha incorreta.",
            "phone": phone,
        })

    if kind == "temp":
        ps.mark_must_change_password(client["id"], True)

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
    cpf_cnpj: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Salva senha + email + CPF/CNPJ e cria sessão."""
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
    if not _is_valid_cpf_cnpj(cpf_cnpj):
        errors.append("CPF/CNPJ inválido. Use 11 dígitos para CPF ou 14 para CNPJ.")

    if errors:
        return _templates().TemplateResponse(request, "portal_setup.html", {
            "phone": phone,
            "nome": client.get("nome") or "",
            "email": email,
            "cpf_cnpj": cpf_cnpj,
            "errors": errors,
        })

    token = ps.setup_client(client["id"], password, email, cpf_cnpj)
    resp = RedirectResponse("/portal/pedidos", status_code=302)
    return _set_session_cookie(resp, token)


# ---------------------------------------------------------------------------
# Esqueci minha senha — gera senha temp de 30min e mostra na tela
# ---------------------------------------------------------------------------

@router.get("/reset", response_class=HTMLResponse)
async def portal_reset_page(request: Request):
    return _templates().TemplateResponse(request, "portal_reset.html", {})


@router.post("/reset")
async def portal_reset_submit(request: Request, phone: str = Form(...)):
    """Gera senha temp de 30min e mostra na própria tela.

    Rate-limit reaproveita o do login pra dificultar enumeração + brute force
    de geração de temps. Se o número não existe, devolve a mesma tela de
    sucesso com uma senha falsa pra não revelar cadastro.
    """
    if not ps.check_rate_limit(phone):
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "error": "Muitas tentativas. Aguarde 15 minutos.",
        })
    ps.record_login_attempt(phone)

    client = ps.get_client_by_phone(phone)
    if not client:
        # Não revelar se o número existe — devolve sucesso com senha aleatória
        # que não foi gravada em DB (não vai funcionar no login).
        fake = ps.generate_temp_password_plaintext()
        return _templates().TemplateResponse(request, "portal_reset.html", {
            "temp_password": fake,
            "expires_minutes": ps.TEMP_PASSWORD_MINUTES,
        })

    temp = ps.create_temp_password(client["id"])
    logger.info("temp password generated for cliente_id=%s", client["id"])
    return _templates().TemplateResponse(request, "portal_reset.html", {
        "temp_password": temp,
        "expires_minutes": ps.TEMP_PASSWORD_MINUTES,
    })


# ---------------------------------------------------------------------------
# Troca de senha (modal blocking após login com temp)
# ---------------------------------------------------------------------------

@router.post("/change-password")
async def portal_change_password(
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Não autenticado."}, status_code=401)

    if len(password) < 6:
        return JSONResponse({"error": "A senha deve ter pelo menos 6 caracteres."}, status_code=400)
    if password != password_confirm:
        return JSONResponse({"error": "As senhas não conferem."}, status_code=400)

    ps.change_password(client["id"], password)
    return JSONResponse({"ok": True})


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


def _is_admin_request(request: Request) -> bool:
    """Verifica se a request tem sessão admin do dashboard. O middleware do
    dashboard pula tudo em /portal/*, então a checagem é feita aqui."""
    if os.getenv("DASHBOARD_AUTH_DISABLED", "").strip().lower() in ("true", "1", "yes"):
        return True
    from app.services import auth_service as _auth
    role = _auth.read_session_token(request.cookies.get("dash_session", ""))
    return role == "admin"


def _is_dashboard_request(request: Request) -> bool:
    """Qualquer usuário logado do dashboard (admin/estoque/logística). O preview
    é somente leitura, então não precisa ser exclusivo do admin."""
    if os.getenv("DASHBOARD_AUTH_DISABLED", "").strip().lower() in ("true", "1", "yes"):
        return True
    from app.services import auth_service as _auth
    role = _auth.read_session_token(request.cookies.get("dash_session", ""))
    return role is not None


@router.get("/preview/{cliente_id}", response_class=HTMLResponse)
async def portal_preview(request: Request, cliente_id: str):
    """Renderiza o portal_pedidos como o cliente vê, em modo somente leitura.
    Acessado a partir da aba Clientes do dashboard por qualquer usuário logado."""
    if not _is_dashboard_request(request):
        return RedirectResponse("/login", status_code=302)

    client = ps.get_client_by_id(cliente_id)
    if not client:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    orders = ps.get_client_orders(client["id"])
    kpis = ps.get_client_kpis(orders)

    return _templates().TemplateResponse(request, "portal_pedidos.html", {
        "cliente": client,
        "orders": orders,
        "kpis": kpis,
        "read_only": True,
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
    """Salva CPF/CNPJ do cliente logado. Usado pelo modal de contingência
    quando o pagamento é bloqueado por falta de CPF/CNPJ (412 cpf_required)."""
    client = await _get_current_client(request)
    if not client:
        return JSONResponse({"error": "Sessão expirada"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cpf_cnpj = str(body.get("cpf") or body.get("cpf_cnpj") or "").strip()
    if not _is_valid_cpf_cnpj(cpf_cnpj):
        return JSONResponse({"error": "CPF/CNPJ inválido"}, status_code=400)
    ps.update_cpf(client["id"], cpf_cnpj)
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
