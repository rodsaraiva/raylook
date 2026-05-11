import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import asyncio
import copy
import traceback
import logging
from metrics import processors, clients
from metrics.actions import ConfirmAction, RejectAction, RevertAction
from metrics.models import DashboardModel
from app.config import settings
from app.services.finance_service import build_stats as build_finance_stats
from app.services.finance_service import get_dashboard_stats as get_finance_dashboard_stats
from app.services.finance_service import list_charges as list_finance_charges
from app.services.finance_service import list_charges_page as list_finance_charges_page
from app.services.finance_service import refresh_dashboard_stats as refresh_finance_dashboard_stats
from app.services.finance_service import get_package_charge_contexts
from app.services.metrics_service import generate_and_persist_metrics
from app.services.customer_service import load_customers
from app.services.customer_service import refresh_customer_rows_snapshot
from app.services.domain_lookup import resolve_supabase_package_id
from app.services.group_context_service import monitored_chat_ids
from app.services.routing_service import (
    backfill_metrics_routing,
    load_poll_chat_map,
    resolve_chat_id,
    resolve_poll_id,
    resolve_target_phone,
)
from app.services.runtime_state_service import (
    CUSTOMER_ROWS_STATE_KEY,
    DASHBOARD_METRICS_STATE_KEY,
    FINANCE_CHARGES_STATE_KEY,
    FINANCE_STATS_STATE_KEY,
    load_runtime_state_metadata,
)
from app.services.staging_dry_run_service import (
    is_staging_dry_run,
    simulate_confirm_package,
    simulate_delete_charge,
    simulate_manual_confirm_package,
    simulate_reject_package,
    simulate_tag_package,
    simulate_update_confirmed_package_votes,
)
from app.services.supabase_service import fetch_project_status
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
from app.services.whatsapp_domain_service import WebhookIngestionService, SalesService
from app.workers.background_tasks import (
    backfill_missing_product_images,
    backfill_recent_drive_thumbnails,
    run_periodic_backfill,
)
from app.routers import customers as customers_router
from app.routers import portal as portal_router
from app.routers import dashboard as dashboard_router
import time
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator
from app.services.confirmation_pipeline import run_post_confirmation_effects
from app.services.manual_package_service import (
    build_manual_confirmed_package,
    build_preview_payload,
    create_manual_package_in_supabase,
)
from images.thumbs import (
    drive_export_view_url,
    ensure_thumbnail_for_image_url,
)
from app.locks import finance_lock, refresh_lock, packages_lock, estoque_lock

# basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("raylook")


class ConfirmedPackageUpdateRequest(BaseModel):
    votes: List[Dict[str, Any]] = Field(default_factory=list)
    confirm_paid_removal: bool = False


class PackageTagRequest(BaseModel):
    tag: Optional[str] = None


PHONE_BR_RE = re.compile(r"^55\d{10,11}$")


MANUAL_ALLOWED_QTY = {3, 6, 9, 12, 24}


class VoteLineCreate(BaseModel):
    qty: int = Field(ge=1, le=24)
    phone: str = Field(..., min_length=1)

    @field_validator("qty")
    @classmethod
    def qty_must_be_allowed(cls, v: int) -> int:
        if v not in MANUAL_ALLOWED_QTY:
            raise ValueError("Quantidade deve ser uma das opções: 3, 6, 9, 12 ou 24.")
        return v

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", (v or "").strip())
        if not PHONE_BR_RE.match(digits):
            raise ValueError("Celular deve estar no formato 55 + DDD + nÃºmero (10 ou 11 dÃ­gitos).")
        return digits


class CreatePackageManualRequest(BaseModel):
    pollId: str = Field(..., min_length=1)
    votes: List[VoteLineCreate] = Field(..., min_length=1)


class LogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Filtra logs de arquivos estÃ¡ticos, health check e mÃ©tricas para limpar o terminal
        return not any(x in msg for x in ["/static/", "/health", "GET /metrics"])

logging.getLogger("uvicorn.access").addFilter(LogFilter())

# Metrics: read from Postgres via Supabase REST (PostgREST)
# or from cached dashboard_metrics.json

app = FastAPI(title="Raylook Dashboard API")

# Request ID + timing middleware
@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    start = time.time()
    logger.info("start request %s %s request_id=%s", request.method, request.url.path, request_id)
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    logger.info("finished request %s %s status=%s request_id=%s duration_ms=%d",
                request.method, request.url.path, response.status_code, request_id, duration_ms)
    return response


# Content Security Policy middleware
@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    response = await call_next(request)
    # basic CSP - adjust sources as needed
    csp = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https: data:; "
        "img-src 'self' data: https://drive.google.com https://drive.usercontent.google.com https://*.googleusercontent.com; "
        "connect-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com;"
    )
    response.headers["Content-Security-Policy"] = csp
    return response
# Setup templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/files/{file_id}")
async def serve_local_file(file_id: str):
    """Serve imagens armazenadas pelo LocalImageStorage.

    Substitui as URLs lh3.googleusercontent.com/d/<id> do Drive em sandbox.
    Sem auth — espelha o "anyone with link" original.
    """
    from fastapi.responses import FileResponse, JSONResponse
    from integrations.local_storage import LocalImageStorage

    resolved = LocalImageStorage().resolve_file_path(file_id)
    if not resolved:
        return JSONResponse({"error": "not_found"}, status_code=404)
    path, mime = resolved
    return FileResponse(path, media_type=mime)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip compression for JSON/HTML responses (F-028).
# Kicks in only for responses >= 1KB to avoid overhead on tiny replies.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# F-009 fix: HTTP Basic Auth middleware para rotas administrativas.
#
# Ativado via env var DASHBOARD_AUTH_ENABLED=true. Usa:
#   DASHBOARD_AUTH_USER  (default: "admin")
#   DASHBOARD_AUTH_PASS  (obrigatório se DASHBOARD_AUTH_ENABLED=true)
#
# Rotas SEMPRE PÚBLICAS (não exigem auth mesmo com o middleware ativo):
#   - /health, /api/supabase/health   (monitoring)
#   - /webhook/whatsapp                (webhook externo, tem secret próprio)
#   - /static/*                        (assets do dash)
#
# Default: DESLIGADO (backward compatible). Pra ligar depois:
#   docker service update --env-add DASHBOARD_AUTH_ENABLED=true \
#     --env-add DASHBOARD_AUTH_USER=admin \
#     --env-add DASHBOARD_AUTH_PASS=... \
#     alana-staging_alana-dashboard
import base64 as _b64_auth
import hmac as _hmac_auth

_AUTH_PUBLIC_PREFIXES = (
    "/health",
    "/api/supabase/health",
    "/webhook",   # cobre /webhook (WHAPI raylook) e /webhook/whatsapp (legado)
    "/static/",
    "/files/",   # LocalImageStorage — espelha "anyone with link" do Drive original
    "/metrics",  # prometheus
    "/portal",   # portal do cliente (auth próprio via sessão)
)


_DASH_PASSWORD = os.getenv("DASHBOARD_AUTH_PASS", "R@ylook")
_DASH_COOKIE = "dash_session"
_DASH_TOKEN = _hmac_auth.new(_DASH_PASSWORD.encode(), b"alana-dash-session", "sha256").hexdigest()


@app.get("/login", response_class=HTMLResponse)
async def dash_login_page(request: Request):
    # Se já tem sessão válida, redireciona pro dashboard
    if request.cookies.get(_DASH_COOKIE) == _DASH_TOKEN:
        return RedirectResponse("/", status_code=302)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(request, "dash_login.html", {"error": error})


@app.post("/login")
async def dash_login_submit(request: Request):
    form = await request.form()
    password = str(form.get("password", "")).strip()
    if _hmac_auth.compare_digest(password, _DASH_PASSWORD):
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(
            key=_DASH_COOKIE,
            value=_DASH_TOKEN,
            max_age=90 * 24 * 3600,  # 90 dias
            httponly=True,
            secure=os.getenv("PORTAL_SECURE_COOKIES", "true").lower() in ("true", "1", "yes"),
            samesite="lax",
            path="/",
        )
        return resp
    return RedirectResponse("/login?error=1", status_code=302)


@app.get("/logout")
async def dash_logout(request: Request):
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(_DASH_COOKIE, path="/")
    return resp


@app.middleware("http")
async def dashboard_auth_middleware(request: Request, call_next):
    # Bypass pra testes automatizados e ambientes que não precisam de auth
    if os.getenv("DASHBOARD_AUTH_DISABLED", "").strip().lower() in ("true", "1", "yes"):
        return await call_next(request)

    path = request.url.path or "/"

    # Rotas públicas (webhooks, portal, static, health, login)
    if any(path == p or path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)
    if path in ("/login", "/logout"):
        return await call_next(request)

    # Verificar cookie de sessão
    if request.cookies.get(_DASH_COOKIE) == _DASH_TOKEN:
        return await call_next(request)

    # API calls retornam 401 JSON, páginas redirecionam pro login
    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return RedirectResponse("/login", status_code=302)

app.include_router(customers_router.router)
app.include_router(portal_router.router)
app.include_router(dashboard_router.router)
if settings.ADHOC_PACKAGES_ENABLED:
    from app.api import adhoc_packages as adhoc_packages_api
    app.include_router(adhoc_packages_api.router)

METRICS_FILE = str(Path(os.environ.get("DATA_DIR", "data")) / "dashboard_metrics.json")
_refresh_lock = None
_packages_lock = None
_webhook_postprocess_task = None


def _get_lock():
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = refresh_lock
    return _refresh_lock


def _get_packages_lock():
    global _packages_lock
    if _packages_lock is None:
        _packages_lock = packages_lock
    return _packages_lock


def _is_supabase_metrics_mode() -> bool:
    return str(settings.METRICS_SOURCE or "").strip().lower() == "supabase"


def _should_async_webhook_postprocess() -> bool:
    return bool(_is_supabase_metrics_mode() and supabase_domain_enabled() and settings.TEST_MODE)


def _latest_monitored_enquete_ts() -> Optional[datetime]:
    if not (_is_supabase_metrics_mode() and supabase_domain_enabled()):
        return None

    filters = []
    target_chat_ids = monitored_chat_ids()
    if len(target_chat_ids) == 1:
        filters.append(("chat_id", "eq", target_chat_ids[0]))
    elif target_chat_ids:
        filters.append(("chat_id", "in", target_chat_ids))

    row = SupabaseRestClient.from_settings().select(
        "enquetes",
        columns="created_at_provider,created_at",
        filters=filters,
        order="created_at_provider.desc",
        single=True,
    )
    if not isinstance(row, dict):
        return None
    return processors.parse_timestamp(row.get("created_at_provider") or row.get("created_at"))


def _supabase_metrics_snapshot_is_stale(data: Dict[str, Any]) -> bool:
    generated_at = processors.parse_timestamp((data or {}).get("generated_at"))
    if generated_at is None:
        return True

    latest_poll_ts = _latest_monitored_enquete_ts()
    if latest_poll_ts and latest_poll_ts > generated_at:
        return True

    if settings.TEST_MODE:
        max_age_seconds = max(int(os.getenv("TEST_MODE_METRICS_MAX_AGE_SECONDS", "15") or 15), 0)
        current_ts = processors.get_date_range().get("now") or datetime.now()
        if (current_ts - generated_at).total_seconds() > max_age_seconds:
            return True

    return False


def _metrics_snapshot_version() -> str:
    try:
        path = Path(METRICS_FILE)
        if path.exists():
            return str(path.stat().st_mtime_ns)
    except Exception:
        logger.exception("Failed to read metrics snapshot version")
    return ""


def _dashboard_stream_versions() -> Dict[str, str]:
    metadata = load_runtime_state_metadata(
        [
            DASHBOARD_METRICS_STATE_KEY,
            FINANCE_CHARGES_STATE_KEY,
            FINANCE_STATS_STATE_KEY,
            CUSTOMER_ROWS_STATE_KEY,
        ]
    )

    dashboard_version = (
        str((metadata.get(DASHBOARD_METRICS_STATE_KEY) or {}).get("updated_at") or "").strip()
        or _metrics_snapshot_version()
    )
    finance_version = max(
        str((metadata.get(FINANCE_CHARGES_STATE_KEY) or {}).get("updated_at") or "").strip(),
        str((metadata.get(FINANCE_STATS_STATE_KEY) or {}).get("updated_at") or "").strip(),
    )
    customer_version = str((metadata.get(CUSTOMER_ROWS_STATE_KEY) or {}).get("updated_at") or "").strip()

    return {
        "dashboard": dashboard_version,
        "finance": finance_version,
        "customers": customer_version,
    }


