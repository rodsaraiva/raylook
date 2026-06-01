"""Testes do abate de crédito na geração de PIX combinado (portal).

credit_service roda contra SQLite REAL (pega erros de chave/ledger);
Asaas e get_client_orders ficam mockados.
"""
import pytest

from app.services import portal_service as ps
from app.services import credit_service as cs
from app.services.sqlite_service import SQLiteRestClient


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
    """Cobre só os acessos que create_combined_pix faz fora do credit_service:
    select de clientes (cpf) e o update de pagamentos (via _mark_paid_with_credit)."""
    def __init__(self):
        self.paid = []
    def select(self, table, columns=None, filters=None, limit=None):
        if table == "clientes":
            return [{"nome": "Ana", "celular": "5511999", "cpf_cnpj": "12345678900"}]
        return []
    def update(self, table, payload, filters=None):
        if table == "pagamentos" and payload.get("status") == "paid":
            pid = dict((f[0], f[2]) for f in (filters or [])).get("id")
            self.paid.append(pid)
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    # credit_service usa SQLite real; portal usa _FakeSb pros demais acessos
    real = SQLiteRestClient(db_path=str(tmp_path / "test.db"))
    real.insert("clientes", {"id": "C1", "nome": "Ana", "celular": "5511999"})
    monkeypatch.setattr(cs, "_client", lambda: real)

    fake = _FakeSb()
    monkeypatch.setattr(ps, "_client", lambda: fake)

    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)

    monkeypatch.setattr("app.services.runtime_state_service.runtime_state_enabled", lambda: False)
    return asaas, fake, real


def _orders(*amounts):
    return [
        {"status": "pending", "pagamento_id": f"P{i}", "total_amount": a}
        for i, a in enumerate(amounts)
    ]


def test_combined_partial_credit(env, monkeypatch):
    asaas, fake, real = env
    cs.add_credit("C1", 50.0, descricao="seed")
    monkeypatch.setattr(ps, "get_client_orders", lambda cid: _orders(120.0, 80.0))
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    out = ps.create_combined_pix("C1")

    assert out["saldo_antes"] == 50.0
    assert out["credito_aplicado"] == 50.0
    assert out["cobranca"] == 150.0
    assert asaas.created == [150.0]            # Asaas cobrado só pela diferença
    assert out.get("pago_com_credito") is not True
    # débito PENDING criado de verdade -> não conta no saldo ainda
    assert cs.get_balance("C1") == 50.0
    ledger = cs.get_ledger("C1")
    pend = [e for e in ledger if e["tipo"] == "debit" and e["status"] == "pending"]
    assert len(pend) == 1 and pend[0]["valor"] == 50.0
    # serialização: cancelou outras cobranças mantendo os pedidos deste PIX
    assert calls == [("C1", ["P0", "P1"])]


def test_combined_full_coverage(env, monkeypatch):
    asaas, fake, real = env
    cs.add_credit("C1", 300.0, descricao="seed")
    monkeypatch.setattr(ps, "get_client_orders", lambda cid: _orders(120.0, 80.0))
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    out = ps.create_combined_pix("C1")

    assert out["cobranca"] == 0.0
    assert out["pago_com_credito"] is True
    assert out["credito_aplicado"] == 200.0
    assert asaas.created == []                 # NÃO chamou Asaas
    assert sorted(fake.paid) == ["P0", "P1"]   # pagamentos marcados paid
    # débito CONFIRMED de 200 -> saldo cai de 300 para 100
    assert cs.get_balance("C1") == 100.0
    # serialização: cancela outras cobranças mesmo na cobertura total
    assert calls == [("C1", ["P0", "P1"])]


def test_combined_no_credit(env, monkeypatch):
    asaas, fake, real = env
    monkeypatch.setattr(ps, "get_client_orders", lambda cid: _orders(120.0))
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    out = ps.create_combined_pix("C1")

    assert out["saldo_antes"] == 0.0
    assert out["credito_aplicado"] == 0.0
    assert out["cobranca"] == 120.0
    assert asaas.created == [120.0]
    # nenhum débito criado quando não há crédito
    assert [e for e in cs.get_ledger("C1") if e["tipo"] == "debit"] == []
    # sem crédito aplicado → não serializa
    assert calls == []


