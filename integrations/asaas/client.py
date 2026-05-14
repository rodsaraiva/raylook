import logging
import os
import re
import time
import unicodedata
import uuid
import requests
from typing import Any, Dict, Optional

logger = logging.getLogger("raylook.integrations.asaas")


def _sandbox_enabled() -> bool:
    try:
        from app.config import settings
        return bool(getattr(settings, "RAYLOOK_SANDBOX", True))
    except Exception:
        return os.getenv("RAYLOOK_SANDBOX", "true").strip().lower() not in ("0", "false", "no")


def _stub_response(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resposta dummy compatível com o shape que o resto do código espera."""
    fake_id = f"sandbox_{uuid.uuid4().hex[:12]}"
    if path.startswith("customers"):
        return {"id": fake_id, "name": (payload or {}).get("name", "Sandbox"), "data": [{"id": fake_id}]}
    if "pixQrCode" in path:
        return {"payload": "00020126sandbox", "encodedImage": ""}
    if path.startswith("payments"):
        return {
            "id": fake_id,
            "status": "PENDING",
            "value": (payload or {}).get("value"),
            "invoiceUrl": f"http://localhost/sandbox/{fake_id}",
            "bankSlipUrl": "",
        }
    return {"id": fake_id}


def _sanitize_description(text: str, max_length: int = 250) -> str:
    """Remove caracteres que o Asaas rejeita em `description`.

    O endpoint /payments retorna 400 com code=parse_error se a descrição
    trouxer emojis (ex: 😮‍💨💕🤌🏻) ou controles. Mantém ASCII imprimível
    (incluindo `$ % & * ( )`) e letras/pontuação acentuadas (ex: ç, ã, à).
    Descarta emojis, zero-width joiners e variation selectors.
    """
    if not text:
        return ""
    cleaned_chars = []
    for ch in str(text):
        code = ord(ch)
        # ASCII imprimível (inclui $, espaço, pontuação comum)
        if 0x20 <= code <= 0x7E:
            cleaned_chars.append(ch)
            continue
        cat = unicodedata.category(ch)
        # Mantém letras/números acentuados (L*/N*) e pontuação (P*/Z*)
        if cat[0] in ("L", "N", "P", "Z") and cat != "Zl" and cat != "Zp":
            cleaned_chars.append(ch)
    cleaned = re.sub(r"\s+", " ", "".join(cleaned_chars)).strip()
    return cleaned[:max_length] or "Cobranca"

class AsaasClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        # F-053: se não foi passado explicitamente, consulta o test_mode_service
        # que decide entre token prod ou sandbox baseado na existência de
        # [ENQUETE DE TESTE] no banco.
        if api_key is None and base_url is None:
            try:
                from app.services.test_mode_service import get_asaas_config
                cfg = get_asaas_config()
                api_key = cfg["token"]
                base_url = cfg["url"]
            except Exception:
                pass  # fallback pra env vars
        self.api_key = api_key or os.getenv("ASAAS_API_KEY") or os.getenv("AS_AASAAS_TOKEN") or ""
        self.base_url = (base_url or os.getenv("ASAAS_API_URL") or os.getenv("AS_AASAAS_URL") or "https://api.asaas.com/v3/").rstrip("/")
        self._poll_retries = int(os.getenv("ASAAS_POLL_RETRIES", "12"))
        self._poll_initial_delay = float(os.getenv("ASAAS_POLL_INITIAL_DELAY", "1.0"))
        self._poll_max_delay = float(os.getenv("ASAAS_POLL_MAX_DELAY", "8.0"))

    def _headers(self) -> Dict[str, str]:
        return {"access_token": self.api_key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        if _sandbox_enabled():
            payload = kwargs.get("json") or kwargs.get("params")
            logger.info("[asaas-stub] %s %s payload=%s", method, path, payload)
            return _stub_response(method, path, payload if isinstance(payload, dict) else None)
        url = self.base_url + "/" + path.lstrip("/")
        resp = requests.request(method, url, headers=self._headers(), timeout=30, **kwargs)
        logger.info("Asaas %s %s -> %d", method, path, resp.status_code)
        if not resp.ok:
            logger.error("Asaas error: %s", resp.text[:500])
        resp.raise_for_status()
        return resp.json() if resp.text.strip() else {}

    def create_customer(self, name: str, phone: str, cpf_cnpj: str) -> Dict[str, Any]:
        """Find or create a customer in Asaas.

        cpf_cnpj é obrigatório — sem ele, o Asaas agruparia todos os
        clientes no mesmo customer (bug crônico). Callers que ainda não
        têm CPF devem tratar via fluxo de contingência (modal no portal).
        """
        cpf_cnpj = (cpf_cnpj or "").strip()
        if not cpf_cnpj:
            raise ValueError("cpf_cnpj obrigatório para criar customer no Asaas")
        try:
            existing = self._request("GET", f"customers?cpfCnpj={cpf_cnpj}")
            data = existing.get("data", [])
            if data:
                return data[0]
        except Exception:
            pass

        return self._request("POST", "customers", json={
            "name": name,
            "phone": phone,
            "cpfCnpj": cpf_cnpj,
        })

    def create_payment_pix(self, customer_id: str, amount: float, due_date: str, description: str = "") -> Dict[str, Any]:
        """Create a PIX billing (cobrança) in Asaas.

        `notificationDisabled=True` impede o Asaas de mandar SMS/WhatsApp/email
        automáticos pro cliente (cada envio era cobrado). Comunicação com o
        cliente é feita exclusivamente pelo portal (/portal/pedidos).

        `description` é sanitizada pra remover emojis/caracteres especiais
        que o Asaas rejeita com 400 parse_error (nomes de produto costumam
        trazer emojis tipo 😮‍💨💕🤌🏻).
        """
        safe_description = _sanitize_description(description) or "Cobranca"
        payment = self._request("POST", "payments", json={
            "customer": customer_id,
            "billingType": "PIX",
            "value": round(amount, 2),
            "dueDate": due_date,
            "description": safe_description,
            "notificationDisabled": True,
        })
        return payment

    def get_payment(self, payment_id: str) -> Dict[str, Any]:
        """Get payment details."""
        return self._request("GET", f"payments/{payment_id}")

    def get_payment_pix(self, payment_id: str) -> Dict[str, Any]:
        """Get PIX data for a payment."""
        payment = self.get_payment(payment_id)
        try:
            pix = self._request("GET", f"payments/{payment_id}/pixQrCode")
            payment["pix_payload"] = pix.get("payload")
            payment["pix_image"] = pix.get("encodedImage")
        except Exception:
            pass
        # Map Asaas fields to format expected by send_payment_whatsapp
        payment["paymentLink"] = payment.get("invoiceUrl") or payment.get("bankSlipUrl") or ""
        payment["transaction_amount"] = payment.get("value")
        return payment

    def get_payment_pix_with_retry(self, payment_id: str) -> Dict[str, Any]:
        """Get PIX data with retry logic for polling."""
        delay = self._poll_initial_delay
        last_exc = None
        for attempt in range(self._poll_retries):
            try:
                result = self.get_payment_pix(payment_id)
                if result.get("pix_payload") or result.get("paymentLink"):
                    return result
                logger.info("Asaas PIX poll attempt %d: no payload yet", attempt + 1)
            except Exception as exc:
                last_exc = exc
                logger.warning("Asaas PIX poll attempt %d failed: %s", attempt + 1, exc)
            time.sleep(min(delay, self._poll_max_delay))
            delay *= 1.5
        # Return whatever we have
        try:
            return self.get_payment_pix(payment_id)
        except Exception:
            if last_exc:
                raise last_exc
            raise RuntimeError(f"Failed to get PIX for {payment_id} after {self._poll_retries} attempts")

    def get_payment_status(self, payment_id: str) -> str:
        """Get current payment status."""
        payment = self.get_payment(payment_id)
        return payment.get("status", "PENDING")