def _encode_sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _run_webhook_postprocess() -> None:
    global _webhook_postprocess_task
    try:
        # Métricas PRIMEIRO (gráfico/dash atualiza imediato via SSE)
        async with _get_lock():
            await generate_and_persist_metrics()
        await asyncio.to_thread(refresh_finance_dashboard_stats)
        await asyncio.to_thread(refresh_customer_rows_snapshot)
        # Backfill de imagens depois (pode demorar)
        image_backfill = await backfill_missing_product_images(wait_for_completion=True, limit=10)
        logger.info("webhook image backfill result: %s", image_backfill)
        thumb_backfill = await backfill_recent_drive_thumbnails(wait_for_completion=True, limit=60)
        logger.info("webhook thumbnail backfill result: %s", thumb_backfill)
    except Exception:
        logger.exception("webhook postprocess failed")
    finally:
        _webhook_postprocess_task = None


def _schedule_webhook_postprocess() -> bool:
    global _webhook_postprocess_task
    task = _webhook_postprocess_task
    if task is not None and not task.done():
        logger.info("webhook postprocess already running; coalescing request")
        return False
    _webhook_postprocess_task = asyncio.create_task(_run_webhook_postprocess())
    return True


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except Exception:
        return False


def _resolve_supabase_package_id_or_none(pkg_id: str) -> Optional[str]:
    if not supabase_domain_enabled():
        return None
    if _is_uuid(pkg_id):
        return str(pkg_id)
    try:
        return resolve_supabase_package_id(pkg_id)
    except Exception as exc:
        logger.warning("Failed to resolve package id=%s for staging Supabase path: %s", pkg_id, exc)
        return None


def _find_package_in_metrics(data: Dict[str, Any], *candidates: Optional[str]) -> Optional[Dict[str, Any]]:
    """Busca um pacote nas métricas por id legacy, id UUID ou source_package_id.

    F-034: a ordem de iteração importa porque `_legacy_package_id` pode
    gerar colisão entre o pacote `open` slot (sequence_no=0) e o primeiro
    pacote fechado (sequence_no=1) — ambos viram id `{poll}_0`. Para
    confirmação de pacotes manuais, queremos **sempre** o pacote real
    (closed/approved) antes do slot open, então iteramos primeiro pelas
    seções de pacotes fechados/aprovados e por último pelo open.

    Também: se algum dos candidates é UUID, preferimos match por
    `source_package_id` ao invés do legacy id, porque UUIDs não colidem.
    """
    wanted = {str(value) for value in candidates if value}
    if not wanted:
        return None
    packages = data.get("votos", {}).get("packages", {}) or {}

    # 1ª passada: match exato por source_package_id (UUID) — zero ambiguidade
    for section in ("confirmed_today", "closed_today", "closed_week", "open"):
        for pkg in packages.get(section, []) or []:
            source_id = str(pkg.get("source_package_id") or "")
            if source_id and source_id in wanted:
                return pkg

    # 2ª passada: fallback por legacy id, na ordem confirmed → closed → open
    # (open por último pra evitar colisão com o primeiro closed — ver F-034)
    for section in ("confirmed_today", "closed_today", "closed_week", "open"):
        for pkg in packages.get(section, []) or []:
            pkg_id = str(pkg.get("id") or "")
            if pkg_id and pkg_id in wanted:
                return pkg
    return None


async def _load_dashboard_data_for_response() -> Dict[str, Any]:
    try:
        from app.services.metrics_service import load_metrics as service_load_metrics

        data = await asyncio.to_thread(service_load_metrics)
    except FileNotFoundError:
        lock = _get_lock()
        async with lock:
            data = await generate_and_persist_metrics()
    data["customers_map"] = load_customers()
    pkgs = data.get("votos", {}).get("packages")
    if isinstance(pkgs, dict):
        pkgs.setdefault("confirmed_today", [])
    return data


