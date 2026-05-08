"""
Unit tests for Mercado Pago client.
"""
import pytest
import datetime
from unittest.mock import MagicMock
from integrations.mercadopago.client import MercadoPagoClient, MercadoPagoError

class DummySDKResponse:
    def __init__(self, status, response):
        self.status = status
        self.response = response

def test_init_missing_token(monkeypatch):
    # Patch both env vars and settings attributes so mp_access_token returns None
    monkeypatch.delenv("MP_ACCESS_TOKEN_TEST", raising=False)
    monkeypatch.delenv("MP_ACCESS_TOKEN_PROD", raising=False)
    monkeypatch.setattr("app.config.settings.TEST_MODE", False)
    monkeypatch.setattr("app.config.settings.MP_ACCESS_TOKEN_PROD", None)
    monkeypatch.setattr("app.config.settings.MP_ACCESS_TOKEN_TEST", None)

    with pytest.raises(ValueError, match="Mercado Pago access token not provided"):
        MercadoPagoClient(access_token=None)

def test_create_customer_success(monkeypatch):
    client = MercadoPagoClient(access_token="fake_token")
    mock_sdk = MagicMock()
    mock_sdk.customer().create.return_value = {"status": 201, "response": {"id": "123456"}}
    client.sdk = mock_sdk

    resp = client.create_customer(
        name="John Doe",
        email="john@example.com",
        cpf_cnpj="12345678909",
        phone="5511999999999",
        external_reference="pkg:123"
    )

    assert resp.get("id") == "123456"
    mock_sdk.customer().create.assert_called_once()
    args = mock_sdk.customer().create.call_args[0][0]
    assert args["email"] == "john@example.com"
    assert args["first_name"] == "John Doe"

def test_create_customer_failed(monkeypatch):
    client = MercadoPagoClient(access_token="fake_token")
    mock_sdk = MagicMock()
    mock_sdk.customer().create.return_value = {"status": 400, "response": {"message": "Invalid email"}}
    client.sdk = mock_sdk

    with pytest.raises(MercadoPagoError, match="Create customer failed: 400"):
        client.create_customer(name="John Doe", email="john", cpf_cnpj="123")

def test_create_payment_pix_success(monkeypatch):
    client = MercadoPagoClient(access_token="fake_token")
    captured = {}

    class _Response:
        status_code = 201

        def __init__(self):
            self.content = b"{}"

        def json(self):
            return {
                "id": "987654",
                "status": "pending",
                "point_of_interaction": {
                    "transaction_data": {
                        "qr_code": "000201...",
                        "qr_code_base64": "iVBORw0KGgo...",
                    }
                },
            }

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("integrations.mercadopago.client.requests.post", _fake_post)

    due_date = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat() + "Z"
    
    resp = client.create_payment_pix(
        amount=50.0,
        due_date=due_date,
        description="Test Payment",
        customer_email="john@example.com",
        customer_name="John Doe"
    )

    assert resp.get("id") == "987654"
    assert resp.get("status") == "pending"
    assert captured["url"].endswith("/v1/payments")
    args = captured["json"]
    assert args["transaction_amount"] == 50.0
    assert args["payment_method_id"] == "pix"
    assert args["payer"]["email"] == "john@example.com"
    assert args["payer"]["first_name"] == "John Doe"
    assert captured["headers"]["Authorization"] == "Bearer fake_token"


def test_get_payment_pix_failed(monkeypatch):
    client = MercadoPagoClient(access_token="fake_token")

    class _Response:
        status_code = 500

        def __init__(self):
            self.content = b"{}"

        def json(self):
            return {"message": "internal_error"}

    monkeypatch.setattr("integrations.mercadopago.client.requests.get", lambda *args, **kwargs: _Response())

    with pytest.raises(MercadoPagoError, match="Get payment failed: 500 internal_error"):
        client.get_payment_pix("mp-123")
