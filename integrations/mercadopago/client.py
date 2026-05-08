import os
from typing import Optional, Dict, Any
import logging
import requests
import mercadopago
from app.config import settings
import uuid

logger = logging.getLogger("raylook.integrations.mercadopago")

class MercadoPagoError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response

class MercadoPagoClient:
    def __init__(self, access_token: Optional[str] = None):
        self.token = access_token or settings.mp_access_token
        if not self.token:
            raise ValueError("Mercado Pago access token not provided (MP_ACCESS_TOKEN_TEST or MP_ACCESS_TOKEN_PROD)")

        # Initialize MercadoPago SDK
        self.sdk = mercadopago.SDK(self.token)
        self.base_url = os.getenv("MP_API_URL", "https://api.mercadopago.com").rstrip("/")
        self.timeout = float(os.getenv("MP_REQUEST_TIMEOUT_SECONDS", "20"))

    def _headers(self, *, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    def create_customer(
        self,
        name: str,
        email: Optional[str],
        cpf_cnpj: str,
        phone: Optional[str] = None,
        external_reference: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new customer in Mercado Pago.
        """
        customer_data = {
            "first_name": name,
            "identification": {
                "type": "CNPJ" if len(cpf_cnpj) > 11 else "CPF",
                "number": cpf_cnpj
            }
        }
        
        # O Mercado Pago valida estritamente o formato de e-mail, mas o Asaas permitia testes.
        # Vamos passar um e-mail default se não houver ou se for inválido, mas a princípio aceitaremos o fornecido.
        if email:
            customer_data["email"] = email
        else:
            customer_data["email"] = f"cliente_{uuid.uuid4().hex[:8]}@teste.com"

        if phone:
            # Separar DDI, DDD e Número se possível. Simplificado aqui
            clean_phone = ''.join(filter(str.isdigit, phone))
            if len(clean_phone) >= 10:
                area_code = clean_phone[:2]
                number = clean_phone[2:]
                if clean_phone.startswith("55") and len(clean_phone) > 11:
                    area_code = clean_phone[2:4]
                    number = clean_phone[4:]
                    
                customer_data["phone"] = {
                    "area_code": area_code,
                    "number": number
                }

        request_options = None
        # O Mercado Pago suporta chaves de idempotência em requests via request_options, 
        # para evitar duplicidade na criação de customer.
        if external_reference:
            customer_data["metadata"] = {
                "external_reference": external_reference
            }

        response = self.sdk.customer().create(customer_data, request_options)
        
        status = response.get("status")
        if status not in (200, 201):
            error_msg = response.get('response', {}).get('message', 'Unknown Error')
            logger.error("[MercadoPago] create_customer failed: %s %s", status, error_msg)
            raise MercadoPagoError(f"Create customer failed: {status} {error_msg}", status_code=status, response=response)
            
        return response.get("response", {})

    def get_customer(self, customer_email: str) -> Optional[Dict[str, Any]]:
        """
        Busca o cliente pelo email.
        """
        filters = {"email": customer_email}
        response = self.sdk.customer().search(filters)
        
        status = response.get("status")
        if status in (200, 201) and response.get("response", {}).get("results"):
            return response["response"]["results"][0]
        return None

    def create_payment_pix(
        self, 
        amount: float, 
        due_date: str, 
        description: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_name: Optional[str] = "Cliente Genérico"
    ) -> Dict[str, Any]:
        """
        Create a PIX payment without requiring a Customer ID.
        Returns the payment resource as JSON.
        """
        import uuid
        email_valido = customer_email if customer_email else f"cliente_{uuid.uuid4().hex[:8]}@teste.com"

        payment_data = {
            "transaction_amount": float(amount),
            "description": description or "Pagamento de Pacote",
            "payment_method_id": "pix",
            "payer": {
                "email": email_valido,
                "first_name": customer_name
            },
            "date_of_expiration": due_date  # Must be in ISO 8601, e.g., 2026-03-15T23:59:59.000-03:00
        }

        try:
            response = requests.post(
                f"{self.base_url}/v1/payments",
                json=payment_data,
                headers=self._headers(idempotency_key=uuid.uuid4().hex),
                timeout=(5, self.timeout),
            )
            response_json = response.json() if response.content else {}
        except Exception as e:
            logger.error("[MercadoPago] create_payment_pix exception: %s", str(e))
            raise MercadoPagoError(f"Create payment exception: {str(e)}")

        status = int(response.status_code)
        if status not in (200, 201):
            error_msg = response_json.get("message", "Unknown Error")
            logger.error("[MercadoPago] create_payment_pix failed: %s %s", status, error_msg)
            raise MercadoPagoError(
                f"Create payment failed: {status} {error_msg}",
                status_code=status,
                response=response_json,
            )

        return response_json

    def get_payment_pix(self, payment_id: str) -> Dict[str, Any]:
        """Retrieve PIX payment data to check status or get QR Code again."""
        try:
            response = requests.get(
                f"{self.base_url}/v1/payments/{payment_id}",
                headers=self._headers(),
                timeout=(5, self.timeout),
            )
            response_json = response.json() if response.content else {}
        except Exception as e:
            logger.error("[MercadoPago] get_payment_pix exception: %s", str(e))
            raise MercadoPagoError(f"Get payment exception: {str(e)}")

        status = int(response.status_code)
        if status not in (200, 201):
            error_msg = response_json.get("message", "Unknown Error")
            raise MercadoPagoError(
                f"Get payment failed: {status} {error_msg}",
                status_code=status,
                response=response_json,
            )

        return response_json

    def get_payment_pix_with_retry(self, payment_id: str, max_retries: int = 3, initial_delay: float = 1.0) -> Dict[str, Any]:
        """
        No Mercado Pago, o PIX (QR Code e Copy/Paste) geralmente já vem na resposta de criação.
        Mas mantemos este método para compatibilidade de fluxo caso o webhook atrase ou se quisermos forçar o refresh.
        """
        import time
        delay = initial_delay
        for attempt in range(max_retries):
            try:
                data = self.get_payment_pix(payment_id)
                poi = data.get("point_of_interaction", {})
                if poi and poi.get("transaction_data"):
                    # Se temos o transaction_data (que contém o QR Code) e o status é pending
                    return data
                
                # Payment might be approved already or data is missing
                time.sleep(delay)
                delay *= 2
            except MercadoPagoError as e:
                time.sleep(delay)
                delay *= 2
        
        # Last attempt without catching
        return self.get_payment_pix(payment_id)
