"""
Efeitos colaterais apos um pacote ser confirmado (financeiro, persistencia, PDF).
Usado pelo fluxo normal (closed -> confirm) e pelo fluxo de criacao manual.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.locks import finance_lock
from app.services.metrics_service import save_metrics as persist_metrics

logger = logging.getLogger("raylook.confirmation_pipeline")

try:
    from finance.manager import FinanceManager
except ImportError:
    FinanceManager = None


def _clean_charge_item_name(raw_value: str) -> str:
    item_name = raw_value or "Peca"
    item_name = re.sub(r"(?:R\$\s*|\$\s*)?\d+(?:[.,]\d{1,2})?", "", item_name)
    item_name = re.sub(r"[🔥🎯📦💕✨✅💰👇]", "", item_name)
    item_name = re.sub(r"\s+", " ", item_name).strip()
    return item_name or "Peca"


def _resolve_charge_rows(moved: Dict[str, Any], pkg_id: str) -> list[Dict[str, Any]]:
    from app.services.finance_service import list_package_charges
    from app.services.supabase_service import supabase_domain_enabled

    source_package_id = str(moved.get("source_package_id") or pkg_id or "").strip()
    if source_package_id and supabase_domain_enabled():
        charges = list_package_charges(source_package_id)
        if charges:
            return charges

    if FinanceManager:
        fm = FinanceManager()
        return fm.register_package_confirmation(moved)
    return []


async def run_post_confirmation_effects(
    moved: Dict[str, Any],
    pkg_id: str,
    *,
    metrics_data_to_save: Optional[Dict[str, Any]] = None,
    persist_confirmed_package: bool = True,
) -> None:
    """
    Registra financeiro, grava pacote confirmado, persiste metricas (se fornecidas) e agenda PDF.

    `metrics_data_to_save`: snapshot de metricas apos remover de fechados; se None (fluxo manual),
    nao grava metricas apos add_confirmed; o worker de PDF tambem nao chama persist_metrics.
    """
    from app.services.confirmed_packages_service import add_confirmed_package

    if not moved:
        return

    if moved.get("pdf_status") == "sent":
        logger.info("Pacote %s ja com PDF enviado; sem novo envio.", pkg_id)
    else:
        moved.setdefault("pdf_attempts", 0)
        moved.setdefault("pdf_status", "queued")
        moved.setdefault("pdf_file_name", None)

    # Sem envio de WhatsApp pro cliente: cobranças ficam visíveis apenas
    # via /portal/pedidos. Mantemos o lock + resolução de cobranças caso
    # passos seguintes precisem garantir que as rows existam.
    try:
        async with finance_lock:
            _resolve_charge_rows(moved, pkg_id)
    except Exception as exc:
        logger.error("Falha ao registrar dados financeiros para o pacote %s: %s", pkg_id, exc)

    if persist_confirmed_package:
        add_confirmed_package(moved)

    if metrics_data_to_save is not None:
        persist_metrics(metrics_data_to_save)

    if moved.get("pdf_status") == "sent":
        return

    async def _pdf_worker(package_snapshot: Dict[str, Any]) -> None:
        logger.info("PDF worker started for pkg=%s", pkg_id)
        try:
            from app.config import settings
            from app.services.package_state_service import update_package_state
            from estoque.pdf_builder import build_pdf
            from finance.utils import get_pdf_filename_by_id

            pdf_bytes = await asyncio.to_thread(build_pdf, package_snapshot, settings.COMMISSION_PERCENT)
            filename = get_pdf_filename_by_id(pkg_id)

            out_dir = Path("etiquetas_estoque").resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            local_path = out_dir / filename
            with open(local_path, "wb") as file_handle:
                file_handle.write(pdf_bytes)

            # Envio pro WhatsApp do estoque removido: PDF fica disponível
            # apenas via endpoint /api/packages/{pkg_id}/pdf no dashboard.
            now_iso = datetime.utcnow().isoformat()
            try:
                update_package_state(
                    pkg_id,
                    {
                        "pdf_file_name": filename,
                        "pdf_status": "sent",
                        "pdf_sent_at": now_iso,
                    },
                )
            except Exception as exc:
                logger.warning("Erro ao atualizar estado do PDF: %s", exc)

            package_snapshot["pdf_file_name"] = filename
            package_snapshot["pdf_status"] = "sent"
            package_snapshot["pdf_sent_at"] = now_iso
            if metrics_data_to_save is not None:
                persist_metrics(metrics_data_to_save)
        except Exception as exc:
            logger.error("ERRO no PDF worker pkg=%s: %s", pkg_id, exc, exc_info=True)
            try:
                from app.services.package_state_service import update_package_state
                update_package_state(pkg_id, {"pdf_status": "failed"})
            except Exception:
                pass
            package_snapshot["pdf_status"] = "failed"
            if metrics_data_to_save is not None:
                persist_metrics(metrics_data_to_save)

    asyncio.create_task(_pdf_worker(dict(moved)))