@app.on_event("startup")
async def startup_backfill_routing_once():
    # F-035: garantir que o state tem os locks compartilhados (alguns
    # endpoints em app/routers/packages.py leem request.app.state.packages_lock).
    try:
        app.state.refresh_lock = refresh_lock
        app.state.packages_lock = packages_lock
    except Exception:
        logger.exception("startup: falha setando app.state locks")

    # F-035: worker de fila de cobrança WhatsApp DESATIVADO.
    # Cobrança agora é feita pelo portal do cliente (/portal/pedidos).
    # O payment_queue_service não é mais necessário para envio.

    # Sincronizador periódico Asaas (a cada 10min) — garantia caso webhook falhe
    try:
        from app.services.asaas_sync_service import start_asaas_sync_scheduler
        asyncio.create_task(start_asaas_sync_scheduler(interval_minutes=10))
        logger.info("startup: agendador de sincronização Asaas iniciado (10min)")
    except Exception as exc:
        logger.warning("startup: falha iniciando Asaas sync scheduler: %s", exc)

    # Reconciliador periódico de votos via WHAPI (a cada 10min)
    # Garante que votos não entregues via webhook sejam capturados
    try:
        from app.services.poll_reconcile_service import start_poll_reconcile_scheduler
        start_poll_reconcile_scheduler()
        logger.info("startup: reconciliador de votos WHAPI iniciado (10min)")
    except Exception as exc:
        logger.warning("startup: falha iniciando poll reconcile scheduler: %s", exc)

    # Sincronizador periódico de status de pagamento MercadoPago (legacy)
    try:
        from app.services.payment_sync_service import start_payment_sync_scheduler
        asyncio.create_task(start_payment_sync_scheduler(interval_minutes=15))
        logger.info("startup: agendador de sincronização MercadoPago iniciado")
    except Exception as exc:
        logger.warning("startup: falha iniciando payment sync scheduler: %s", exc)

    data = None
    metrics_missing = False
    try:
        data = _load_metrics()
    except HTTPException as exc:
        if exc.status_code == 404:
            metrics_missing = True
            logger.info("startup backfill skipped: metrics file not found")
        else:
            logger.warning("startup backfill skipped: unable to load metrics (%s)", exc.detail)
    except Exception as exc:
        logger.warning("startup backfill skipped: unexpected load error: %s", exc)

    if data is not None:
        try:
            chat_map = await asyncio.to_thread(load_poll_chat_map)
            result = await asyncio.to_thread(backfill_metrics_routing, data, chat_map)
            if result.get("updated", 0) > 0:
                _save_metrics(data)
            logger.info("startup routing backfill result: %s", result)
        except Exception as exc:
            logger.warning("startup backfill failed: %s", exc)

    if _is_supabase_metrics_mode():
        if metrics_missing:
            try:
                async with _get_lock():
                    await generate_and_persist_metrics()
                logger.info("startup metrics bootstrap completed")
            except Exception as exc:
                logger.warning("startup metrics bootstrap failed: %s", exc)
        try:
            asyncio.create_task(run_periodic_backfill())
            logger.info("startup image backfill task started")
        except Exception as exc:
            logger.warning("startup image backfill failed to start: %s", exc)

        # F-003 fix: worker que recupera webhook_inbox parados em status='received'
        try:
            from app.workers.webhook_retry import webhook_retry_loop
            asyncio.create_task(webhook_retry_loop())
            logger.info("startup webhook_retry worker started")
        except Exception as exc:
            logger.warning("startup webhook_retry worker failed to start: %s", exc)

        # F-045: worker que tira snapshot horário dos KPIs do dash pra histórico imutável
        try:
            from app.workers.metrics_snapshot_worker import metrics_snapshot_loop
            asyncio.create_task(metrics_snapshot_loop())
            logger.info("startup metrics_snapshot worker started")
        except Exception as exc:
            logger.warning("startup metrics_snapshot worker failed to start: %s", exc)

        # F-052: limpeza diária de pastas antigas e duplicadas no Google Drive
        try:
            from app.workers.drive_cleanup_worker import drive_cleanup_loop
            asyncio.create_task(drive_cleanup_loop())
            logger.info("startup drive_cleanup worker started")
        except Exception as exc:
            logger.warning("startup drive_cleanup worker failed to start: %s", exc)

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker/Traefik."""
    return {"status": "ok"}


@app.get("/api/supabase/health")
async def supabase_health_check():
    try:
        project = await asyncio.to_thread(fetch_project_status)
        return {"status": "ok", "project": project}
    except RuntimeError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})
    except Exception:
        logger.exception("Failed to check Supabase health")
        raise HTTPException(status_code=502, detail="Failed to connect to Supabase")


@app.api_route("/webhook", methods=["POST", "PATCH"])
@app.api_route("/webhook/whatsapp", methods=["POST", "PATCH"])
async def whatsapp_webhook(request: Request):
    if not settings.WHATSAPP_WEBHOOK_ENABLED:
        raise HTTPException(status_code=503, detail="Webhook disabled")

    secret = (settings.WHATSAPP_WEBHOOK_SECRET or "").strip()
    # F-010: se WHATSAPP_WEBHOOK_SECRET_REQUIRED=true e secret vazio,
    # fail-closed (não aceita requests). Permite ativar depois de
    # configurar o secret no provider (WHAPI) em dois passos seguros.
    secret_required = os.getenv("WHATSAPP_WEBHOOK_SECRET_REQUIRED", "").strip().lower() in ("true", "1", "yes")
    if not secret:
        if secret_required:
            logger.error(
                "F-010: webhook rejeitado — WHATSAPP_WEBHOOK_SECRET_REQUIRED=true "
                "mas WHATSAPP_WEBHOOK_SECRET não está configurado"
            )
            raise HTTPException(
                status_code=503,
                detail="Webhook secret required but not configured",
            )
        # Aviso alto em cada request pra forçar visibilidade do problema
        logger.warning(
            "F-010: webhook aceito SEM autenticação (WHATSAPP_WEBHOOK_SECRET vazio). "
            "Configure o secret na WHAPI e no env do serviço, depois ligue "
            "WHATSAPP_WEBHOOK_SECRET_REQUIRED=true para fail-closed."
        )
    else:
        # Aceita o secret em vários formatos pra ser compatível com diferentes
        # provedores de webhook (WHAPI envia Authorization: Bearer; Evolution
        # envia x-webhook-secret; configs antigas usam ?secret=).
        auth_header = (request.headers.get("authorization") or "").strip()
        if auth_header.lower().startswith("bearer "):
            auth_header = auth_header[7:].strip()
        candidate = (
            auth_header
            or request.headers.get("x-webhook-secret")
            or request.headers.get("x-api-key")
            or request.query_params.get("secret")
            or ""
        ).strip()
        if candidate != secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase domain disabled.")

    try:
        result = await asyncio.to_thread(
            WebhookIngestionService(SupabaseRestClient.from_settings()).ingest,
            payload,
        )
        if result.get("processed") and _is_supabase_metrics_mode():
            if _should_async_webhook_postprocess():
                scheduled = _schedule_webhook_postprocess()
                return {
                    "status": "accepted",
                    "result": result,
                    "postprocess": "scheduled" if scheduled else "coalesced",
                }

            image_backfill = await backfill_missing_product_images(wait_for_completion=True, limit=10)
            logger.info("webhook image backfill result: %s", image_backfill)
            async with _get_lock():
                await generate_and_persist_metrics()
            await asyncio.to_thread(refresh_finance_dashboard_stats)
            await asyncio.to_thread(refresh_customer_rows_snapshot)
        return {"status": "accepted", "result": result}
    except Exception as exc:
        logger.exception("whatsapp webhook processing failed")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {exc}")


@app.get("/api/reconcile/supabase-baserow")
async def reconcile_supabase_baserow():
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase domain disabled.")
    try:
        sb = SupabaseRestClient.from_settings()
        enquetes_sb = sb.select("enquetes", columns="id")
        votos_sb = sb.select("votos", columns="id")
        pacotes_sb = sb.select("pacotes", columns="id")
        vendas_sb = sb.select("vendas", columns="id")
        pagamentos_sb = sb.select("pagamentos", columns="id")
    except Exception as exc:
        logger.exception("reconcile failed")
        raise HTTPException(status_code=502, detail=f"Reconcile failed: {exc}")

    supabase_counts = {
        "enquetes": len(enquetes_sb) if isinstance(enquetes_sb, list) else 0,
        "votos": len(votos_sb) if isinstance(votos_sb, list) else 0,
        "pacotes": len(pacotes_sb) if isinstance(pacotes_sb, list) else 0,
        "vendas": len(vendas_sb) if isinstance(vendas_sb, list) else 0,
        "pagamentos": len(pagamentos_sb) if isinstance(pagamentos_sb, list) else 0,
    }
    return {"status": "ok", "supabase": supabase_counts, "baserow_comparison": "disabled_in_staging"}

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Dashboard atual — layout combinado dos mockups (rail vertical + split lista/detalhe).
    return templates.TemplateResponse(request, "dashboard_v2.html", {"settings": settings})


@app.get("/v1", response_class=HTMLResponse)
async def read_root_v1(request: Request):
    # Dashboard antigo (cards de KPI + listas) — mantido como /v1 enquanto o v2 valida.
    import hashlib, time as _time
    cache_bust = hashlib.md5(str(int(_time.time()) // 300).encode()).hexdigest()[:8]
    return templates.TemplateResponse(request, "index.html", {"settings": settings, "cache_bust": cache_bust})

@app.get("/api/metrics/history")
async def get_metrics_history(
    hours: int = 48,
    from_ts: str | None = None,
    to_ts: str | None = None,
):
    """F-045: histórico imutável de KPIs hora-a-hora.

    Lê da tabela `metrics_hourly_snapshots` (snapshots append-only gravados
    pelo worker `metrics_snapshot_worker` a cada hora cheia).

    Parâmetros:
    - `hours`: quantas horas pra trás a partir de agora (default 48, max 720=30d)
    - `from_ts` / `to_ts`: ISO timestamps opcionais (sobrescrevem `hours`)
    """
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Histórico só disponível no modo Supabase")

    from datetime import datetime, timedelta, timezone
    from app.services.supabase_service import SupabaseRestClient

    safe_hours = max(1, min(int(hours or 48), 720))
    now_utc = datetime.now(timezone.utc)

    if from_ts:
        try:
            start = datetime.fromisoformat(from_ts.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="from_ts inválido (use ISO 8601)")
    else:
        start = now_utc - timedelta(hours=safe_hours)

    if to_ts:
        try:
            end = datetime.fromisoformat(to_ts.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="to_ts inválido (use ISO 8601)")
    else:
        end = now_utc

    sb = SupabaseRestClient.from_settings()
    try:
        rows = sb.select(
            "metrics_hourly_snapshots",
            columns=(
                "id,hour_bucket,captured_at,"
                "votes_today_so_far,votes_last_24h,votes_hour_delta,"
                "enquetes_total,enquetes_open,enquetes_closed,enquetes_created_today,"
                "pacotes_open,pacotes_closed,pacotes_approved,pacotes_cancelled,pacotes_approved_today,"
                "total_pending_brl,total_paid_brl,total_paid_today_brl,total_cancelled_brl,"
                "pending_count,paid_count,cancelled_count,active_count,conversion_rate_pct,"
                "customers_total,customers_with_debt,"
                "queue_queued,queue_sending,queue_retry,queue_error,queue_sent,"
                "webhook_received,webhook_processed,webhook_failed"
            ),
            filters=[
                ("hour_bucket", "gte", start.isoformat()),
                ("hour_bucket", "lte", end.isoformat()),
            ],
            order="hour_bucket.asc",
            limit=1000,
        )
    except Exception as exc:
        logger.exception("Falha ao consultar metrics_hourly_snapshots")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    items = rows if isinstance(rows, list) else []
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(items),
        "items": items,
    }


@app.post("/api/metrics/snapshot")
async def force_metrics_snapshot():
    """F-045: força um snapshot agora (útil pra debug ou pra capturar antes
    de uma mudança grande). Idempotente no hour_bucket atual (upsert)."""
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Snapshot só disponível no modo Supabase")
    try:
        from app.workers.metrics_snapshot_worker import capture_once
        snap_id = await capture_once()
        return {"status": "success", "id": snap_id}
    except Exception as exc:
        logger.exception("Falha ao forçar snapshot")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/test-mode")
async def get_test_mode():
    """F-053: retorna status do modo de teste (enquete [ENQUETE DE TESTE])."""
    try:
        from app.services.test_mode_service import get_test_mode_status
        return await asyncio.to_thread(get_test_mode_status)
    except Exception as exc:
        logger.exception("Falha ao verificar test mode")
        return {"active": False, "label": None}


@app.post("/api/test-mode/toggle")
async def toggle_test_mode():
    """Liga/desliga modo de teste. Chamado pelo Easter egg no 'Criar Pacote'."""
    try:
        from app.services.test_mode_service import is_test_mode_active, set_test_mode
        current = await asyncio.to_thread(is_test_mode_active)
        result = await asyncio.to_thread(set_test_mode, not current)
        return {"status": "success", **result}
    except Exception as exc:
        logger.exception("Falha ao toggle test mode")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/drive/cleanup")
async def force_drive_cleanup():
    """F-052: força limpeza de pastas antigas/duplicadas no Google Drive.

    Remove pastas duplicadas (mesmo poll_id) e pastas de enquetes inativas
    com mais de 30 dias. Enquetes com status='open' são protegidas.
    """
    try:
        from app.workers.drive_cleanup_worker import cleanup_drive_once
        report = await cleanup_drive_once()
        return {"status": "success", "report": report}
    except Exception as exc:
        logger.exception("Falha no drive cleanup")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/metrics/temperature/refresh")
async def refresh_sales_temperature():
    """F-050: força o recálculo do termômetro de vendas (cache de 3h).

    Retorna o novo snapshot do termômetro. Usado pelo botãozinho de
    refresh ao lado do indicador no card Pacotes Confirmados (72h).
    """
    try:
        from app.services.sales_temperature_service import get_temperature
        data = await asyncio.to_thread(get_temperature, True)
        return {"status": "success", "temperature": data}
    except Exception as exc:
        logger.exception("Falha ao recalcular sales_temperature")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/metrics/health")
async def metrics_snapshot_health(window_hours: int = 24):
    """F-045b: saúde do worker de snapshots.

    Detecta gaps no histórico horário. Retorna `status`:
      - `ok`         → sem gaps no janela e snapshot recente (<2h)
      - `degraded`   → tem gaps mas o snapshot mais recente está fresco
      - `critical`   → snapshot mais recente é antigo (>2h) OU sem snapshots
    """
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Health só disponível no modo Supabase")

    from datetime import datetime, timedelta, timezone
    from app.services.supabase_service import SupabaseRestClient

    safe_window = max(1, min(int(window_hours or 24), 720))
    now_utc = datetime.now(timezone.utc)
    start = now_utc - timedelta(hours=safe_window)

    sb = SupabaseRestClient.from_settings()
    try:
        rows = sb.select(
            "metrics_hourly_snapshots",
            columns="hour_bucket,captured_at",
            filters=[("hour_bucket", "gte", start.isoformat())],
            order="hour_bucket.asc",
            limit=1000,
        )
    except Exception as exc:
        logger.exception("metrics health: falha ao consultar snapshots")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    items = rows if isinstance(rows, list) else []
    if not items:
        return {
            "status": "critical",
            "reason": "nenhum snapshot encontrado na janela",
            "window_hours": safe_window,
            "snapshot_count": 0,
            "expected_count": safe_window,
            "gaps": [],
            "last_snapshot_at": None,
            "minutes_since_last": None,
        }

    buckets = []
    for it in items:
        try:
            buckets.append(datetime.fromisoformat(str(it.get("hour_bucket")).replace("Z", "+00:00")))
        except Exception:
            continue

    gaps = []
    for i in range(1, len(buckets)):
        delta = buckets[i] - buckets[i - 1]
        if delta > timedelta(hours=1, minutes=5):
            missing = int(delta.total_seconds() // 3600) - 1
            gaps.append({
                "after": buckets[i - 1].isoformat(),
                "before": buckets[i].isoformat(),
                "missing_hours": missing,
            })

    last_bucket = buckets[-1]
    minutes_since_last = (now_utc - last_bucket).total_seconds() / 60.0
    expected = safe_window

    if minutes_since_last > 130:  # >2h sem snapshot
        status = "critical"
        reason = f"último snapshot há {minutes_since_last:.0f} min (esperado <60)"
    elif gaps:
        status = "degraded"
        reason = f"{len(gaps)} gap(s) detectado(s) na janela de {safe_window}h"
    else:
        status = "ok"
        reason = "sem gaps, worker saudável"

    return {
        "status": status,
        "reason": reason,
        "window_hours": safe_window,
        "snapshot_count": len(items),
        "expected_count": expected,
        "gaps": gaps,
        "last_snapshot_at": last_bucket.isoformat(),
        "minutes_since_last": round(minutes_since_last, 1),
    }


@app.get("/api/metrics", response_model=DashboardModel)
async def get_metrics():
    if _is_supabase_metrics_mode():
        data = None
        try:
            from app.services.metrics_service import load_metrics as service_load_metrics
            data = await asyncio.to_thread(service_load_metrics)
        except FileNotFoundError:
            logger.info("Supabase metrics snapshot missing; generating a new snapshot on demand")
        except Exception:
            logger.exception("Failed to load persisted Supabase metrics snapshot")

        if data is not None:
            try:
                if await asyncio.to_thread(_supabase_metrics_snapshot_is_stale, data):
                    logger.info("Supabase metrics snapshot is stale; regenerating on demand")
                    data = None
            except Exception:
                logger.exception("Failed to validate Supabase metrics snapshot freshness")

        if data is None:
            try:
                lock = _get_lock()
                async with lock:
                    data = await generate_and_persist_metrics()
            except Exception:
                logger.exception("Failed to generate Supabase metrics snapshot")
                raise HTTPException(status_code=500, detail="Failed to generate metrics from Supabase")

        data["customers_map"] = load_customers()
        pkgs = data.get("votos", {}).get("packages")
        if pkgs is not None:
            pkgs.setdefault("confirmed_today", [])
        return JSONResponse(content=data)

    try:
        data = _load_metrics()
        pkgs = data.get("votos", {}).get("packages")
        if pkgs is not None:
            pkgs.setdefault("confirmed_today", [])
        return JSONResponse(content=data)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Metrics file not found. Run dashboard.py first.")
    except Exception:
        logger.exception("Failed to read metrics file")
        raise HTTPException(status_code=500, detail="Failed to read metrics file")


@app.get("/api/stream/dashboard")
async def stream_dashboard(request: Request):
    async def event_generator():
        last_versions: Dict[str, str] | None = None
        last_keepalive_at = time.monotonic()

        while True:
            if await request.is_disconnected():
                break

            try:
                current_versions = await asyncio.to_thread(_dashboard_stream_versions)
            except Exception as exc:
                logger.exception("Falha ao ler versões do stream do dashboard")
                yield _encode_sse(
                    "error",
                    {
                        "detail": str(exc),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                )
                await asyncio.sleep(5)
                continue

            if last_versions is None:
                last_versions = current_versions
                yield _encode_sse(
                    "ready",
                    {
                        "state": current_versions,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                )
                last_keepalive_at = time.monotonic()
            else:
                changed = [
                    key
                    for key, version in current_versions.items()
                    if version and version != (last_versions or {}).get(key)
                ]
                if changed:
                    last_versions = current_versions
                    yield _encode_sse(
                        "update",
                        {
                            "changed": changed,
                            "state": current_versions,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    last_keepalive_at = time.monotonic()
                elif (time.monotonic() - last_keepalive_at) >= 15:
                    yield _encode_sse(
                        "ping",
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    last_keepalive_at = time.monotonic()

            await asyncio.sleep(2)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

@app.post("/api/refresh")
async def refresh_metrics():
    lock = _get_lock()
    if lock.locked():
        logger.info("refresh skipped: already running")
        try:
            data = _load_metrics()
        except HTTPException:
            data = {}
        return {"status": "busy", "detail": "Sincronizacao ja esta em andamento.", "data": data}

    try:
        async with lock:
            data = await generate_and_persist_metrics()
            if isinstance(data, dict) and _is_supabase_metrics_mode():
                await asyncio.to_thread(refresh_finance_dashboard_stats)
                await asyncio.to_thread(refresh_customer_rows_snapshot)
                data["customers_map"] = load_customers()
            else:
                try:
                    from app.services.payment_sync_service import sync_mercadopago_payments

                    updated_count = await sync_mercadopago_payments()
                    if updated_count > 0:
                        logger.info(
                            "Sincronização manual concluída: %s cobranças atualizadas para 'pago' via polling MP.",
                            updated_count,
                        )
                    else:
                        logger.info(
                            "Sincronização manual concluída: nenhum novo pagamento confirmado via polling MP."
                        )
                except Exception as exc:
                    logger.error("Erro na sincronização manual de pagamentos via polling: %s", exc, exc_info=True)
            return {"status": "success", "data": data}
    except Exception:
        logger.exception("Error refreshing metrics")
        raise HTTPException(status_code=500, detail="Error generating metrics")


def _row_created_ts_enquete(row: Dict[str, Any]) -> Optional[datetime]:
    ts = processors.parse_timestamp(row.get("createdAtTs", row.get("field_171")))
    if ts:
        return ts
    co = row.get("created_on")
    if co and isinstance(co, str):
        try:
            return datetime.fromisoformat(co.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


@app.get("/api/polls/recent")
async def get_recent_polls(limit: int = 20, offset: int = 0, search: str = ""):
    """Lista enquetes criadas nas Ãºltimas 72h, mais recentes primeiro (thumb local + tÃ­tulo).

    Paginado com `limit` e `offset` para carregar aos poucos. Thumbnails: mesmo pipeline que
    Pacotes Fechados (`ensure_thumbnail_for_image_url`), sem depender de refresh de mÃ©tricas.
    """
    cutoff = datetime.utcnow() - timedelta(hours=72)
    items: List[tuple] = []

    if supabase_domain_enabled():
        client = SupabaseRestClient.from_settings()
        try:
            rows = client.select_all(
                "enquetes",
                columns="id,external_poll_id,titulo,created_at_provider,drive_file_id,produto:produto_id(drive_file_id)",
                filters=[("created_at_provider", "gte", cutoff.isoformat())],
                order="created_at_provider.desc",
            )
        except Exception as e:
            logger.exception("get_recent_polls: falha ao buscar enquetes no Supabase")
            raise HTTPException(status_code=503, detail=str(e)) from e

        seen_poll_ids: set = set()
        for row in rows:
            ts = processors.parse_timestamp(row.get("created_at_provider"))
            if not ts or ts < cutoff:
                continue
            poll_id = row.get("external_poll_id")
            if not poll_id:
                continue
            seen_poll_ids.add(str(row.get("id") or ""))
            produto = row.get("produto") or {}
            # F-061: imagem da enquete vem primeiro; produto é fallback.
            drive_file_id = row.get("drive_file_id") or produto.get("drive_file_id")
            items.append((ts, str(poll_id), str(row.get("titulo") or ""), drive_file_id))

        # "Criar Pacote" mostra apenas enquetes das últimas 72h,
        # casando com o card "Enquetes Ativas".
    else:
        table_enquetes = os.getenv("BASEROW_TABLE_ENQUETES", settings.BASEROW_TABLE_ENQUETES)
        days_ago = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
        params = {"filter__created_on__date_after": days_ago}
        try:
            rows = clients.fetch_rows_filtered(table_enquetes, params, size=200)
        except Exception as e:
            logger.exception("get_recent_polls: falha ao buscar enquetes")
            raise HTTPException(status_code=503, detail=str(e)) from e

        for row in rows:
            ts = _row_created_ts_enquete(row)
            if not ts or ts < cutoff:
                continue
            poll_id = row.get("pollId", row.get("field_169"))
            if not poll_id:
                continue
            title = row.get("title", row.get("field_173", ""))
            drive_id = row.get("driveFileId", row.get("field_200", row.get("driveFileid")))
            items.append((ts, str(poll_id), str(title), drive_id))

    items.sort(key=lambda x: x[0], reverse=True)
    if search.strip():
        search_lower = search.strip().lower()
        items = [item for item in items if search_lower in item[2].lower()]
    total = len(items)
    page = items[offset : offset + max(1, min(limit, 100))]

    async def _thumb_row(row: tuple) -> Dict[str, Any]:
        ts, pid, title, drive_id = row
        thumb_url: Optional[str] = None
        if drive_id:
            img_url = drive_export_view_url(str(drive_id).strip())
            thumb_url = await asyncio.to_thread(ensure_thumbnail_for_image_url, img_url)
        return {
            "pollId": pid,
            "title": title,
            "thumbUrl": thumb_url,
            "createdAt": ts.isoformat(),
        }

    polls = await asyncio.gather(*[_thumb_row(r) for r in page]) if page else []
    return {
        "polls": list(polls),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
    }


@app.post("/api/packages/manual/preview")
async def manual_package_preview(body: CreatePackageManualRequest):
    """Preview do pacote manual (nomes por celular, totais) â€” nÃ£o persiste."""
    total_qty = sum(v.qty for v in body.votes)
    if total_qty != 24:
        raise HTTPException(
            status_code=400,
            detail="O pacote precisa ter exatamente 24 peças.",
        )
    try:
        preview = build_preview_payload(body.pollId, body.votes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "success", "preview": preview}


@app.post("/api/packages/manual/confirm")
async def manual_package_confirm(body: CreatePackageManualRequest):
    """Confirma pacote manual: vai direto para confirmados (sem Baserow), mesmo pipeline de PDF/financeiro."""
    total_qty = sum(v.qty for v in body.votes)
    if total_qty != 24:
        raise HTTPException(
            status_code=400,
            detail="O pacote precisa ter exatamente 24 peças.",
        )
    if is_staging_dry_run():
        try:
            moved = await asyncio.to_thread(build_manual_confirmed_package, body.pollId, body.votes)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        data = await _load_dashboard_data_for_response()
        simulated = simulate_manual_confirm_package(data, moved)
        simulated["customers_map"] = load_customers()
        return {
            "status": "success",
            "simulated": True,
            "mode": "staging_dry_run",
            "moved": {"to": "approved", "package": moved},
            "data": simulated,
        }
    if supabase_domain_enabled():
        try:
            result = await asyncio.to_thread(create_manual_package_in_supabase, body.pollId, body.votes)
            data = await generate_and_persist_metrics()
            data["customers_map"] = load_customers()
            return {
                "status": "success",
                "mode": "supabase",
                "moved": {"to": "closed", "package_id": result.get("package_id")},
                "result": result,
                "data": data,
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            logger.exception("manual_package_confirm: falha no fluxo Supabase")
            raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        moved = build_manual_confirmed_package(body.pollId, body.votes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    pkg_id = moved["id"]
    lock = packages_lock
    async with lock:
        await run_post_confirmation_effects(moved, pkg_id, metrics_data_to_save=None)

    try:
        from app.services.metrics_service import load_metrics as service_load_metrics

        merged_data = service_load_metrics()
    except FileNotFoundError:
        merged_data = {}

    return {
        "status": "success",
        "moved": {"to": "confirmed_packages", "package": moved},
        "data": merged_data,
    }


def _load_metrics() -> dict:
    """Load metrics snapshot. In Supabase mode, reads from runtime state."""
    if supabase_domain_enabled() and str(settings.METRICS_SOURCE or "").strip().lower() == "supabase":
        try:
            from app.services.metrics_service import load_metrics as service_load_metrics

            return service_load_metrics()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Supabase metrics snapshot not found.")
    if not os.path.exists(METRICS_FILE):
        raise HTTPException(status_code=404, detail="Metrics file not found.")
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.exception("Corrupt metrics file: %s", METRICS_FILE)
        raise HTTPException(status_code=500, detail="Metrics file is corrupt")


def _save_metrics(data: dict) -> None:
    """Persist metrics snapshot. In Supabase mode, writes to runtime state."""
    if supabase_domain_enabled() and str(settings.METRICS_SOURCE or "").strip().lower() == "supabase":
        from app.services.metrics_service import save_metrics as service_save_metrics

        service_save_metrics(data)
        return
    tmp_path = METRICS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        os.replace(tmp_path, METRICS_FILE)
    except Exception:
        # fallback to overwrite
        with open(METRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _extract_poll_id_from_package(pkg: Dict[str, Any]) -> str:
    poll_id = str(pkg.get("poll_id") or "").strip()
    if poll_id:
        return poll_id
    pkg_id = str(pkg.get("id") or "")
    if "_" in pkg_id:
        return pkg_id.rsplit("_", 1)[0]
    return pkg_id


def _fetch_active_votes_for_poll_supabase(poll_id: str) -> List[Dict[str, Any]]:
    """Fetch active votes for a poll from Supabase/PostgREST.

    Resolves external_poll_id -> enquete_id -> votos (status=in, qty>0),
    then joins with clientes for name and phone.
    """
    try:
        sb = SupabaseRestClient.from_settings()

        resp = sb._request("GET", f"/rest/v1/enquetes?external_poll_id=eq.{poll_id}&select=id&limit=1")
        enquetes = resp.json() if resp.status_code == 200 else []
        if not enquetes:
            logger.info("Supabase: no enquete found for external_poll_id=%s", poll_id)
            return []
        enquete_id = enquetes[0]["id"]

        resp = sb._request("GET", f"/rest/v1/votos?enquete_id=eq.{enquete_id}&status=eq.in&qty=gt.0&select=id,cliente_id,qty")
        votos = resp.json() if resp.status_code == 200 else []
        if not votos:
            logger.info("Supabase: no active votes for enquete_id=%s", enquete_id)
            return []

        cliente_ids = list({v["cliente_id"] for v in votos if v.get("cliente_id")})
        clientes_map: Dict[str, Dict[str, Any]] = {}
        if cliente_ids:
            ids_csv = ",".join(cliente_ids)
            resp = sb._request("GET", f"/rest/v1/clientes?id=in.({ids_csv})&select=id,nome,celular")
            for c in (resp.json() if resp.status_code == 200 else []):
                clientes_map[c["id"]] = c

        active_votes: List[Dict[str, Any]] = []
        for v in votos:
            qty = int(v.get("qty", 0))
            if qty <= 0:
                continue
            cliente = clientes_map.get(v.get("cliente_id"), {})
            phone = "".join(ch for ch in str(cliente.get("celular", "")) if ch.isdigit())
            if not phone:
                continue
            name = str(cliente.get("nome", "Cliente")).strip() or "Cliente"
            active_votes.append({"phone": phone, "name": name, "qty": qty})

        logger.info("Supabase: found %d active votes for poll %s (enquete %s)", len(active_votes), poll_id, enquete_id)
        return active_votes
    except Exception:
        logger.exception("Failed to fetch active votes from Supabase for poll_id=%s", poll_id)
        return []


def _fetch_active_votes_for_poll(poll_id: str) -> List[Dict[str, Any]]:
    metrics_source = str(settings.METRICS_SOURCE or os.getenv("METRICS_SOURCE", "")).strip().lower()
    if metrics_source == "supabase" and supabase_domain_enabled():
        return _fetch_active_votes_for_poll_supabase(poll_id)

    from metrics import clients, processors

    table_votos = os.getenv("BASEROW_TABLE_VOTOS", settings.BASEROW_TABLE_VOTOS)
    if not table_votos:
        return []

    rows: List[Dict[str, Any]] = []
    filters_to_try = [
        {"filter__pollId__equal": poll_id},
        {"filter__field_158__equal": poll_id},
    ]

    for query in filters_to_try:
        try:
            fetched = clients.fetch_rows_filtered(table_votos, query, size=200)
            rows.extend(fetched or [])
        except Exception:
            logger.exception("Falha ao buscar votos da enquete %s com filtro %s", poll_id, query)

    dedup_by_id: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        rid = row.get("id")
        if rid is not None:
            dedup_by_id[rid] = row
    unique_rows = list(dedup_by_id.values()) if dedup_by_id else rows

    processor = processors.VoteProcessor()
    parse_ts = processors.parse_timestamp

    def _sort_key(v: Dict[str, Any]):
        ts = parse_ts(v.get("timestamp", v.get("field_166")))
        if not ts:
            ts = datetime.min
        try:
            rid = int(v.get("id", 0))
        except Exception:
            rid = 0
        return (ts, rid)

    for vote in sorted(unique_rows, key=_sort_key):
        processor.process_vote(vote)

    poll_votes = processor.poll_votes.get(poll_id, {})
    active_votes: List[Dict[str, Any]] = []
    for _, vote_list in poll_votes.items():
        if not vote_list:
            continue
        v = vote_list[-1]
        try:
            qty = int(v.get("parsed_qty", v.get("qty", 0)))
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        phone = str(v.get("voterPhone", v.get("field_160", "")))
        phone_clean = "".join(ch for ch in phone if ch.isdigit())
        if not phone_clean:
            continue
        name = str(v.get("voterName", v.get("field_161", "Cliente"))).strip() or "Cliente"
        active_votes.append({"phone": phone_clean, "name": name, "qty": qty})

    return active_votes


def _clean_item_name(raw_item_name: Any) -> str:
    import re

    item_name = str(raw_item_name or "PeÃ§a")
    item_name = re.sub(r"(?:R\$\s*|\$\s*)?\d+(?:[.,]\d{1,2})?", "", item_name)
    item_name = re.sub(r"[ðŸ”¥ðŸŽ¯ðŸ“¦ðŸ’•âœ¨âœ…ðŸ’°ðŸ‘‡]", "", item_name)
    item_name = re.sub(r"\s+", " ", item_name).strip()
    return item_name or "PeÃ§a"


def _enqueue_charge_job(charge: Dict[str, Any], package: Dict[str, Any]) -> None:
    # No-op: envio de WhatsApp foi removido. Cliente vê cobrança via /portal/pedidos.
    return


@app.get("/api/packages/{pkg_id}/pdf")
async def download_package_pdf_by_package_id(pkg_id: str):
    """
    Gera o PDF da etiqueta on-demand (mesma lÃ³gica do worker de confirmaÃ§Ã£o) e devolve o ficheiro.
    NÃ£o envia WhatsApp nem grava em disco para este pedido â€” sÃ³ resposta HTTP com o PDF.
    """
    from estoque.pdf_builder import build_pdf
    from finance.utils import get_pdf_filename_by_id

    from app.services.confirmed_packages_service import get_confirmed_package
    from app.services.package_state_service import load_package_states

    # Always check confirmed_packages first — it has the latest edited votes
    confirmed_pkg = get_confirmed_package(pkg_id)

    resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
    if resolved_pkg_id:
        if confirmed_pkg:
            # Use confirmed_packages as source of truth for votes (reflects edits)
            pkg = copy.deepcopy(confirmed_pkg)
        else:
            # Try cached metrics first, then fall back to charge contexts — never call generate_metrics
            pkg = None
            try:
                cached_data = _load_metrics()
                pkg = _find_package_in_metrics(cached_data, pkg_id, resolved_pkg_id)
            except Exception:
                pass
            if not pkg:
                contexts = await asyncio.to_thread(get_package_charge_contexts, resolved_pkg_id)
                if not contexts:
                    raise HTTPException(status_code=404, detail="Pacote aprovado não encontrado no Supabase.")
                qty_total = sum(int(item.get("quantity") or 0) for item in contexts)
                pkg = {
                    "id": pkg_id,
                    "source_package_id": resolved_pkg_id,
                    "poll_title": contexts[0].get("poll_title"),
                    "image": contexts[0].get("image"),
                    "qty": qty_total,
                    "votes": [
                        {
                            "name": item.get("customer_name"),
                            "phone": item.get("customer_phone"),
                            "qty": int(item.get("quantity") or 0),
                        }
                        for item in contexts
                    ],
                }
    else:
        raw = confirmed_pkg
        if not raw:
            raise HTTPException(status_code=404, detail="Pacote confirmado não encontrado.")

        pkg = copy.deepcopy(raw)
        states = load_package_states()
        st = states.get(pkg_id) or {}
        for key, value in st.items():
            if key != "votes":
                pkg[key] = value
        if "votes" in st and "votes" in pkg:
            votes_list = pkg["votes"]
            for idx_str, vote_update in st["votes"].items():
                try:
                    idx = int(idx_str)
                    if 0 <= idx < len(votes_list):
                        votes_list[idx].update(vote_update)
                except (ValueError, IndexError):
                    continue

    try:
        pdf_bytes = await asyncio.to_thread(build_pdf, pkg, settings.COMMISSION_PERCENT)
    except Exception as e:
        logger.exception("Falha ao gerar PDF on-demand para pkg=%s", pkg_id)
        raise HTTPException(status_code=500, detail="Erro ao gerar PDF da etiqueta.") from e

    filename = get_pdf_filename_by_id(pkg_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/packages/{pkg_id}/confirm")
async def confirm_package(pkg_id: str, request: Request):
    """Move package from closed_today to confirmed_packages using action object."""
    lock = packages_lock
    async with lock:
        # Tag opcional via JSON body: {"tag": "..."}
        tag_value: Optional[str] = None
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("tag", None) is not None:
                tag_value = str(body.get("tag"))
        except Exception:
            tag_value = None

        if is_staging_dry_run():
            resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
            data = await _load_dashboard_data_for_response()
            simulated, moved = simulate_confirm_package(
                data,
                pkg_id,
                source_package_id=resolved_pkg_id,
                tag=tag_value,
            )
            return {
                "status": "success",
                "simulated": True,
                "mode": "staging_dry_run",
                "moved": {
                    "from": "closed",
                    "to": "approved",
                    "package_id": pkg_id,
                    "source_package_id": resolved_pkg_id,
                    "package": moved,
                },
                "result": {"simulated": True},
                "data": simulated,
            }

        resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
        if resolved_pkg_id:
            try:
                sales = SalesService(SupabaseRestClient.from_settings())
                result = await asyncio.to_thread(sales.approve_package, resolved_pkg_id)
                if tag_value is not None:
                    try:
                        from app.services.package_state_service import update_package_state

                        update_package_state(resolved_pkg_id, {"tag": tag_value})
                    except Exception as exc:
                        logger.warning("Falha ao persistir tag do pacote aprovado %s: %s", resolved_pkg_id, exc)
                data = await generate_and_persist_metrics()
                data["customers_map"] = load_customers()
                moved = _find_package_in_metrics(data, pkg_id, resolved_pkg_id)
                if moved and tag_value is not None:
                    moved["tag"] = tag_value
                if moved:
                    from app.workers.background import pdf_worker

                    asyncio.create_task(pdf_worker(moved))
                    # Cobrança via WhatsApp removida — clientes agora consultam
                    # e pagam pelo portal (/portal/pedidos).

                # F-040: refresh dos snapshots financeiros e de clientes pra
                # que o dash reflita a nova cobrança imediatamente sem precisar
                # F5. O banco já tem a venda+pagamento criados pelo
                # sales.approve_package, então o refresh aqui já pega eles.
                async def _refresh_snapshots():
                    try:
                        from app.services.finance_service import (
                            refresh_charge_snapshot,
                            refresh_dashboard_stats,
                        )
                        from app.services.customer_service import refresh_customer_rows_snapshot
                        # Pequeno delay pra o payments_worker gravar o
                        # provider_payment_id via update_vote_state antes do
                        # snapshot ser lido. Se não, o primeiro snapshot vem
                        # sem o ID do Asaas. Refresh de novo depois.
                        await asyncio.to_thread(refresh_charge_snapshot)
                        await asyncio.to_thread(refresh_dashboard_stats)
                        await asyncio.to_thread(refresh_customer_rows_snapshot)
                        await asyncio.sleep(5)
                        await asyncio.to_thread(refresh_charge_snapshot)
                        await asyncio.to_thread(refresh_dashboard_stats)
                    except Exception:
                        logger.warning("confirm_package: refresh snapshots falhou", exc_info=True)

                asyncio.create_task(_refresh_snapshots())

                return {
                    "status": "success",
                    "mode": "supabase",
                    "moved": {"from": "closed", "to": "approved", "package_id": pkg_id, "source_package_id": resolved_pkg_id},
                    "result": result,
                    "data": data,
                }
            except KeyError:
                raise HTTPException(status_code=404, detail="Pacote não encontrado no Supabase.")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Erro ao aprovar pacote no Supabase: {exc}")

        data = _load_metrics()
        moved = None
        try:
            action = ConfirmAction(pkg_id)
            data = action.execute(data)
            moved = action.confirmed_pkg
        except KeyError:
            raise HTTPException(status_code=404, detail="Pacote não encontrado em Pacotes Fechados.")

        if moved:
            if tag_value is not None:
                moved["tag"] = tag_value
                try:
                    from app.services.package_state_service import update_package_state
                    update_package_state(pkg_id, {"tag": tag_value})
                except Exception as e:
                    logger.warning("Falha ao persistir tag do pacote %s no state: %s", pkg_id, e)

            logger.info("Pacote encontrado. Criando task de envio de PDF.")
            await run_post_confirmation_effects(moved, pkg_id, metrics_data_to_save=data)
        else:
            logger.warning("AVISO: Pacote %s NÃƒO retornado apÃ³s execuÃ§Ã£o da aÃ§Ã£o de confirmaÃ§Ã£o.", pkg_id)

        try:
            from app.services.metrics_service import load_metrics as service_load_metrics

            merged_data = service_load_metrics()
        except FileNotFoundError:
            merged_data = data

        return {
            "status": "success",
            "moved": {"from": "closed_today", "to": "confirmed_packages", "package": moved},
            "data": merged_data,
        }


@app.get("/api/inventory/packages")
async def get_inventory_packages(
    start: str = "",
    end: str = "",
    status: str = "all",
    tag: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 30,
):
    """Histórico de pacotes pro estoque.

    Filtros:
      start: YYYY-MM-DD — data inicial. Vazio = primeiro dia do mês corrente.
      end: YYYY-MM-DD — data final (inclusiva). Vazio = mesmo que start (dia único).
           Se start também vazio = último dia do mês corrente.
      status: 'all', 'approved', 'cancelled'. Default 'all'.
      tag: filtra por fornecedor (case-insensitive, contém).
      search: busca em título da enquete.
      page, page_size: paginação.
    """
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")

    from datetime import datetime, timezone, date
    import calendar

    def _parse_date(s: str, field: str) -> date | None:
        s = (s or "").strip()
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=400, detail=f"{field} inválido (use YYYY-MM-DD)")

    start_date = _parse_date(start, "start")
    end_date = _parse_date(end, "end")

    if start_date is None and end_date is None:
        now = datetime.now(timezone.utc)
        start_date = date(now.year, now.month, 1)
        end_date = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
    elif start_date is None:
        start_date = end_date
    elif end_date is None:
        end_date = start_date

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    # Usar formato UTC com 'Z' (não '+00:00') pra evitar problema de URL encoding no PostgREST
    start_iso = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = start_iso
    end = end_iso

    sb = SupabaseRestClient.from_settings()

    status = (status or "all").strip().lower()

    # Filtros base (status + opcional fornecedor)
    base_filters = []
    if tag.strip():
        # `tag` na URL filtra por FORNECEDOR
        base_filters.append(("fornecedor", "ilike", f"*{tag.strip()}*"))

    columns = (
        "id,sequence_no,total_qty,participants_count,status,tag,fornecedor,custom_title,"
        "opened_at,closed_at,approved_at,cancelled_at,updated_at,"
        "pdf_status,pdf_file_name,pdf_sent_at,confirmed_by,cancelled_by,"
        "enquete:enquete_id(id,external_poll_id,titulo,chat_id,drive_file_id,"
        "produto:produto_id(nome,drive_file_id))"
    )

    pkgs = []

    def _query(status_value: str, date_field_for: str) -> list:
        f = base_filters + [
            ("status", "eq", status_value),
            (date_field_for, "gte", start),
            (date_field_for, "lte", end),
        ]
        return sb.select_all("pacotes", columns=columns, filters=f, order=f"{date_field_for}.desc") or []

    if status == "approved":
        pkgs = _query("approved", "approved_at")
    elif status == "cancelled":
        pkgs = _query("cancelled", "cancelled_at")
    else:
        # 'all' = approved (filtrado por approved_at) + cancelled (filtrado por cancelled_at)
        pkgs = _query("approved", "approved_at") + _query("cancelled", "cancelled_at")
        # Ordenar mesclado pela data mais recente entre approved_at/cancelled_at
        def _sort_key(p):
            return p.get("approved_at") or p.get("cancelled_at") or ""
        pkgs.sort(key=_sort_key, reverse=True)

    # Filtro de busca por título (frontend-style, depois aplica)
    if search.strip():
        s = search.strip().lower()
        def matches(p):
            enq = p.get("enquete") or {}
            title = (enq.get("titulo") or p.get("custom_title") or "").lower()
            return s in title
        pkgs = [p for p in pkgs if matches(p)]

    total = len(pkgs)
    # Paginação
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 30), 100))
    start_idx = (page - 1) * page_size
    paged = pkgs[start_idx:start_idx + page_size]

    items = []
    for p in paged:
        enq = p.get("enquete") or {}
        produto = enq.get("produto") or {}
        # F-061: imagem da enquete tem prioridade; produto é fallback.
        drive_id = enq.get("drive_file_id") or produto.get("drive_file_id")
        items.append({
            "id": p.get("id"),
            "title": p.get("custom_title") or enq.get("titulo") or "Pacote",
            "image": f"/files/{drive_id}" if drive_id else None,
            "status": p.get("status"),
            "tag": p.get("tag") or "",  # tipo de peça (etiqueta)
            "fornecedor": p.get("fornecedor") or "",
            "qty": int(p.get("total_qty") or 0),
            "participants": int(p.get("participants_count") or 0),
            "approved_at": p.get("approved_at"),
            "cancelled_at": p.get("cancelled_at"),
            "closed_at": p.get("closed_at"),
            "pdf_url": f"/api/packages/{p.get('id')}/pdf",
            "pdf_file_name": p.get("pdf_file_name"),
            "external_poll_id": enq.get("external_poll_id"),
        })

    # Lista de fornecedores presentes pra dropdown de filtro
    all_tags = sorted({(p.get("fornecedor") or "").strip() for p in pkgs if (p.get("fornecedor") or "").strip()})

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_prev": page > 1,
        "has_next": start_idx + page_size < total,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "status": status,
        "tag": tag,
        "search": search,
        "tags_available": all_tags,
        "summary": {
            "approved_count": sum(1 for p in pkgs if p.get("status") == "approved"),
            "cancelled_count": sum(1 for p in pkgs if p.get("status") == "cancelled"),
            "total_pieces": sum(int(p.get("total_qty") or 0) for p in pkgs if p.get("status") == "approved"),
        },
    }


@app.post("/api/enquetes/{poll_id}/fornecedor")
async def set_enquete_fornecedor(poll_id: str, request: PackageTagRequest):
    """Define/atualiza o FORNECEDOR de uma enquete.

    Diferente da `tag` (tipo de peça, usada na etiqueta), o `fornecedor`
    serve apenas para organização/filtro no estoque. É propagado para
    os pacotes que vierem dessa enquete a partir desse momento.

    Aceita poll_id como external_poll_id (do WhatsApp) ou UUID interno.
    Reaproveita o modelo PackageTagRequest (campo `tag` é o nome do fornecedor).
    """
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")

    fornecedor_value = (request.tag or "").strip() or None

    sb = SupabaseRestClient.from_settings()
    rows = sb.select("enquetes", columns="id", filters=[("external_poll_id", "eq", poll_id)], limit=1)
    if not isinstance(rows, list) or not rows:
        if _is_uuid(poll_id):
            rows = sb.select("enquetes", columns="id", filters=[("id", "eq", poll_id)], limit=1)
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Enquete não encontrada")

    enquete_id = rows[0]["id"]
    sb.update("enquetes", {"fornecedor": fornecedor_value, "updated_at": SupabaseRestClient.now_iso()}, filters=[("id", "eq", enquete_id)])

    # Atualiza fornecedor do pacote aberto também (pra exibir já)
    try:
        sb.update(
            "pacotes",
            {"fornecedor": fornecedor_value, "updated_at": SupabaseRestClient.now_iso()},
            filters=[("enquete_id", "eq", enquete_id), ("status", "eq", "open")],
        )
    except Exception:
        pass

    try:
        await generate_and_persist_metrics()
    except Exception:
        pass

    return {"status": "success", "poll_id": poll_id, "enquete_id": enquete_id, "fornecedor": fornecedor_value}


@app.post("/api/packages/{pkg_id}/tag")
async def set_package_tag(pkg_id: str, request: PackageTagRequest):
    """
    Define/atualiza a tag de um pacote (fechado ou confirmado).
    - Persistimos sempre em packages_state.json
    - Se o pacote jÃ¡ for confirmado, tambÃ©m salvamos em confirmed_packages.json e
      re-enfileiramos o PDF para refletir a nova tag.
    """
    lock = packages_lock
    async with lock:
        tag_value = None if request.tag is None else str(request.tag)

        if is_staging_dry_run():
            data = await _load_dashboard_data_for_response()
            simulated, found = simulate_tag_package(data, pkg_id, tag=tag_value)
            return {
                "status": "success",
                "simulated": True,
                "package_id": pkg_id,
                "tag": tag_value,
                "found": found,
                "data": simulated,
            }

        try:
            from app.services.package_state_service import update_package_state
            update_package_state(pkg_id, {"tag": tag_value})
        except Exception as e:
            logger.warning("Falha ao persistir tag no state para pkg=%s: %s", pkg_id, e)

        try:
            from app.services.metrics_service import load_metrics as service_load_metrics
            merged_data = service_load_metrics()
        except FileNotFoundError:
            merged_data = _load_metrics()

        # Resolve the Supabase UUID so we can match by source_package_id
        resolved_uuid = None
        try:
            from app.services.package_state_service import _resolve_package_uuid
            resolved_uuid = _resolve_package_uuid(pkg_id)
        except Exception:
            pass

        # Patch tag into cached metrics so the frontend sees the update immediately
        for section_key in ("open", "closed_today", "closed_week", "confirmed_today"):
            for pkg in merged_data.get("votos", {}).get("packages", {}).get(section_key, []):
                if pkg.get("id") == pkg_id or pkg.get("source_package_id") == pkg_id or (resolved_uuid and pkg.get("source_package_id") == resolved_uuid):
                    pkg["tag"] = tag_value

        return {"status": "success", "package_id": pkg_id, "tag": tag_value, "data": merged_data}


@app.post("/api/packages/{pkg_id}/reject")
async def reject_package(pkg_id: str):
    """Mark package as rejected (move to end) using action object."""
    lock = packages_lock
    async with lock:
        if is_staging_dry_run():
            resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
            data = await _load_dashboard_data_for_response()
            simulated, moved = simulate_reject_package(data, pkg_id, source_package_id=resolved_pkg_id)
            return {
                "status": "success",
                "simulated": True,
                "mode": "staging_dry_run",
                "moved": {"package": moved, "rejected": True},
                "data": simulated,
            }

        resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
        if resolved_pkg_id:
            try:
                sb = SupabaseRestClient.from_settings()
                # 1) Pegar enquete_id do pacote (pra rebuild depois)
                pkg_row = sb.select(
                    "pacotes",
                    columns="id,enquete_id,status",
                    filters=[("id", "eq", resolved_pkg_id)],
                    limit=1,
                )
                if isinstance(pkg_row, list) and pkg_row:
                    enquete_id = pkg_row[0].get("enquete_id")
                    previous_status = pkg_row[0].get("status")
                else:
                    enquete_id = None
                    previous_status = None

                # Bloquear cancelamento se já foi confirmado.
                # Para cancelar uma venda confirmada, é preciso primeiro
                # tratar o lado financeiro (reembolso no Asaas, etc).
                if previous_status == "approved":
                    raise HTTPException(
                        status_code=409,
                        detail="Pacote já confirmado não pode ser cancelado pelo dashboard. Trate o cancelamento manualmente (reembolso, etc).",
                    )

                # 2) Marcar pacote como cancelled
                sb.update(
                    "pacotes",
                    {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
                    filters=[("id", "eq", resolved_pkg_id)],
                    returning="minimal",
                )

                # 2.1) Remover votos consumidos por este pacote (status='out', qty=0)
                #      Evita que o rebuild_for_poll forme o pacote novamente.
                #      Se o cliente quiser comprar de novo, vota de novo no WhatsApp.
                try:
                    membros = sb.select_all(
                        "pacote_clientes",
                        columns="cliente_id,voto_id",
                        filters=[("pacote_id", "eq", resolved_pkg_id)],
                    )
                    voto_ids = [str(m["voto_id"]) for m in (membros or []) if m.get("voto_id")]
                    if voto_ids:
                        sb.update(
                            "votos",
                            {"status": "out", "qty": 0, "updated_at": datetime.now(timezone.utc).isoformat()},
                            filters=[("id", "in", voto_ids)],
                        )
                        # Audit trail: registra a remoção
                        for m in membros:
                            try:
                                sb.insert(
                                    "votos_eventos",
                                    {
                                        "enquete_id": enquete_id,
                                        "cliente_id": m.get("cliente_id"),
                                        "voto_id": m.get("voto_id"),
                                        "qty": 0,
                                        "action": "remove",
                                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                                        "raw_event_id": f"pkg_cancel_{resolved_pkg_id}",
                                        "payload_json": {"reason": "package_cancelled", "package_id": resolved_pkg_id},
                                    },
                                    returning="minimal",
                                )
                            except Exception:
                                pass
                except Exception as exc_v:
                    logger.warning("falha ao remover votos do pacote cancelled %s: %s", resolved_pkg_id, exc_v)

                # 3) (removido) Cancelamento de vendas/pagamentos só faz sentido
                #    se o pacote estava confirmado — bloqueado acima.

                # 4) Rebuild da enquete: votos voltam disponíveis pra formar novos pacotes
                if enquete_id:
                    try:
                        from app.services.whatsapp_domain_service import PackageService
                        PackageService(sb).rebuild_for_poll(str(enquete_id))
                    except Exception as exc_r:
                        logger.warning("rebuild_for_poll falhou após cancelamento pkg=%s: %s", resolved_pkg_id, exc_r)

                # 5) Refresh dos snapshots financeiro + clientes
                try:
                    await asyncio.to_thread(refresh_finance_dashboard_stats)
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(refresh_customer_rows_snapshot)
                except Exception:
                    pass

                data = await generate_and_persist_metrics()
                data["customers_map"] = load_customers()
                return {
                    "status": "success",
                    "mode": "supabase",
                    "moved": {"package_id": pkg_id, "source_package_id": resolved_pkg_id, "rejected": True},
                    "previous_status": previous_status,
                    "data": data,
                }
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Erro ao cancelar pacote no Supabase: {exc}")

        data = _load_metrics()
        try:
            action = RejectAction(pkg_id)
            data = action.execute(data)
            moved = action.rejected_pkg
        except KeyError:
            raise HTTPException(status_code=404, detail="Pacote nÃ£o encontrado em Pacotes Fechados.")
        
        if moved:
            try:
                from app.services.rejected_packages_service import add_rejected_package
                add_rejected_package(moved)
            except Exception as e:
                logger.warning("Falha ao persistir pacote cancelado %s: %s", pkg_id, e)
        
        _save_metrics(data)
        
        # Faz o recarregamento com o merge automÃ¡tico para retornar o dashboard atualizado para o frontend
        try:
            from app.services.metrics_service import load_metrics as service_load_metrics
            merged_data = service_load_metrics()
        except FileNotFoundError:
            merged_data = data
            
        return {"status": "success", "moved": {"package": moved, "rejected": True}, "data": merged_data}


@app.post("/api/packages/{pkg_id}/revert")
async def revert_package(pkg_id: str):
    """ReversÃ£o nÃ£o Ã© mais permitida conforme nova regra de negÃ³cio."""
    raise HTTPException(status_code=403, detail="A reversÃ£o de pacotes confirmados nÃ£o Ã© mais permitida.")


@app.post("/api/packages/{pkg_id}/cancel")
async def cancel_confirmed_package(pkg_id: str, request: Request):
    """Cancela um pacote confirmado em cascata.

    Body (JSON, opcional): `{"force": bool}`.
    Sem force e com pagamentos pagos: retorna 409 com lista de clientes pagos
    pra UI exibir aviso. Com force=true, preserva os pagos e cancela o resto.
    """
    from app.services import package_cancellation_service as pcs
    from app.services.confirmed_packages_service import get_confirmed_package

    force = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force") or False)
    except Exception:
        pass

    # pkg_id pode ser UUID ou legacy "pollId_seq" — resolver pra UUID
    pkg = get_confirmed_package(pkg_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="Pacote não encontrado")
    real_uuid = pkg.get("source_package_id") or pkg_id

    try:
        result = await asyncio.to_thread(
            pcs.cancel_package, real_uuid, force=force, cancelled_by="admin"
        )
    except pcs.PackageNotFound:
        raise HTTPException(status_code=404, detail="Pacote não encontrado no banco")
    except pcs.PackageCancelBlocked as exc:
        # 409 com a lista de pagos pro frontend montar o modal
        return JSONResponse(
            status_code=409,
            content={
                "status": "blocked_paid",
                "paid_count": len(exc.paid_info),
                "paid_clients": exc.paid_info,
            },
        )

    # Refresh snapshots como o cancelamento de charge individual faz
    try:
        from app.services.finance_service import (
            refresh_charge_snapshot, refresh_dashboard_stats,
        )
        from app.services.customer_service import refresh_customer_rows_snapshot
        await asyncio.to_thread(refresh_charge_snapshot)
        await asyncio.to_thread(refresh_dashboard_stats)
        await asyncio.to_thread(refresh_customer_rows_snapshot)
    except Exception:
        logger.warning("cancel_package: refresh de snapshots falhou", exc_info=True)

    return {"status": "success", **result}


@app.post("/api/packages/{pkg_id}/retry_payments")
async def retry_payments(pkg_id: str):
    """Removido — cobrança via WhatsApp desativada. Clientes pagam pelo portal."""
    raise HTTPException(
        status_code=410,
        detail="Envio de cobranças via WhatsApp foi desativado. Clientes agora acessam o portal para pagar.",
    )


@app.post("/api/packages/backfill-routing")
async def backfill_packages_routing():
    lock = _get_packages_lock()
    async with lock:
        data = _load_metrics()
        chat_map = await asyncio.to_thread(load_poll_chat_map)
        result = await asyncio.to_thread(backfill_metrics_routing, data, chat_map)
        if result.get("updated", 0) > 0:
            _save_metrics(data)
        return {"status": "success", "result": result}


@app.get("/api/packages/{pkg_id}/edit-data")
async def get_confirmed_package_edit_data(pkg_id: str):
    from app.services.confirmed_packages_service import get_confirmed_package, load_confirmed_packages
    from app.services.confirmed_package_edit_service import build_edit_columns

    pkg = get_confirmed_package(pkg_id)
    if not pkg and supabase_domain_enabled():
        # Fallback: look up the package in the metrics snapshot (covers Supabase-sourced confirmed)
        try:
            cached = _load_metrics()
            for section in ("confirmed_today", "closed_today", "closed_week"):
                for p in cached.get("votos", {}).get("packages", {}).get(section, []):
                    if p.get("id") == pkg_id:
                        pkg = p
                        break
                if pkg:
                    break
        except Exception:
            pass
    if not pkg:
        raise HTTPException(status_code=404, detail="Pacote confirmado não encontrado.")

    poll_id = _extract_poll_id_from_package(pkg)
    if not poll_id:
        raise HTTPException(status_code=400, detail="Pacote sem poll_id para ediÃ§Ã£o.")

    active_votes = await asyncio.to_thread(_fetch_active_votes_for_poll, poll_id)
    confirmed_packages = load_confirmed_packages()
    available_votes, selected_votes = build_edit_columns(pkg, active_votes, confirmed_packages)

    total_selected_qty = sum(int(v.get("qty", 0)) for v in selected_votes)

    return {
        "status": "success",
        "data": {
            "package_id": pkg_id,
            "poll_id": poll_id,
            "available_votes": available_votes,
            "selected_votes": selected_votes,
            "selected_qty": total_selected_qty,
            "required_qty": 24,
        },
    }


@app.patch("/api/packages/{pkg_id}/edit")
async def edit_package(pkg_id: str, request: Request):
    """F-032/F-033: atualiza título (e preço, se embutido no título) de um pacote.

    O título editado é persistido em `pacotes.custom_title` no Postgres.
    Se o título contém um novo preço no formato "$X,XX" ou "R$ X,XX"
    diferente do preço atual do pacote, recalcula `unit_price`,
    `subtotal`, `commission_amount` e `total_amount` em cada linha de
    `pacote_clientes` desse pacote (só esse pacote, não afeta outros).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")

    new_title = (body.get("poll_title") or body.get("custom_title") or "")
    if isinstance(new_title, str):
        new_title = new_title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="poll_title required")

    lock = packages_lock
    async with lock:
        updated_in_db = False
        try:
            from app.services.package_state_service import update_package_state
            update_package_state(pkg_id, {"custom_title": new_title})
            updated_in_db = True
        except Exception:
            logger.exception("edit_package: falha ao atualizar custom_title para pkg=%s", pkg_id)

        # F-033: recalcular preços no pacote_clientes se o título trouxer
        # novo preço embutido (ex: "Calça PMG $10,00" editado para "Calça PMG $15,00")
        price_updated = False
        try:
            from finance.utils import extract_price
            from app.config import settings as _settings
            new_price = extract_price(new_title)
            if new_price and new_price > 0 and supabase_domain_enabled():
                resolved_pkg_id = _resolve_supabase_package_id_or_none(pkg_id)
                if resolved_pkg_id:
                    sb = SupabaseRestClient.from_settings()
                    pc_rows = sb.select(
                        "pacote_clientes",
                        columns="id,qty,unit_price",
                        filters=[("pacote_id", "eq", resolved_pkg_id)],
                    )
                    if isinstance(pc_rows, list):
                        commission_pct = float(_settings.COMMISSION_PERCENT)
                        for row in pc_rows:
                            current_price = float(row.get("unit_price") or 0)
                            if abs(current_price - new_price) < 0.01:
                                continue  # mesmo preço, skip
                            qty = int(row.get("qty") or 0)
                            subtotal = round(new_price * qty, 2)
                            commission_amount = round(subtotal * (commission_pct / 100), 2)
                            total_amount = round(subtotal + commission_amount, 2)
                            sb.update(
                                "pacote_clientes",
                                {
                                    "unit_price": new_price,
                                    "subtotal": subtotal,
                                    "commission_amount": commission_amount,
                                    "total_amount": total_amount,
                                },
                                filters=[("id", "eq", row["id"])],
                                returning="minimal",
                            )
                            price_updated = True
                        if price_updated:
                            logger.info(
                                "F-033: pacote %s tivemos unit_price atualizado para %.2f em %d linhas",
                                resolved_pkg_id,
                                new_price,
                                len(pc_rows),
                            )
        except Exception:
            logger.exception("edit_package: falha recalculando preço pra pkg=%s (F-033 branch)", pkg_id)

        # Se o pacote já está em 'confirmed_packages' (store local),
        # atualiza o snapshot também pra refletir imediatamente sem esperar
        # o próximo reload completo de métricas.
        try:
            from app.services.confirmed_packages_service import (
                get_confirmed_package,
                add_confirmed_package,
            )
            pkg = get_confirmed_package(pkg_id)
            if pkg:
                pkg["poll_title"] = new_title
                add_confirmed_package(pkg)
        except Exception:
            logger.warning("edit_package: pacote %s não está em confirmed_packages (ok se ainda não confirmado)", pkg_id)

        # Regenera métricas pra refletir o novo título em todas as abas
        try:
            if _is_supabase_metrics_mode():
                async with _get_lock():
                    data = await generate_and_persist_metrics()
            else:
                data = _load_metrics()
        except Exception:
            logger.exception("edit_package: falha regenerando métricas, retornando status ok mesmo assim")
            data = None

        return {
            "status": "success",
            "package_id": pkg_id,
            "poll_title": new_title,
            "persisted": updated_in_db,
            "price_updated": price_updated,
            "data": data,
        }