class _FakeSbIndiv:
    """Serve as queries de get_or_create_pix; credit_service usa SQLite real à parte."""
    def __init__(self, pix_already=False, status="sent"):
        self.pix_already = pix_already
        self.status = status
        self.updates = []
    def select(self, table, columns=None, filters=None, limit=None):
        if table == "pagamentos":
            row = {"id": "P1", "venda_id": "V1", "status": self.status,
                   "provider_payment_id": None, "payment_link": None, "pix_payload": None}
            if self.pix_already:
                row["pix_payload"] = "OLD"; row["payment_link"] = "http://old"
            return [row]
        if table == "vendas":
            return [{"id": "V1", "cliente_id": "C1", "total_amount": 200.0, "qty": 2,
                     "produto": {"nome": "Camisa"}}]
        if table == "clientes":
            return [{"nome": "Ana", "celular": "5511999", "cpf_cnpj": "12345678900"}]
        return []
    def update(self, table, payload, filters=None):
        self.updates.append((table, payload))
        return []


# ---------------------------------------------------------------------------
# _cancel_other_open_charges (serialização do crédito)
# ---------------------------------------------------------------------------
#
# Setup: credit_service roda contra SQLite REAL (a remoção do débito pending é
# exercitada de verdade no ledger). portal_service._client() usa um fake DB
# leve (_FakeCancelSb) que devolve as linhas seedadas de pagamentos /
# app_runtime_state e grava update/delete — evita montar a cadeia de FKs
# pesada de vendas/pagamentos (pacote→enquete→produto) só pra esse teste.


class FakeCancelAsaas:
    def __init__(self):
        self.cancelled = []
    def cancel_payment(self, payment_id):
        self.cancelled.append(payment_id)
        return {"id": payment_id, "deleted": True}


class _FakeCancelSb:
    def __init__(self, pagamentos=None, states=None):
        self._pagamentos = pagamentos or []
        self._states = states or []
        self.updates = []
        self.deleted_states = []
    def select_all(self, table, columns=None, filters=None, order=None):
        if table == "pagamentos":
            wanted = None
            for f in filters or []:
                if f[0] == "status" and f[1] == "in":
                    wanted = set(f[2])
            return [r for r in self._pagamentos
                    if wanted is None or r.get("status") in wanted]
        if table == "app_runtime_state":
            return list(self._states)
        return []
    def update(self, table, payload, filters=None):
        self.updates.append((table, payload, dict((f[0], f[2]) for f in (filters or []))))
        if table == "pagamentos":
            pid = dict((f[0], f[2]) for f in (filters or [])).get("id")
            for r in self._pagamentos:
                if str(r.get("id")) == str(pid):
                    r.update(payload)
        return []


def _make_cancel_env(tmp_path, monkeypatch, pagamentos=None, states=None):
    real = SQLiteRestClient(db_path=str(tmp_path / "cancel.db"))
    real.insert("clientes", {"id": "C1", "nome": "Ana", "celular": "5511999"})
    monkeypatch.setattr(cs, "_client", lambda: real)

    fake = _FakeCancelSb(pagamentos=pagamentos, states=states)
    monkeypatch.setattr(ps, "_client", lambda: fake)

    asaas = FakeCancelAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)

    deleted = []
    monkeypatch.setattr(
        "app.services.runtime_state_service.delete_runtime_state",
        lambda key: deleted.append(key),
    )
    fake.deleted_states = deleted
    return real, fake, asaas, deleted


