import time
import logging
import re
from typing import Dict, Any, Optional
from integrations.mercadopago.client import MercadoPagoClient, MercadoPagoError

logger = logging.getLogger("raylook.integrations.mercadopago.utils")

def _normalize_cpf_field(vote: Dict[str, Any]) -> str:
    """Extrair e limpar CPF/CNPJ. Retornar um CPF padrão de teste caso não encontre."""
    cpf = str(vote.get("cpf") or vote.get("cpf_cnpj") or "00000000191").strip()
    cpf_digits = "".join(filter(str.isdigit, cpf))
    if len(cpf_digits) not in (11, 14):
        return "00000000191"
    return cpf_digits

def _clean_customer_name(name: str) -> str:
    """
    Remove números, emojis e caracteres especiais do nome, 
    mantendo apenas letras e espaços.
    """
    if not name:
        return "Cliente Genérico"
    
    # Converter para string
    name_str = str(name)
    
    # 1. Substituir dígitos, underscores e caracteres não-alfanuméricos (pontuação, emojis) por espaço
    # \d: dígitos, \W: não-alfanumérico (inclui emojis e pontuação), _: underscore
    name_clean = re.sub(r'[\d_\W]+', ' ', name_str)
    
    # 2. Limpar espaços duplos resultantes da limpeza e nas extremidades
    name_clean = re.sub(r'\s+', ' ', name_clean).strip()
    
    # 3. Fallback se a limpeza esvaziar o nome
    if not name_clean:
        return "Cliente Genérico"
        
    return name_clean

def ensure_mercadopago_customer(
    mp_client: MercadoPagoClient,
    vote: Dict[str, Any],
    external_ref: Optional[str] = None,
    max_retries: int = 3,
    initial_delay: float = 1.0,
) -> Dict[str, Any]:
    """
    Ensure a customer exists in Mercado Pago for the given vote.
    - Uses vote["name"], vote["phone"], vote["email"] if present.
    - Returns a dict with keys: id, status, attempts, error (opt).
    """
    if not isinstance(vote, dict):
        return {"id": None, "status": "invalid_vote", "attempts": 0, "error": "vote_not_dict"}

    if vote.get("mercadopago_customer_id"):
        return {"id": vote.get("mercadopago_customer_id"), "status": "created", "attempts": 0}

    name = vote.get("name")
    name = _clean_customer_name(name)
    
    phone = vote.get("phone") or vote.get("mobilePhone") or vote.get("celular") or None
    email = vote.get("email") or None
    cpf = _normalize_cpf_field(vote)

    attempts = 0
    delay = initial_delay
    last_exc = None
    for attempt in range(1, max_retries + 1):
        attempts = attempt
        try:
            # Check if user already exists
            if email:
                existing = mp_client.get_customer(email)
                if existing and existing.get("id"):
                    return {"id": existing.get("id"), "status": "created", "attempts": attempts}

            # Create if not exists
            resp = mp_client.create_customer(name=name, email=email, cpf_cnpj=cpf, phone=phone, external_reference=external_ref)
            cid = resp.get("id")
            if cid:
                logger.info("[MercadoPago] Customer created: id=%s external_ref=%s name=%s", cid, external_ref or "-", name)
                return {"id": cid, "status": "created", "attempts": attempts}
            
            logger.error("[MercadoPago] create_customer returned no id (external_ref=%s)", external_ref or "-")
            return {"id": None, "status": "failed", "attempts": attempts, "error": "missing_id_in_response"}
        
        except MercadoPagoError as e:
            last_exc = e
            logger.warning("[MercadoPago] create_customer attempt %d failed for external_ref=%s: %s", attempt, external_ref or "-", e)
            # Transient error retry
            time.sleep(delay)
            delay = min(delay * 2, 16.0)
            continue
        except Exception as e:
            last_exc = e
            logger.exception("[MercadoPago] Unexpected error creating customer external_ref=%s: %s", external_ref or "-", e)
            time.sleep(delay)
            delay = min(delay * 2, 16.0)
            continue

    logger.error("[MercadoPago] Exhausted attempts creating customer external_ref=%s last_error=%s", external_ref or "-", last_exc)
    return {"id": None, "status": "failed", "attempts": attempts, "error": str(last_exc)}
