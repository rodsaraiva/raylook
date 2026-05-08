import hashlib
import io
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime, timedelta

import requests
from PIL import Image

logger = logging.getLogger("raylook.images.thumbs")

STATIC_UPLOADS = Path("static") / "uploads"
STATIC_UPLOADS.mkdir(parents=True, exist_ok=True)


def _safe_filename(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"thumb_{h}.jpg"


def _resize_image_bytes(img_bytes: bytes, max_size: int = 300) -> bytes:
    with Image.open(io.BytesIO(img_bytes)) as im:
        im = im.convert("RGB")
        im.thumbnail((max_size, max_size))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85)
        return out.getvalue()


def drive_export_view_url(drive_id: str) -> str:
    """URL servida pelo LocalImageStorage (sandbox) — mesmo formato em todos os endpoints."""
    return f"/files/{drive_id}"


def poll_image_thumb_from_metrics(data: Dict[str, Any], poll_id: str) -> Optional[str]:
    """Retorna `image_thumb` já calculado para o poll_id (lista de pacotes abertos/fechados)."""
    packages = data.get("votos", {}).get("packages", {})
    if not isinstance(packages, dict):
        return None
    for pkg_list in packages.values():
        if not isinstance(pkg_list, list):
            continue
        for pkg in pkg_list:
            if not isinstance(pkg, dict):
                continue
            pid = pkg.get("poll_id")
            if not pid:
                pkg_id = pkg.get("id")
                if isinstance(pkg_id, str) and "_" in pkg_id:
                    pid = pkg_id.rsplit("_", 1)[0]
            if str(pid) != str(poll_id):
                continue
            thumb = pkg.get("image_thumb")
            if thumb and isinstance(thumb, str) and thumb.strip():
                return thumb.strip()
    return None


def ensure_thumbnail_for_image_url(image_url: str) -> Optional[str]:
    """
    Garante arquivo em static/uploads (hash SHA1 da URL) e retorna /static/uploads/thumb_*.jpg.
    Mesma lógica usada em process_thumbnails / lista de Pacotes Fechados (não usar URL direta do Drive no <img>).
    """
    if not image_url or not isinstance(image_url, str):
        return None
    src = image_url.strip()
    if not src:
        return None
    if not (src.startswith("http://") or src.startswith("https://") or src.startswith("data:")):
        return None
    try:
        filename = _safe_filename(src)
        out_path = STATIC_UPLOADS / filename
        if out_path.exists():
            return f"/static/uploads/{filename}"

        if src.startswith("data:"):
            header, b64 = src.split(",", 1)
            import base64

            img_bytes = base64.b64decode(b64)
        else:
            resp = requests.get(src, timeout=10)
            resp.raise_for_status()
            img_bytes = resp.content

        thumb_bytes = _resize_image_bytes(img_bytes, max_size=300)
        with out_path.open("wb") as out:
            out.write(thumb_bytes)
        return f"/static/uploads/{filename}"
    except Exception as e:
        logger.warning("ensure_thumbnail_for_image_url failed for %s: %s", src[:80], e)
        return None


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        # Handles both Z and +00:00
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def cleanup_old_thumbnails(data: Dict[str, Any], max_age_hours: int = 168) -> bool:
    """
    Remove arquivos de thumbnail de enquetes criadas há mais de max_age_hours (default 7 dias)
    e limpa a referência no dicionário de dados.
    """
    changed = False
    now = datetime.now()
    votos = data.get("votos", {})
    packages = votos.get("packages", {}) if isinstance(votos, dict) else {}
    
    # Coletar todos os thumbnails que DEVEM ser mantidos
    valid_thumbs = set()
    
    for section in packages.values():
        if not isinstance(section, list):
            continue
        for pkg in section:
            opened_at = _parse_iso(pkg.get("opened_at"))
            thumb = pkg.get("image_thumb")
            
            if not thumb:
                continue
                
            if opened_at and (now - opened_at) > timedelta(hours=max_age_hours):
                # Muito antigo, remover do data
                pkg["image_thumb"] = None
                changed = True
                logger.info("Removing old thumbnail reference for pkg %s (age > %dh)", pkg.get("id"), max_age_hours)
            else:
                # Manter este thumb
                filename = thumb.split("/")[-1]
                valid_thumbs.add(filename)

    # Limpar arquivos físicos que não estão em valid_thumbs
    try:
        for file in STATIC_UPLOADS.glob("thumb_*.jpg"):
            if file.name not in valid_thumbs:
                try:
                    file.unlink()
                    logger.info("Deleted orphaned or old thumbnail file: %s", file.name)
                except Exception as e:
                    logger.warning("Failed to delete thumbnail file %s: %s", file.name, e)
    except Exception as e:
        logger.warning("Error during thumbnail file cleanup: %s", e)

    return changed


def process_thumbnails(data: Dict[str, Any], max_age_hours: int = 168, download_missing: bool = True) -> bool:
    """
    Processa o dicionário de métricas em busca de imagens sem thumbnails,
    gera as imagens e atualiza o dicionário in-place.
    Apenas gera thumbnails para enquetes criadas nas últimas max_age_hours (default 7 dias).
    Retorna True se houve alguma mudança.
    """
    # Primeiro, limpar o que for velho
    changed = cleanup_old_thumbnails(data, max_age_hours)
    
    now = datetime.now()
    votos = data.get("votos", {})
    packages = votos.get("packages", {}) if isinstance(votos, dict) else {}

    for key, pkg_list in packages.items():
        if not isinstance(pkg_list, list):
            continue
        for pkg in pkg_list:
            if not isinstance(pkg, dict):
                continue
            
            # Verificar idade da enquete
            opened_at = _parse_iso(pkg.get("opened_at"))
            if opened_at and (now - opened_at) > timedelta(hours=max_age_hours):
                # Pular enquetes antigas
                continue

            image = pkg.get("image")
            if not image or pkg.get("image_thumb"):
                continue
            if not download_missing:
                # Só usar thumbs já existentes no disco, sem baixar
                from pathlib import Path
                import hashlib
                src = str(image).strip()
                sha = hashlib.sha1(src.encode()).hexdigest()
                local = Path("static/uploads") / f"thumb_{sha}.jpg"
                if local.exists():
                    pkg["image_thumb"] = f"/static/uploads/thumb_{sha}.jpg"
                    changed = True
                continue
            if not isinstance(image, str):
                continue
            src = image.strip()
            if not src:
                continue
            if not (src.startswith("http://") or src.startswith("https://") or src.startswith("data:")):
                logger.debug("Skipping non-http image src: %s", src)
                continue
            thumb_url = ensure_thumbnail_for_image_url(src)
            if thumb_url:
                pkg["image_thumb"] = thumb_url
                changed = True
                logger.info(
                    "Thumbnail for package %s -> %s",
                    pkg.get("id") or pkg.get("poll_title"),
                    pkg["image_thumb"],
                )
            continue
    return changed


def generate_thumbnails_for_metrics(metrics_path: Path) -> None:
    """
    Legado: Mantido para compatibilidade, mas agora usa process_thumbnails internamente.
    """
    try:
        import json
        if not metrics_path.exists():
            return
        with metrics_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        if process_thumbnails(data):
            # write back atomically
            tmp = metrics_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp.replace(metrics_path)
            logger.info("Updated metrics file with thumbnails.")
    except Exception as e:
        logger.warning("Unable to process thumbnails for file %s: %s", metrics_path, e)

