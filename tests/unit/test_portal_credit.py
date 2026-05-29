"""Testes do abate de crédito na geração de PIX (portal)."""
import pytest

from app.services import portal_service as ps


class FakeAsaas:
    def __init__(self):
        self.created = []
    def create_customer(self, name, phone, cpf_cnpj):
        return {"id": "cus_1"}
    def create_payment_pix(self, customer_id, amount, due, description):
        self.created.append(amount)
        return {"id": "pay_1", "invoiceUrl": "http://x"}
    def get_payment_pix_with_retry(self, pid):
        return {"pix_payload": "PIXPAYLOAD", "paymentLink": "http://link"}


class _FakeSb:
    def select(self, table, columns=None, filters=None, limit=None):
        if table == "clientes":
            return [{"nome": "Ana", "celular": "5511999", "cpf_cnpj": "12345678900"}]
        return []
    def update(self, *a, **k):
        return []


@pytest.fixture
def env(monkeypatch):
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    monkeypatch.setattr(ps, "_client", lambda: _FakeSb())
    return asaas


def _orders(*amounts):
    return [
        {"status": "pending", "pagamento_id": f"P{i}", "total_amount": a}
        for i, a in enumerate(amounts)
    ]


def test_combined_partial_credit(env, monkeypatch):
    monkeypatch.setattr(ps, "get_client_orders", lambda cid: _orders(120.0, 80.0))
    monkeypatch.setattr("app.services.credit_service.get_balance", lambda cid: 50.0)
    pending_debits = []
    monkeypatch.setattr(
        "app.services.credit_service.add_pending_debit",
        lambda cid, valor, **kw: pending_debits.append((valor, kw)),
    )

    out = ps.create_combined_pix("C1")

    assert out["saldo_antes"] == 50.0
    assert out["credito_aplicado"] == 50.0
    assert out["cobranca"] == 150.0
    assert env.created == [150.0]            # Asaas cobrado só pela diferença
    assert pending_debits and pending_debits[0][0] == 50.0
    assert pending_debits[0][1].get("asaas_payment_id") == "pay_1"
    assert out.get("pago_com_credito") is not True


def test_combined_full_coverage(env, monkeypatch):
    monkeypatch.setattr(ps, "get_client_orders", lambda cid: _orders(120.0, 80.0))
    monkeypatch.setattr("app.services.credit_service.get_balance", lambda cid: 300.0)
    confirmed = []
    paid_marked = []
    monkeypatch.setattr(
        "app.services.credit_service.add_confirmed_debit",
        lambda cid, valor, **kw: confirmed.append(valor),
    )
    monkeypatch.setattr(ps, "_mark_paid_with_credit", lambda ids: paid_marked.extend(ids), raising=False)

    out = ps.create_combined_pix("C1")

    assert out["cobranca"] == 0.0
    assert out["pago_com_credito"] is True
    assert out["credito_aplicado"] == 200.0
    assert env.created == []                 # NÃO chamou Asaas
    assert confirmed == [200.0]
    assert sorted(paid_marked) == ["P0", "P1"]
