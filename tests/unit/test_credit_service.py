"""Testes do credit_service usando SQLite real (sem mock de DB)."""
import pytest

from app.services import credit_service as cs
from app.services.sqlite_service import SQLiteRestClient


@pytest.fixture
def db(tmp_path, monkeypatch):
    client = SQLiteRestClient(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(cs, "_client", lambda: client)
    cli = client.insert("clientes", {"nome": "Ana", "celular": "5511999"})[0]
    return client, cli["id"]


def test_balance_empty(db):
    _, cid = db
    assert cs.get_balance(cid) == 0.0


def test_add_credit_increases_balance(db):
    _, cid = db
    cs.add_credit(cid, 150.0, descricao="cancelamento")
    assert cs.get_balance(cid) == 150.0


def test_add_credit_idempotent_by_venda(db):
    client, cid = db
    cs.add_credit(cid, 150.0, venda_id="V1")
    cs.add_credit(cid, 150.0, venda_id="V1")
    assert cs.get_balance(cid) == 150.0


def test_pending_debit_not_counted(db):
    _, cid = db
    cs.add_credit(cid, 100.0)
    cs.add_pending_debit(cid, 30.0, pagamento_id="P1")
    assert cs.get_balance(cid) == 100.0


def test_confirm_debit_subtracts(db):
    _, cid = db
    cs.add_credit(cid, 100.0)
    cs.add_pending_debit(cid, 30.0, pagamento_id="P1")
    cs.confirm_debit(pagamento_id="P1")
    assert cs.get_balance(cid) == 70.0


def test_confirm_debit_idempotent(db):
    _, cid = db
    cs.add_credit(cid, 100.0)
    cs.add_pending_debit(cid, 30.0, pagamento_id="P1")
    cs.confirm_debit(pagamento_id="P1")
    cs.confirm_debit(pagamento_id="P1")
    assert cs.get_balance(cid) == 70.0


def test_pending_debit_dedup(db):
    _, cid = db
    cs.add_credit(cid, 100.0)
    cs.add_pending_debit(cid, 30.0, pagamento_id="P1")
    cs.add_pending_debit(cid, 30.0, pagamento_id="P1")
    cs.confirm_debit(pagamento_id="P1")
    assert cs.get_balance(cid) == 70.0


def test_confirmed_debit_full_coverage(db):
    _, cid = db
    cs.add_credit(cid, 300.0)
    cs.add_confirmed_debit(cid, 200.0, pagamento_id="P9", descricao="pago com crédito")
    assert cs.get_balance(cid) == 100.0


def test_confirm_debit_by_asaas_id(db):
    _, cid = db
    cs.add_credit(cid, 100.0)
    cs.add_pending_debit(cid, 40.0, asaas_payment_id="pay_x")
    cs.confirm_debit(asaas_payment_id="pay_x")
    assert cs.get_balance(cid) == 60.0


def test_ledger_lists_entries(db):
    _, cid = db
    cs.add_credit(cid, 100.0, descricao="c1")
    cs.add_confirmed_debit(cid, 40.0, pagamento_id="P1", descricao="d1")
    ledger = cs.get_ledger(cid)
    assert len(ledger) == 2
    assert {e["tipo"] for e in ledger} == {"credit", "debit"}


def test_list_balances_positive_only(db):
    client, cid = db
    other = client.insert("clientes", {"nome": "Bia", "celular": "5511888"})[0]["id"]
    cs.add_credit(cid, 100.0)
    cs.add_credit(other, 50.0)
    cs.add_confirmed_debit(other, 50.0, pagamento_id="PX")  # saldo 0 -> não aparece
    balances = cs.list_balances()
    ids = {b["cliente_id"]: b["saldo"] for b in balances}
    assert ids == {cid: 100.0}
    assert balances[0]["nome"] == "Ana"