def test_cancel_individual_charge(tmp_path, monkeypatch):
    real, fake, asaas, _ = _make_cancel_env(
        tmp_path, monkeypatch,
        pagamentos=[{"id": "PA", "provider_payment_id": "pay_old", "status": "sent",
                     "venda": {"cliente_id": "C1"}}],
    )
    cs.add_credit("C1", 100.0, descricao="seed")
    cs.add_pending_debit("C1", 30.0, pagamento_id="PA")

    ps._cancel_other_open_charges("C1", keep_pagamento_ids=[])

    assert "pay_old" in asaas.cancelled
    # pagamento resetado
    row = [r for r in fake._pagamentos if r["id"] == "PA"][0]
    assert row["provider_payment_id"] is None
    assert row["status"] == "created"
    # débito pending removido de verdade no ledger SQLite
    assert [e for e in cs.get_ledger("C1") if e["tipo"] == "debit"] == []


def test_cancel_skips_kept_pagamento(tmp_path, monkeypatch):
    real, fake, asaas, _ = _make_cancel_env(
        tmp_path, monkeypatch,
        pagamentos=[{"id": "PA", "provider_payment_id": "pay_old", "status": "sent",
                     "venda": {"cliente_id": "C1"}}],
    )
    cs.add_credit("C1", 100.0, descricao="seed")
    cs.add_pending_debit("C1", 30.0, pagamento_id="PA")

    ps._cancel_other_open_charges("C1", keep_pagamento_ids=["PA"])

    assert asaas.cancelled == []
    row = [r for r in fake._pagamentos if r["id"] == "PA"][0]
    assert row["provider_payment_id"] == "pay_old"  # não resetado
    pend = [e for e in cs.get_ledger("C1") if e["tipo"] == "debit" and e["status"] == "pending"]
    assert len(pend) == 1  # débito preservado


def test_cancel_other_client_untouched(tmp_path, monkeypatch):
    real, fake, asaas, _ = _make_cancel_env(
        tmp_path, monkeypatch,
        pagamentos=[{"id": "PA", "provider_payment_id": "pay_old", "status": "sent",
                     "venda": {"cliente_id": "C2"}}],
    )
    ps._cancel_other_open_charges("C1", keep_pagamento_ids=[])
    assert asaas.cancelled == []


def test_cancel_combined_charge(tmp_path, monkeypatch):
    real, fake, asaas, deleted = _make_cancel_env(
        tmp_path, monkeypatch,
        states=[{"key": "combined_pix_payX",
                 "payload_json": {"pagamento_ids": ["PB"], "cliente_id": "C1"}}],
    )
    cs.add_credit("C1", 100.0, descricao="seed")
    cs.add_pending_debit("C1", 60.0, asaas_payment_id="payX")

    ps._cancel_other_open_charges("C1", keep_pagamento_ids=[])

    assert "payX" in asaas.cancelled
    assert "combined_pix_payX" in deleted
    assert [e for e in cs.get_ledger("C1") if e["tipo"] == "debit"] == []


def test_cancel_combined_skips_when_covers_only_keep(tmp_path, monkeypatch):
    real, fake, asaas, deleted = _make_cancel_env(
        tmp_path, monkeypatch,
        states=[{"key": "combined_pix_payX",
                 "payload_json": {"pagamento_ids": ["PB"], "cliente_id": "C1"}}],
    )
    cs.add_credit("C1", 100.0, descricao="seed")
    cs.add_pending_debit("C1", 60.0, asaas_payment_id="payX")

    ps._cancel_other_open_charges("C1", keep_pagamento_ids=["PB"])

    assert asaas.cancelled == []
    assert deleted == []
    pend = [e for e in cs.get_ledger("C1") if e["tipo"] == "debit" and e["status"] == "pending"]
    assert len(pend) == 1


def test_cancel_combined_payload_as_json_string(tmp_path, monkeypatch):
    import json
    real, fake, asaas, deleted = _make_cancel_env(
        tmp_path, monkeypatch,
        states=[{"key": "combined_pix_payY",
                 "payload_json": json.dumps({"pagamento_ids": ["PB"], "cliente_id": "C1"})}],
    )
    cs.add_credit("C1", 100.0, descricao="seed")
    cs.add_pending_debit("C1", 60.0, asaas_payment_id="payY")

    ps._cancel_other_open_charges("C1", keep_pagamento_ids=[])

    assert "payY" in asaas.cancelled
    assert "combined_pix_payY" in deleted


