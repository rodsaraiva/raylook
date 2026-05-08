"""F-053 v2: modo de teste via toggle no 'Criar Pacote'.

Easter egg: digitar "[ENQUETE DE TESTE]" no campo de busca do modal
"Criar Pacote" mostra um toggle ON/OFF em vez da lista de enquetes.

Quando ativo:
  - Cobranças Asaas → token sandbox
  - Mensagens WhatsApp → instância "Marcos Paulo" para contato 3390
  - Banner visual "MODO TESTE ATIVO" em todas as abas

Estado: guardado em app_runtime_state (key "test_mode"). Simples boolean.
Sem enquete, sem pacote, sem cobrança.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger("raylook.services.test_mode")

TEST_ENQUETE_PREFIX = "[ENQUETE DE TESTE]"

_ASAAS_PROD_TOKEN = os.getenv(
    "ASAAS_PROD_TOKEN",
    "$aact_prod_000MzkwODA2MWY2OGM3MWRlMDU2NWM3MzJlNzZmNGZhZGY6OjkwMWEyMmQxLTY1MDktNDM4Yy04YzI1LTdmNWVmNDI3OGEwNjo6JGFhY2hfMTgwMWFiOGYtYjgzYS00NmI3LTkxMjgtNTVhZjBjODg5YTFh",
)
_ASAAS_PROD_URL = "https://api.asaas.com/v3/"

_ASAAS_SANDBOX_TOKEN = os.getenv(
    "ASAAS_SANDBOX_TOKEN",
    "$aact_hmlg_000MzkwODA2MWY2OGM3MWRlMDU2NWM3MzJlNzZmNGZhZGY6OmY5OWVjNTY0LTAzOGItNDExNy05NDYwLWNlMjg1Y2Y0ZjE2Yjo6JGFhY2hfODk2MTUwMTMtYmQ2Mi00YzExLWE4MjItN2E3NDI3YTdiMmFi",
)
_ASAAS_SANDBOX_URL = "https://api-sandbox.asaas.com/v3/"

_TEST_PHONE = "5562993353390"
_TEST_EVOLUTION_INSTANCE = "Marcos Paulo"

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
    if is_test_mode_active():
        return {"token": _ASAAS_SANDBOX_TOKEN, "url": _ASAAS_SANDBOX_URL, "is_test": True}
    return {"token": _ASAAS_PROD_TOKEN, "url": _ASAAS_PROD_URL, "is_test": False}


def get_whatsapp_routing() -> dict:
    if is_test_mode_active():
        return {"instance_name": _TEST_EVOLUTION_INSTANCE, "override_phone": _TEST_PHONE, "is_test": True}
    return {"instance_name": None, "override_phone": None, "is_test": False}


def get_test_mode_status() -> dict:
    active = is_test_mode_active()
    return {
        "active": active,
        "label": "MODO TESTE ATIVO — cobranças no sandbox, mensagens para contato de teste" if active else None,
    }