@app.post("/api/packages/{pkg_id}/update-confirmed")
async def update_confirmed_package_votes(pkg_id: str, request: ConfirmedPackageUpdateRequest):
    from app.services.confirmed_packages_service import get_confirmed_package, add_confirmed_package
    from app.services.confirmed_package_edit_service import (
        normalize_votes_payload,
        split_added_removed_votes,
        validate_package_total,
    )

    lock = packages_lock
    async with lock:
        if is_staging_dry_run():
            data = await _load_dashboard_data_for_response()
            simulated, updated = simulate_update_confirmed_package_votes(data, pkg_id, votes=request.votes)
            return {
                "status": "success",
                "simulated": True,
                "data": simulated,
                "summary": {
                    "updated": updated,
                    "added_votes": 0,
                    "removed_votes": 0,
                    "created_charges": 0,
                    "deleted_charges": 0,
                },
            }

        pkg = get_confirmed_package(pkg_id)
        if not pkg:
            raise HTTPException(status_code=404, detail="Pacote confirmado nÃ£o encontrado.")

        new_votes = normalize_votes_payload(request.votes)
        if validate_package_total(new_votes) is None:
            raise HTTPException(status_code=400, detail="O pacote deve conter exatamente 24 peÃ§as.")

        current_votes = normalize_votes_payload(pkg.get("votes", []))
        added_votes, removed_votes = split_added_removed_votes(current_votes, new_votes)

        # F-051/F-060: sincronização cirúrgica com pacote_clientes + vendas + pagamentos.
        # Se algum membro pago está sendo removido, exige confirmação explícita.
        resolved_uuid = _resolve_supabase_package_id_or_none(pkg_id)
        sync_summary: Dict[str, Any] = {}
        if resolved_uuid:
            from app.services.confirmed_package_sync_service import ConfirmedPackageSyncService
            sync_svc = ConfirmedPackageSyncService()
            analysis = await asyncio.to_thread(sync_svc.analyze, resolved_uuid, current_votes, new_votes)
            if analysis["requires_confirmation"] and not request.confirm_paid_removal:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "paid_removal_requires_confirmation",
                        "message": "Há clientes que já pagaram sendo removidos do pacote. Confirme pra continuar.",
                        "paid_removals": analysis["paid_removals"],
                    },
                )
            try:
                sync_summary = await asyncio.to_thread(sync_svc.apply, resolved_uuid, current_votes, new_votes)
            except Exception:
                logger.exception("F-060: falha ao sincronizar pacote %s", pkg_id)
                raise HTTPException(status_code=500, detail="Falha ao atualizar pacote no banco")

            # Atualiza total_qty + pdf_status no pacote
            try:
                _sb = SupabaseRestClient.from_settings()
                _sb.update(
                    "pacotes",
                    {
                        "total_qty": sum(int(v.get("qty") or 0) for v in new_votes),
                        "pdf_status": "queued",
                        "pdf_attempts": 0,
                        "pdf_file_name": None,
                    },
                    filters=[("id", "eq", resolved_uuid)],
                    returning="minimal",
                )
            except Exception:
                logger.exception("F-060: falha ao atualizar pacote %s (total_qty/pdf)", pkg_id)

        pkg["votes"] = new_votes
        pkg["qty"] = sum(int(v.get("qty", 0)) for v in new_votes)
        pkg["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Reset PDF status to force generation of a new label with updated composition
        pkg["pdf_status"] = "queued"
        pkg["pdf_attempts"] = 0
        pkg["pdf_file_name"] = None

        add_confirmed_package(pkg)  # no-op (F-051), mantido por compatibilidade
        
        # Iniciar worker para gerar novo PDF da etiqueta e enviar ao estoque
        from app.workers.background import pdf_worker
        asyncio.create_task(pdf_worker(pkg))

        removed_charge_ids: List[str] = []
        created_charge_ids: List[str] = []
        if FinanceManager:
            async with finance_lock:
                fm = FinanceManager()
                charges = fm.list_charges()

                removed_phones = {str(v.get("phone")) for v in removed_votes if v.get("phone")}
                charges_to_remove = [
                    c
                    for c in charges
                    if str(c.get("package_id")) == str(pkg_id)
                    and str(c.get("customer_phone")) in removed_phones
                ]
                for charge in charges_to_remove:
                    cid = str(charge.get("id"))
                    if cid and fm.delete_charge(cid):
                        removed_charge_ids.append(cid)

                if added_votes:
                    new_charge_payload = {
                        "id": pkg_id,
                        "poll_title": pkg.get("poll_title", ""),
                        "valor_col": pkg.get("valor_col"),
                        "image": pkg.get("image"),
                        "image_thumb": pkg.get("image_thumb"),
                        "confirmed_at": pkg.get("confirmed_at"),
                        "votes": added_votes,
                    }
                    new_charges = fm.register_package_confirmation(new_charge_payload)
                    created_charge_ids = [str(c.get("id")) for c in new_charges if c.get("id")]

                    for charge in new_charges:
                        _enqueue_charge_job(charge, pkg)

        if removed_charge_ids:
            try:
                from app.services.payment_queue_service import remove_open_jobs_for_charge_ids

                remove_open_jobs_for_charge_ids(removed_charge_ids)
            except Exception:
                logger.exception("Falha ao remover jobs da fila para cobranÃ§as removidas do pacote %s", pkg_id)

        try:
            from app.services.metrics_service import load_metrics as service_load_metrics
            merged_data = service_load_metrics()
        except FileNotFoundError:
            merged_data = _load_metrics()

        return {
            "status": "success",
            "data": merged_data,
            "summary": {
                "added_votes": len(added_votes),
                "removed_votes": len(removed_votes),
                "created_charges": len(created_charge_ids),
                "deleted_charges": len(removed_charge_ids),
            },
        }


@app.get("/api/packages/{pkg_id}/edit-data-closed")
async def get_closed_package_edit_data(pkg_id: str):
    """Dados pra editar um pacote fechado (trocas com a fila da própria enquete)."""
    from app.services import closed_package_edit_service as cpes

    resolved = _resolve_supabase_package_id_or_none(pkg_id) or pkg_id
    try:
        data = await asyncio.to_thread(cpes.get_edit_data, resolved)
    except cpes.ClosedPackageNotFound:
        raise HTTPException(status_code=404, detail="Pacote fechado não encontrado")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "data": data}