def test_individual_partial_credit(tmp_path, monkeypatch):
    from app.services.sqlite_service import SQLiteRestClient
    real = SQLiteRestClient(db_path=str(tmp_path / "i1.db"))
    real.insert("clientes", {"id": "C1", "nome": "Ana", "celular": "5511999"})
    monkeypatch.setattr(cs, "_client", lambda: real)
    cs.add_credit("C1", 50.0, descricao="seed")

    fake = _FakeSbIndiv()
    monkeypatch.setattr(ps, "_client", lambda: fake)
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    out = ps.get_or_create_pix("P1", "C1")

    assert out["credito_aplicado"] == 50.0
    assert out["cobranca"] == 150.0
    assert asaas.created == [150.0]
    assert cs.get_balance("C1") == 50.0   # pending não conta
    pend = [e for e in cs.get_ledger("C1") if e["tipo"] == "debit" and e["status"] == "pending"]
    assert len(pend) == 1 and pend[0]["valor"] == 50.0
    # serialização: cancela outras cobranças mantendo este pagamento
    assert calls == [("C1", ["P1"])]


def test_individual_full_coverage(tmp_path, monkeypatch):
    from app.services.sqlite_service import SQLiteRestClient
    real = SQLiteRestClient(db_path=str(tmp_path / "i2.db"))
    real.insert("clientes", {"id": "C1", "nome": "Ana", "celular": "5511999"})
    monkeypatch.setattr(cs, "_client", lambda: real)
    cs.add_credit("C1", 500.0, descricao="seed")

    fake = _FakeSbIndiv()
    monkeypatch.setattr(ps, "_client", lambda: fake)
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    out = ps.get_or_create_pix("P1", "C1")

    assert out["pago_com_credito"] is True
    assert out["cobranca"] == 0.0
    assert asaas.created == []
    assert cs.get_balance("C1") == 300.0   # debit confirmado de 200
    assert any(t == "pagamentos" and p.get("status") == "paid" for t, p in fake.updates)
    # serialização: cancela outras cobranças mantendo este pagamento
    assert calls == [("C1", ["P1"])]


def test_individual_reentrancy_does_not_reapply(tmp_path, monkeypatch):
    from app.services.sqlite_service import SQLiteRestClient
    real = SQLiteRestClient(db_path=str(tmp_path / "i3.db"))
    real.insert("clientes", {"id": "C1", "nome": "Ana", "celular": "5511999"})
    monkeypatch.setattr(cs, "_client", lambda: real)
    cs.add_credit("C1", 1000.0, descricao="seed")
    # débito pendente já registrado de uma geração anterior (R$50 sobre os R$200)
    cs.add_pending_debit("C1", 50.0, pagamento_id="P1", descricao="anterior")

    fake = _FakeSbIndiv(pix_already=True)
    monkeypatch.setattr(ps, "_client", lambda: fake)
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    calls = []
    monkeypatch.setattr(ps, "_cancel_other_open_charges",
                        lambda cid, keep_pagamento_ids: calls.append((cid, list(keep_pagamento_ids))))

    saldo_before = cs.get_balance("C1")
    out = ps.get_or_create_pix("P1", "C1")

    assert asaas.created == []                 # não criou novo PIX
    assert out.get("pago_com_credito") is not True
    assert out["credito_aplicado"] == 50.0     # reporta o débito já registrado
    assert out["cobranca"] == 150.0            # 200 - 50
    # nenhum débito novo, nada marcado paid
    assert cs.get_balance("C1") == saldo_before
    assert not any(p.get("status") == "paid" for _, p in fake.updates)
    debits = [e for e in cs.get_ledger("C1") if e["tipo"] == "debit"]
    assert len(debits) == 1                    # continua só o débito anterior
    # reentrância (PIX já existente) não aplica crédito novo → não serializa
    assert calls == []
