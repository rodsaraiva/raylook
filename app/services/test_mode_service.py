"""Modo de teste via toggle no 'Criar Pacote'.

Easter egg: digitar "[ENQUETE DE TESTE]" no campo de busca do modal
"Criar Pacote" mostra um toggle ON/OFF em vez da lista de enquetes.

Quando ativo:
  - Banner visual "MODO TESTE ATIVO" em todas as abas

Estado: guardado em app_runtime_state (key "test_mode"). Simples boolean.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger("raylook.services.test_mode")

TEST_ENQUETE_PREFIX = "[ENQUETE DE TESTE]"

_ASAAS_PROD_TOKEN = os.getenv("ASAAS_PROD_TOKEN", "")
_ASAAS_PROD_URL = "https://api.asaas.com/v3/"

RUNTIME_KEY = "test_mode"


def _load_flag() -> bool:
    try:
        from app.services.runtime_state_service import load_runtime_state, runtime_state_enabled
        if not runtime_state_enabled():
            return False
        data = load_runtime_state(RUNTIME_KEY) or {}
        return bool(data.get("active"))
    except Exception:
        return False


def _save_flag(active: bool) -> None:
    try:
        from app.services.runtime_state_service import save_runtime_state, runtime_state_enabled
        if runtime_state_enabled():
            save_runtime_state(RUNTIME_KEY, {"active": active})
    except Exception:
        logger.exception("test_mode: falha ao salvar flag")


def is_test_mode_active() -> bool:
    return _load_flag()


def set_test_mode(active: bool) -> Dict[str, Any]:
    _save_flag(active)
    logger.info("test_mode: %s", "ATIVADO" if active else "DESATIVADO")
    return get_test_mode_status()


def get_asaas_config() -> dict:
    return {"token": _ASAAS_PROD_TOKEN, "url": _ASAAS_PROD_URL, "is_test": False}


def get_whatsapp_routing() -> dict:
    return {"instance_name": None, "override_phone": None, "is_test": False}


def get_test_mode_status() -> dict:
    active = is_test_mode_active()
    return {
        "active": active,
        "label": "MODO TESTE ATIVO" if active else None,
    }