@app.post("/api/packages/{pkg_id}/update-closed")
async def update_closed_package_votes(pkg_id: str, request: Request):
    """Aplica edição de membros num pacote fechado. Sem vendas/pagamentos
    envolvidos — só `pacote_clientes`."""
    from app.services import closed_package_edit_service as cpes

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")

    votes = body.get("votes") or []
    resolved = _resolve_supabase_package_id_or_none(pkg_id) or pkg_id

    try:
        summary = await asyncio.to_thread(cpes.apply_edit, resolved, votes)
    except cpes.ClosedPackageNotFound:
        raise HTTPException(status_code=404, detail="Pacote fechado não encontrado")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "success", "summary": summary}


try:
    from finance.manager import FinanceManager
except ImportError:
    FinanceManager = None

@app.get("/api/finance/charges")
async def get_charges(
    page: int | None = None,
    page_size: int | None = None,
    status: str | None = None,
    search: str | None = None,
):
    """Lista de cobranças financeiras.

    F-028 fix: sempre retorna resposta paginada no envelope
    {items, total, page, page_size, has_prev, has_next}.
    O fallback antigo que retornava todo o histórico (1142+ linhas, ~1MB)
    foi removido — cliente deve paginar. Default: page=1, page_size=50.
    Máximo por página: 200.
    """
    safe_page = max(1, page or 1)
    safe_size = min(200, max(1, page_size or 50))
    return list_finance_charges_page(
        page=safe_page,
        page_size=safe_size,
        status=status,
        search=search,
    )

