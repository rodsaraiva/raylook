from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from typing import Any, Optional
from pydantic import BaseModel

from app.services.packages_service import confirm_package, reject_package, revert_package
from app.workers.background import pdf_worker, payments_worker

router = APIRouter(prefix="/api/packages")

class PackageTagRequest(BaseModel):
    tag: Optional[str] = None

class PackageEditRequest(BaseModel):
    poll_title: Optional[str] = None

@router.post("/{pkg_id}/confirm")
async def confirm_package_endpoint(pkg_id: str, request: Request, background_tasks: BackgroundTasks):
    lock = request.app.state.packages_lock
    async with lock:
        try:
            tag_value: Optional[str] = None
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("tag", None) is not None:
                    tag_value = str(body.get("tag"))
            except Exception:
                tag_value = None

            result = await confirm_package(pkg_id, tag=tag_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="Pacote não encontrado em Pacotes Fechados.")
        moved = result.get("moved", {}).get("package")
        if moved:
            background_tasks.add_task(pdf_worker, moved)
            background_tasks.add_task(payments_worker, moved)
        return {"status": "success", "moved": result.get("moved"), "data": result.get("data")}

@router.post("/{pkg_id}/reject")
async def reject_package_endpoint(pkg_id: str, request: Request):
    lock = request.app.state.packages_lock
    async with lock:
        try:
            result = reject_package(pkg_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Pacote não encontrado em Pacotes Fechados.")
        moved = result.get("moved")
        return {"status": "success", "moved": {"package": moved, "rejected": True}, "data": result.get("data")}

@router.post("/{pkg_id}/revert")
async def revert_package_endpoint(pkg_id: str, request: Request):
    raise HTTPException(status_code=403, detail="A reversão de pacotes confirmados não é mais permitida.")


@router.post("/{pkg_id}/tag")
async def set_package_tag_endpoint(pkg_id: str, request: Request, background_tasks: BackgroundTasks, payload: PackageTagRequest):
    lock = request.app.state.packages_lock
    async with lock:
        tag_value = None if payload.tag is None else str(payload.tag)
        try:
            from app.services.package_state_service import update_package_state
            update_package_state(pkg_id, {"tag": tag_value})
        except Exception:
            pass

        # Se for confirmado, atualizar store e re-enfileirar PDF
        try:
            from datetime import datetime, timezone
            from app.services.confirmed_packages_service import get_confirmed_package, add_confirmed_package
            pkg = get_confirmed_package(pkg_id)
            if pkg:
                pkg["tag"] = tag_value
                pkg["updated_at"] = datetime.now(timezone.utc).isoformat()
                pkg["pdf_status"] = "queued"
                pkg["pdf_attempts"] = 0
                pkg["pdf_file_name"] = None
                add_confirmed_package(pkg)

                from app.workers.background import pdf_worker
                background_tasks.add_task(pdf_worker, pkg)
        except Exception:
            pass

        from app.services.metrics_service import load_metrics
        return {"status": "success", "package_id": pkg_id, "tag": tag_value, "data": load_metrics()}


@router.patch("/{pkg_id}/edit")
async def edit_package_endpoint(pkg_id: str, request: Request, payload: PackageEditRequest):
    lock = request.app.state.packages_lock
    async with lock:
        new_title = (payload.poll_title or "").strip() or None
        if new_title:
            try:
                from app.services.package_state_service import update_package_state
                update_package_state(pkg_id, {"custom_title": new_title})
            except Exception:
                pass

            try:
                from app.services.confirmed_packages_service import get_confirmed_package, add_confirmed_package
                pkg = get_confirmed_package(pkg_id)
                if pkg:
                    pkg["poll_title"] = new_title
                    add_confirmed_package(pkg)
            except Exception:
                pass

        from app.services.metrics_service import load_metrics
        return {"status": "success", "package_id": pkg_id, "poll_title": new_title, "data": load_metrics()}


