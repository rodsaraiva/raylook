"""API router para criação de pacote do zero (adhoc)."""
import asyncio
import io
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.services.adhoc_package_service import create_adhoc_package
from app.services.supabase_service import SupabaseRestClient
from integrations.google_drive import GoogleDriveClient

logger = logging.getLogger("raylook.adhoc")

router = APIRouter(prefix="/api/packages/adhoc")

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB

PHONE_BR_RE = re.compile(r"^55\d{10,11}$")


class UploadImageResponse(BaseModel):
    drive_file_id: str
    full_url: str


class ProductDraft(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    unit_price: float = Field(..., gt=0, le=10000)
    image: dict = Field(...)

    @field_validator("image")
    @classmethod
    def image_has_drive_id(cls, v):
        if not isinstance(v, dict) or not v.get("drive_file_id"):
            raise ValueError("image.drive_file_id obrigatório.")
        return v


class VoteLineAdhoc(BaseModel):
    phone: str
    qty: int = Field(ge=1, le=24)
    customer_id: Optional[str] = None
    name: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", (v or "").strip())
        if not PHONE_BR_RE.match(digits):
            raise ValueError("Celular deve estar no formato 55 + DDD + número (10 ou 11 dígitos).")
        return digits


class AdhocPackageRequest(BaseModel):
    product: ProductDraft
    votes: List[VoteLineAdhoc] = Field(..., min_length=1, max_length=24)


def _resolve_vote_names(votes: List[VoteLineAdhoc]) -> List[dict]:
    """Busca nomes cadastrados pra cada phone (1 query por phone — lista é pequena, ≤24)."""
    client = SupabaseRestClient.from_settings()
    name_by_phone = {}
    for v in votes:
        try:
            rows = client.select(
                "clientes",
                columns="celular,nome",
                filters=[("celular", "eq", v.phone)],
                limit=1,
            ) or []
            if isinstance(rows, list) and rows:
                name_by_phone[v.phone] = rows[0].get("nome") or ""
        except Exception:
            pass
    return [
        {"phone": v.phone, "name": v.name or name_by_phone.get(v.phone) or "", "qty": v.qty}
        for v in votes
    ]


def _detect_duplicate_clients(votes: List[VoteLineAdhoc]) -> List[dict]:
    """Para cada cliente nos votos, retorna lista dos pacotes approved/closed onde
    ele já aparece (últimos 30 dias). Usado pra avisar a Alana antes de criar um
    pacote adhoc que duplicaria o cliente.

    Evita o cenário que aconteceu em 19/04 com a Lary — ela estava num pacote
    approved e foi adicionada inadvertidamente a um pacote adhoc do mesmo produto.
    """
    if not votes:
        return []
    client = SupabaseRestClient.from_settings()

    # 1) Resolve phones -> cliente_id
    phones = [v.phone for v in votes]
    clientes_rows = client.select_all(
        "clientes",
        columns="id,nome,celular",
        filters=[("celular", "in", phones)],
    )
    if not isinstance(clientes_rows, list) or not clientes_rows:
        return []
    cliente_by_id = {str(c["id"]): c for c in clientes_rows}
    cliente_ids = list(cliente_by_id.keys())

    # 2) pacote_clientes desses clientes + embed do pacote (status, titulo)
    pcs = client.select_all(
        "pacote_clientes",
        columns=(
            "cliente_id,qty,"
            "pacote:pacote_id(id,status,approved_at,closed_at,updated_at,"
            "enquete:enquete_id(titulo))"
        ),
        filters=[("cliente_id", "in", cliente_ids)],
    )
    if not isinstance(pcs, list):
        return []

    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    warnings_by_phone: dict = {}
    for pc in pcs:
        pacote = pc.get("pacote") or {}
        status = str(pacote.get("status") or "").lower()
        if status not in ("closed", "approved"):
            continue  # open/cancelled não conta
        # só últimos 30 dias
        ref_iso = pacote.get("approved_at") or pacote.get("closed_at") or pacote.get("updated_at")
        if ref_iso:
            try:
                ref_dt = datetime.fromisoformat(str(ref_iso).replace("Z", "+00:00"))
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                if ref_dt < cutoff:
                    continue
            except Exception:
                pass

        cliente_id = str(pc.get("cliente_id") or "")
        cliente = cliente_by_id.get(cliente_id) or {}
        phone = cliente.get("celular") or ""
        if not phone:
            continue
        enquete = pacote.get("enquete") or {}
        entry = warnings_by_phone.setdefault(phone, {
            "phone": phone,
            "name": cliente.get("nome") or "",
            "existing_packages": [],
        })
        entry["existing_packages"].append({
            "package_id": pacote.get("id"),
            "package_status": status,
            "poll_title": enquete.get("titulo") or "",
            "qty": int(pc.get("qty") or 0),
            "ref_at": ref_iso,
        })

    # Só retorna entries que de fato têm pacote ativo
    return [w for w in warnings_by_phone.values() if w["existing_packages"]]


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/upload-image", response_model=UploadImageResponse)
async def upload_image(image: UploadFile = File(...)):
    if image.content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Formato não suportado. Use: {', '.join(sorted(ALLOWED_MIME))}.",
        )

    content = await image.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Imagem acima de 5MB.")

    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Imagem inválida ou corrompida.") from exc

    safe_name = (image.filename or "adhoc.png").replace("/", "_").replace("\\", "_")[:100]

    storage = GoogleDriveClient()
    try:
        file_id = storage.upload_file(
            safe_name,
            content,
            mime_type=image.content_type,
        )
    except Exception as exc:
        logger.exception("adhoc upload_image: falha ao salvar imagem")
        raise HTTPException(status_code=502, detail="Falha ao salvar imagem.") from exc

    return UploadImageResponse(drive_file_id=file_id, full_url=storage.get_public_url(file_id))