@app.get("/api/finance/stats")
async def get_finance_stats():
    return get_finance_dashboard_stats()


@app.post("/api/finance/sync-asaas")
async def trigger_asaas_sync():
    """Dispara sync manual com Asaas (verifica todos pagamentos pendentes)."""
    from app.services.asaas_sync_service import sync_asaas_payments
    updated = await sync_asaas_payments()
    return {"status": "success", "updated": updated}


@app.get("/api/admin/polls/{enquete_id}/whapi-compare")
async def poll_whapi_compare(enquete_id: str):
    """Diagnóstico: compara estado WHAPI vs banco para uma enquete (somente leitura)."""
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")
    from app.services.poll_reconcile_service import PollReconcileService
    svc = PollReconcileService()
    result = await asyncio.to_thread(svc.compare, enquete_id)
    return result


@app.post("/api/admin/polls/{enquete_id}/resync")
async def poll_resync(enquete_id: str):
    """Sincroniza votos de uma enquete específica via WHAPI (aplica diff no banco)."""
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")
    from app.services.poll_reconcile_service import PollReconcileService
    svc = PollReconcileService()
    result = await asyncio.to_thread(svc.sync, enquete_id)
    return result


@app.post("/api/admin/polls/resync-all-open")
async def poll_resync_all_open():
    """Sincroniza todas as enquetes abertas (mesma lógica do job periódico)."""
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")
    from app.services.poll_reconcile_service import PollReconcileService
    svc = PollReconcileService()
    result = await asyncio.to_thread(svc.sync_all_open)
    return result


