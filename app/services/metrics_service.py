from pathlib import Path
from typing import Any, Dict, Optional
import json
import asyncio
import logging
import os

from app.utils.fileio import atomic_write, safe_read_json
from app.storage import JsonFileStorage

logger = logging.getLogger("raylook.metrics_service")

METRICS_FILE = Path(os.environ.get("DATA_DIR", "data")) / "dashboard_metrics.json"
_storage = JsonFileStorage(METRICS_FILE)

def load_metrics() -> Dict[str, Any]:
    data = _storage.load()
    if data is None:
        raise FileNotFoundError("Metrics file not found.")
    
    # Merge confirmed packages from separate storage
    try:
        from app.services.confirmed_packages_service import merge_confirmed_into_metrics
        data = merge_confirmed_into_metrics(data)
    except Exception as e:
        logger.warning("Erro ao mesclar pacotes confirmados: %s", e)
    try:
        from app.services.rejected_packages_service import merge_rejected_into_metrics
        data = merge_rejected_into_metrics(data)
    except Exception as e:
        logger.warning("Erro ao mesclar pacotes cancelados: %s", e)
    
    # Merge asynchronous states (payments, pdf) into metrics before returning
    try:
        from app.services.package_state_service import merge_states_into_metrics
        data = merge_states_into_metrics(data)
    except Exception as e:
        logger.warning("Erro ao mesclar estados dos pacotes: %s", e)
        
    return data

def save_metrics(data: Dict[str, Any]) -> None:
    _storage.save(data)

async def generate_and_persist_metrics() -> Dict[str, Any]:
    """
    Generate dashboard metrics (delegates to existing generate_metrics),
    persist atomically and trigger thumbnail generation.
    """
    # import lazily to avoid circular import during app initialization
    from metrics.services import generate_metrics  # local import
    # generate (blocking) in thread
    dashboard_data = await asyncio.to_thread(generate_metrics)
    if dashboard_data is None:
        # fallback to existing file if generation failed
        old = _storage.load()
        if old is None:
            data = {}
        else:
            data = old
    else:
        data = dashboard_data

    # REMOVIDO: Não preservamos mais confirmed_today do dashboard_metrics.json,
    # pois agora os confirmados vivem no confirmed_packages.json.
    # O merge acontece dinamicamente no load_metrics.
    
    # preserve metadata only (thumbnails, etc.)
    try:
        old = _storage.load()
        if old:
            # 2. Preserve thumbnails and other metadata
            from metrics import processors
            processors.preserve_package_metadata(data, old)
    except Exception as e:
        logger.warning("Erro ao preservar metadados: %s", e)

    # Merge confirmed packages (filtered by today) so the summary and return data are correct
    try:
        from app.services.confirmed_packages_service import merge_confirmed_into_metrics
        data = merge_confirmed_into_metrics(data)
    except Exception as e:
        logger.warning("Erro ao mesclar pacotes confirmados na geração: %s", e)
    try:
        from app.services.rejected_packages_service import merge_rejected_into_metrics
        data = merge_rejected_into_metrics(data)
    except Exception as e:
        logger.warning("Erro ao mesclar pacotes cancelados na geração: %s", e)

    # ensure minimal structure
    v_dict = data.setdefault("votos", {})
    if isinstance(v_dict, dict):
        p_dict = v_dict.setdefault("packages", {})
        if isinstance(p_dict, dict):
            p_dict.setdefault("confirmed_today", [])

    # Thumbnails: NÃO bloquear a geração de métricas. O backfill worker
    # (background_tasks.py) gera thumbs a cada 2 min de forma assíncrona.
    # O process_thumbnails síncrono causava OOM no startup quando o volume
    # de uploads estava vazio (tentava baixar 200+ imagens de uma vez).
    try:
        from images.thumbs import process_thumbnails
        process_thumbnails(data, download_missing=False)
    except Exception as e:
        logger.warning("Thumbnail generation failed: %s", e)

    # persist atomically
    try:
        _storage.save(data)
    except Exception:
        # fallback write
        with METRICS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    try:
        from app.services.runtime_state_service import (
            DASHBOARD_METRICS_STATE_KEY,
            runtime_state_enabled,
            save_runtime_state,
        )

        if runtime_state_enabled():
            save_runtime_state(
                DASHBOARD_METRICS_STATE_KEY,
                {
                    "generated_at": data.get("generated_at"),
                    "source": "metrics_service",
                },
            )
    except Exception as e:
        logger.warning("Erro ao atualizar marcador de runtime das métricas: %s", e)

    return data

