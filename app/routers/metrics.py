from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import logging

from app.services import metrics_service
from app.services.customer_service import load_customers
from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")

logger = logging.getLogger("raylook.routers.metrics")

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "settings": settings})

@router.get("/api/metrics")
async def get_metrics():
    try:
        data = metrics_service.load_metrics()
        
        # ensure confirmed_today exists inside votos.packages
        v = data.get("votos", {})
        if isinstance(v, dict):
            pkgs = v.get("packages", {})
            if isinstance(pkgs, dict):
                pkgs.setdefault("confirmed_today", [])
        
        # Inject customers map for Late Binding on the frontend
        data["customers_map"] = load_customers()
        
        return JSONResponse(content=data)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Metrics file not found. Run dashboard generation first.")
    except Exception:
        logger.exception("Failed to read metrics file")
        raise HTTPException(status_code=500, detail="Failed to read metrics file")

@router.post("/api/refresh")
async def refresh_metrics(request: Request):
    lock = request.app.state.refresh_lock
    if lock.locked():
        raise HTTPException(status_code=409, detail="Sincronização já está em andamento.")
    try:
        async with lock:
            data = await metrics_service.generate_and_persist_metrics()
            return {"status": "success", "data": data}
    except Exception:
        logger.exception("Error refreshing metrics")
        raise HTTPException(status_code=500, detail="Error generating metrics")


@router.get("/health")
async def health() -> JSONResponse:
    """Simple health check."""
    return JSONResponse(content={"status": "ok"})

@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness check: ensure metrics file can be read (if exists)."""
    try:
        data = metrics_service.load_metrics()
        return JSONResponse(content={"ready": True})
    except FileNotFoundError:
        # If file missing, still return ready=False
        return JSONResponse(content={"ready": False}, status_code=503)
    except Exception:
        return JSONResponse(content={"ready": False}, status_code=503)