@app.get("/api/finance/extract")
async def get_finance_extract(date_from: str = "", date_to: str = "", kind: str = "paid"):
    """Extrato de cobranças: lista pagamentos no período.

    Params (opcionais, formato YYYY-MM-DD):
      date_from: data inicial inclusiva
      date_to:   data final inclusiva
      kind:      'paid' (pagos, filtra por paid_at) ou 'pending' (em aberto, filtra por created_at)
    Sem parâmetros: retorna últimos 30 dias, pagos.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.supabase_service import SupabaseRestClient

    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase desabilitado")

    kind = (kind or "paid").strip().lower()
    if kind not in ("paid", "pending"):
        raise HTTPException(status_code=400, detail="kind inválido (use 'paid' ou 'pending')")

    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="date_from inválido (use YYYY-MM-DD)")
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="date_to inválido (use YYYY-MM-DD)")
    else:
        end = datetime.now(timezone.utc) + timedelta(days=1)

    sb = SupabaseRestClient.from_settings()

    if kind == "paid":
        date_field = "paid_at"
        pagamentos_status_filter = ("status", "eq", "paid")
        legacy_status_filter = ("status", "eq", "paid")
    else:
        date_field = "created_at"
        pagamentos_status_filter = ("status", "in", ["created", "sent"])
        # legacy_charges: 'pending', 'enviando', 'erro no envio' são em débito
        legacy_status_filter = ("status", "in", ["pending", "enviando", "erro no envio"])

    items = []
    total = 0.0

    # ---------- 1) Pagamentos novos (Asaas) ----------
    pagamentos = sb.select_all(
        "pagamentos",
        columns="id,venda_id,paid_at,created_at,status",
        filters=[
            pagamentos_status_filter,
            (date_field, "gte", start.isoformat()),
            (date_field, "lt", end.isoformat()),
        ],
        order=f"{date_field}.desc",
    )

    if pagamentos:
        venda_ids = [str(p["venda_id"]) for p in pagamentos if p.get("venda_id")]
        vendas_map = {}
        if venda_ids:
            for i in range(0, len(venda_ids), 200):
                batch = venda_ids[i:i+200]
                vs = sb.select_all(
                    "vendas",
                    columns=(
                        "id,cliente_id,qty,total_amount,"
                        "cliente:cliente_id(nome,celular),"
                        "produto:produto_id(nome),"
                        "pacote:pacote_id(enquete:enquete_id(titulo))"
                    ),
                    filters=[("id", "in", batch)],
                )
                for v in (vs or []):
                    vendas_map[str(v["id"])] = v

        for p in pagamentos:
            venda = vendas_map.get(str(p.get("venda_id"))) or {}
            cliente = venda.get("cliente") or {}
            produto = venda.get("produto") or {}
            enquete = (venda.get("pacote") or {}).get("enquete") or {}
            amount = float(venda.get("total_amount") or 0)
            total += amount
            items.append({
                "paid_at": p.get("paid_at"),
                "created_at": p.get("created_at"),
                "status": p.get("status"),
                "customer_name": cliente.get("nome") or "Cliente",
                "customer_phone": cliente.get("celular") or "",
                "product_name": enquete.get("titulo") or produto.get("nome") or "Produto",
                "qty": int(venda.get("qty") or 0),
                "total_amount": amount,
                "source": "asaas",
            })

    # ---------- 2) Cobranças legadas (MercadoPago histórico) ----------
    legacy_field = date_field  # mesma lógica: paid_at se 'paid', created_at se 'pending'
    legacy_charges = sb.select_all(
        "legacy_charges",
        columns="id,paid_at,created_at,status,customer_name,customer_phone,poll_title,quantity,total_amount",
        filters=[
            legacy_status_filter,
            (legacy_field, "gte", start.isoformat()),
            (legacy_field, "lt", end.isoformat()),
        ],
        order=f"{legacy_field}.desc",
    )

    for lc in (legacy_charges or []):
        amount = float(lc.get("total_amount") or 0)
        total += amount
        items.append({
            "paid_at": lc.get("paid_at"),
            "created_at": lc.get("created_at"),
            "status": lc.get("status"),
            "customer_name": lc.get("customer_name") or "Cliente",
            "customer_phone": lc.get("customer_phone") or "",
            "product_name": lc.get("poll_title") or "Produto",
            "qty": int(lc.get("quantity") or 0),
            "total_amount": amount,
            "source": "legacy",
        })

    # Ordenar tudo junto pela data correta (desc)
    items.sort(key=lambda x: str(x.get(date_field) or ""), reverse=True)

    return {
        "items": items,
        "count": len(items),
        "total": round(total, 2),
        "date_from": start.date().isoformat(),
        "date_to": (end - timedelta(days=1)).date().isoformat(),
        "kind": kind,
    }


@app.get("/api/finance/queue")
async def get_finance_queue(limit: int = 300):
    try:
        from app.services.payment_queue_service import get_queue_snapshot
        return get_queue_snapshot(limit=limit)
    except Exception:
        logger.exception("Erro ao carregar snapshot da fila de cobranÃ§as")
        raise HTTPException(status_code=500, detail="Falha ao carregar fila de cobranÃ§as")

@app.delete("/api/finance/charges/{charge_id}")
async def delete_finance_charge(charge_id: str):
    if is_staging_dry_run():
        return {
            "status": "success",
            "simulated": True,
            "message": "Cobrança simulada como excluída.",
            "charges": simulate_delete_charge(list_finance_charges(), charge_id),
        }

    if supabase_domain_enabled():
        client = SupabaseRestClient.from_settings()
        deleted = False
        last_error = None
        # Tentar deletar de pagamentos (cobranças novas Asaas)
        try:
            rows = client.select("pagamentos", columns="id", filters=[("id", "eq", charge_id)], limit=1)
            if isinstance(rows, list) and rows:
                client.delete("pagamentos", filters=[("id", "eq", charge_id)])
                deleted = True
        except Exception as exc:
            last_error = exc
            logger.warning("delete_finance_charge: pagamentos delete falhou charge=%s: %s", charge_id, exc)
        # Tentar deletar de legacy_charges (cobranças MercadoPago)
        if not deleted:
            try:
                rows = client.select("legacy_charges", columns="id", filters=[("id", "eq", charge_id)], limit=1)
                if isinstance(rows, list) and rows:
                    client.delete("legacy_charges", filters=[("id", "eq", charge_id)])
                    deleted = True
            except Exception as exc:
                last_error = exc
                logger.warning("delete_finance_charge: legacy_charges delete falhou charge=%s: %s", charge_id, exc)
        if not deleted:
            if last_error:
                raise HTTPException(status_code=500, detail=f"Erro ao excluir cobrança: {last_error}")
            raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
        # Invalidar cache do financeiro + clientes
        try:
            from app.services.finance_service import refresh_charge_snapshot, refresh_dashboard_stats
            from app.services.customer_service import refresh_customer_rows_snapshot
            await asyncio.to_thread(refresh_charge_snapshot)
            await asyncio.to_thread(refresh_dashboard_stats)
            await asyncio.to_thread(refresh_customer_rows_snapshot)
        except Exception:
            pass
        return {"status": "success", "message": "Cobrança excluída com sucesso."}

    if not FinanceManager:
        raise HTTPException(status_code=501, detail="Finance module not available")
    fm = FinanceManager()
    if not fm.delete_charge(charge_id):
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    return {"status": "success", "message": "Cobrança excluída com sucesso."}

@app.post("/api/finance/charges/{charge_id}/resend")
async def resend_finance_charge(charge_id: str):
    """Removido — cobrança via WhatsApp desativada. Clientes pagam pelo portal."""
    raise HTTPException(
        status_code=410,
        detail="Envio de cobranças via WhatsApp foi desativado. Clientes agora acessam o portal para pagar.",
    )




# --- Manual payment status toggle ---
@app.patch("/api/finance/charges/{charge_id}/status")
async def update_charge_status(charge_id: str, request: Request):
    """Atualiza status de um pagamento: pago, pendente ou cancelado.

    F-036: aceita `cancelled` (ou `cancelado`) além de `paid`/`pending`.
    Quando cancelado, o valor sai do "em débito" do cliente automaticamente
    (via RPC `get_customer_stats` que só soma created|sent, não cancelled).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")

    new_status_raw = str(body.get("status", "")).strip().lower()
    ALIASES = {
        "paid": "paid", "pago": "paid",
        "pending": "created", "pendente": "created",  # "pending" no dash = "created" no banco
        "cancelled": "cancelled", "canceled": "cancelled", "cancelado": "cancelled",
    }
    if new_status_raw not in ALIASES:
        raise HTTPException(
            status_code=400,
            detail="Status deve ser paid/pago, pending/pendente ou cancelled/cancelado",
        )

    db_status = ALIASES[new_status_raw]

    if supabase_domain_enabled():
        from app.services.supabase_service import SupabaseRestClient
        from datetime import datetime, timezone
        sb = SupabaseRestClient.from_settings()

        # Status legacy_charges usa nomes em pt: 'pending', 'paid', 'cancelled'
        # Status pagamentos: 'created', 'sent', 'paid', 'cancelled'
        update_data: Dict[str, Any] = {"status": db_status}
        if db_status == "paid":
            update_data["paid_at"] = datetime.now(timezone.utc).isoformat()
        else:
            update_data["paid_at"] = None

        # Tenta primeiro em pagamentos (cobranças Asaas)
        updated = False
        try:
            rows = sb.select("pagamentos", columns="id", filters=[("id", "eq", charge_id)], limit=1)
            if isinstance(rows, list) and rows:
                resp = sb._request(
                    "PATCH",
                    f"/rest/v1/pagamentos?id=eq.{charge_id}",
                    payload=update_data,
                    prefer="return=minimal",
                )
                if resp.status_code in (200, 204):
                    updated = True
        except Exception:
            pass

        # Se não estava em pagamentos, tenta em legacy_charges
        if not updated:
            try:
                rows = sb.select("legacy_charges", columns="id", filters=[("id", "eq", charge_id)], limit=1)
                if isinstance(rows, list) and rows:
                    resp = sb._request(
                        "PATCH",
                        f"/rest/v1/legacy_charges?id=eq.{charge_id}",
                        payload=update_data,
                        prefer="return=minimal",
                    )
                    if resp.status_code in (200, 204):
                        updated = True
            except Exception as exc:
                logger.warning("legacy_charges PATCH falhou: %s", exc)

        if not updated:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada em pagamentos nem legacy_charges")

        # F-037/F-039: refresh dos snapshots de charges E stats pra que o próximo
        # GET /api/finance/charges e /api/finance/stats retornem valores novos
        # sem servir dados velhos do runtime_state. Sem isso, o operador clica
        # "paid" no dash mas a tabela e o KPI continuam mostrando "pending" até
        # reload manual.
        try:
            from app.services.finance_service import refresh_charge_snapshot, refresh_dashboard_stats
            await asyncio.to_thread(refresh_charge_snapshot)
            await asyncio.to_thread(refresh_dashboard_stats)
        except Exception:
            logger.warning("update_charge_status: refresh charge/stats snapshot falhou", exc_info=True)

        # F-036: refresh do snapshot de clientes pra refletir mudança em total_debt/total_paid
        try:
            from app.services.customer_service import refresh_customer_rows_snapshot
            await asyncio.to_thread(refresh_customer_rows_snapshot)
        except Exception:
            logger.warning("update_charge_status: refresh customer snapshot falhou", exc_info=True)

        return {
            "status": "success",
            "charge_id": charge_id,
            "new_status": db_status,
        }

    raise HTTPException(status_code=501, detail="Not implemented without Supabase")


# F-062: webhook do Asaas removido. Reconciliação de pagamentos acontece
# 100% via polling em `app/services/asaas_sync_service.py` (a cada 10 min),
# que cobre tanto pagamentos individuais quanto PIX combinados do portal.
# Pra desativar completamente: no painel Asaas, Configurações → Webhooks,
# remover a URL de /webhook/asaas.


# Prometheus metrics endpoint (optional)
try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

    @app.get("/metrics")
    async def prometheus_metrics():
        try:
            data = generate_latest()
            return Response(content=data, media_type=CONTENT_TYPE_LATEST)
        except Exception:
            raise HTTPException(status_code=500, detail="Prometheus metrics unavailable")
except Exception:
    # prometheus_client not installed; no endpoint
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