class AdhocPackageConfirmRequest(AdhocPackageRequest):
    force: bool = False


@router.post("/preview")
async def preview(body: AdhocPackageRequest):
    total = sum(v.qty for v in body.votes)
    if total != 24:
        raise HTTPException(status_code=400, detail="O pacote precisa ter exatamente 24 peças.")
    votes_resolved = _resolve_vote_names(body.votes)
    subtotal = round(body.product.unit_price * 24, 2)
    commission_amount = round(24 * settings.COMMISSION_PER_PIECE, 2)
    total_final = round(subtotal + commission_amount, 2)

    try:
        duplicate_warnings = await asyncio.to_thread(_detect_duplicate_clients, body.votes)
    except Exception:
        logger.exception("adhoc preview: falha ao detectar duplicatas (prosseguindo sem aviso)")
        duplicate_warnings = []

    return {
        "total_qty": total,
        "subtotal": subtotal,
        "commission_percent": 0,
        "commission_amount": commission_amount,
        "total_final": total_final,
        "votes_resolved": votes_resolved,
        "duplicate_warnings": duplicate_warnings,
        "product": {
            "name": body.product.name,
            "unit_price": body.product.unit_price,
            "drive_file_id": body.product.image["drive_file_id"],
        },
    }


@router.post("/confirm")
async def confirm(body: AdhocPackageConfirmRequest):
    total = sum(v.qty for v in body.votes)
    if total != 24:
        raise HTTPException(status_code=400, detail="O pacote precisa ter exatamente 24 peças.")

    # Re-checa duplicatas no momento do confirm (preview pode ter sido gerado
    # há muito tempo e o estado pode ter mudado). Se tem duplicata e !force,
    # bloqueia com 409.
    if not body.force:
        try:
            warnings = await asyncio.to_thread(_detect_duplicate_clients, body.votes)
        except Exception:
            logger.exception("adhoc confirm: falha ao detectar duplicatas (permitindo criação)")
            warnings = []
        if warnings:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=409,
                content={
                    "status": "blocked_duplicates",
                    "message": "Há clientes que já estão em outros pacotes ativos.",
                    "duplicate_warnings": warnings,
                },
            )

    try:
        result = await asyncio.to_thread(
            create_adhoc_package,
            product_name=body.product.name,
            unit_price=body.product.unit_price,
            drive_file_id=body.product.image["drive_file_id"],
            votes=[v.model_dump() for v in body.votes],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("adhoc confirm: falha ao criar pacote")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Recalcula snapshot de métricas pra o pacote recém-criado aparecer
    # imediatamente no dashboard (mesmo padrão do fluxo manual com enquete
    # em main.py:1255).
    try:
        from app.services.metrics_service import generate_and_persist_metrics
        await generate_and_persist_metrics()
    except Exception:
        logger.exception("adhoc confirm: falha ao recalcular métricas (pacote já persistido)")

    return result
