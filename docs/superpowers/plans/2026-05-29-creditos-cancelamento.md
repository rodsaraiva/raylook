# Sistema de Créditos por Cancelamento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando um pacote pago é cancelado, o valor pago vira crédito na plataforma, abatido automaticamente nas próximas compras do cliente, visível no financeiro (extrato+saldo) e no portal (saldo).

**Architecture:** Nova tabela `creditos` (ledger) é a fonte única de verdade. Cancelamento grava `credit`; geração de PIX grava `debit` (pendente até a confirmação do pagamento, ou confirmado na hora se o crédito cobre tudo). Saldo = soma dos lançamentos `confirmed`. Toda persistência em Postgres/SQLite via `SupabaseRestClient.from_settings()`.

**Tech Stack:** Python 3.12, FastAPI, PostgREST (Postgres prod) / SQLiteRestClient (dev/teste), Jinja2, JS vanilla. Testes: pytest com SQLite real.

**Spec:** `docs/superpowers/specs/2026-05-29-creditos-cancelamento-design.md`

---

## Estrutura de arquivos

| Arquivo | Responsabilidade | Ação |
|---|---|---|
| `deploy/postgres/schema.sql` | Schema canônico Postgres | Modify (add `creditos`) |
| `deploy/sqlite/schema.sql` | Schema canônico SQLite | Modify (add `creditos`) |
| `deploy/migrations/2026-05-29-creditos.sql` | Migration idempotente prod | Create |
| `app/services/sqlite_service.py` | Registrar `creditos` (uuid+timestamp) | Modify |
| `app/services/credit_service.py` | Ledger: saldo, extrato, add/confirm | Create |
| `app/services/package_cancellation_service.py` | Gera crédito ao cancelar pago | Modify |
| `app/services/portal_service.py` | Abate crédito no PIX (indiv./combinado) | Modify |
| `app/services/asaas_sync_service.py` | Confirma débito ao confirmar pagamento | Modify |
| `app/routers/finance.py` | `GET /api/finance/credits` | Modify |
| `app/routers/portal.py` | Injeta saldo no portal | Modify |
| `templates/portal_pedidos.html` | KPI saldo + crédito aplicado no PIX | Modify |
| `templates/dashboard_v2.html` | Aba "Créditos" no financeiro | Modify |
| `static/js/dashboard_v2.js` | Render da aba Créditos | Modify |
| `tests/unit/test_credit_service.py` | Testes do ledger (SQLite real) | Create |
| `tests/unit/test_package_cancellation_service.py` | Testes do crédito no cancelamento | Modify |
| `tests/unit/test_portal_credit.py` | Testes do abate no PIX | Create |
| `tests/unit/test_asaas_sync_service.py` | Teste confirma débito | Modify |

Convenção de teste: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/<arquivo> -v`

---

## Task 1: Schema da tabela `creditos`

**Files:**
- Modify: `deploy/postgres/schema.sql` (após o bloco `CREATE TABLE ... pagamentos`)
- Modify: `deploy/sqlite/schema.sql` (após o bloco `CREATE TABLE ... pagamentos`)
- Modify: `app/services/sqlite_service.py` (`_UUID_PK_TABLES`, `_TIMESTAMP_COLUMNS`)

- [ ] **Step 1: Adicionar tabela ao schema Postgres**

Em `deploy/postgres/schema.sql`, após o `CREATE TABLE IF NOT EXISTS pagamentos (...);`:

```sql
CREATE TABLE IF NOT EXISTS creditos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    cliente_id text NOT NULL REFERENCES clientes(id),
    tipo text NOT NULL CHECK (tipo IN ('credit', 'debit')),
    status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed')),
    valor numeric NOT NULL CHECK (valor > 0),
    pacote_id text REFERENCES pacotes(id),
    venda_id text REFERENCES vendas(id),
    pagamento_id text REFERENCES pagamentos(id),
    asaas_payment_id text,
    descricao text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON creditos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_creditos_pagamento ON creditos(pagamento_id);
CREATE INDEX IF NOT EXISTS idx_creditos_asaas ON creditos(asaas_payment_id);
```

- [ ] **Step 2: Adicionar tabela ao schema SQLite**

Em `deploy/sqlite/schema.sql`, após o `CREATE TABLE IF NOT EXISTS pagamentos (...);`:

```sql
CREATE TABLE IF NOT EXISTS creditos (
    id TEXT PRIMARY KEY,
    cliente_id TEXT NOT NULL REFERENCES clientes(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('credit', 'debit')),
    status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed')),
    valor REAL NOT NULL CHECK (valor > 0),
    pacote_id TEXT REFERENCES pacotes(id),
    venda_id TEXT REFERENCES vendas(id),
    pagamento_id TEXT REFERENCES pagamentos(id),
    asaas_payment_id TEXT,
    descricao TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON creditos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_creditos_pagamento ON creditos(pagamento_id);
CREATE INDEX IF NOT EXISTS idx_creditos_asaas ON creditos(asaas_payment_id);
```

- [ ] **Step 3: Registrar `creditos` no sqlite_service**

Em `app/services/sqlite_service.py`, adicionar `"creditos",` ao set `_UUID_PK_TABLES` e a linha `"creditos": ("created_at",),` ao dict `_TIMESTAMP_COLUMNS`.

- [ ] **Step 4: Verificar que o SQLite cria a tabela**

Run:
```bash
cd /root/rodrigo/raylook
python3 -c "
from app.services.sqlite_service import SQLiteRestClient
import tempfile, os
db = tempfile.mktemp(suffix='.db')
c = SQLiteRestClient(db_path=db)
cli = c.insert('clientes', {'nome':'Ana','celular':'5511999'})[0]
row = c.insert('creditos', {'cliente_id': cli['id'], 'tipo':'credit', 'valor': 10.0})[0]
assert row['id'] and row['status']=='confirmed' and row['created_at'], row
print('OK', row['valor'], row['status'])
os.remove(db)
"
```
Expected: `OK 10.0 confirmed`

- [ ] **Step 5: Commit**

```bash
git add deploy/postgres/schema.sql deploy/sqlite/schema.sql app/services/sqlite_service.py
git commit -m "feat(creditos): tabela ledger de créditos (postgres + sqlite)"
```

---

## Task 2: `credit_service` — saldo, extrato e lançamentos

**Files:**
- Create: `app/services/credit_service.py`
- Test: `tests/unit/test_credit_service.py`

- [ ] **Step 1: Escrever os testes (SQLite real)**

`tests/unit/test_credit_service.py`:

```python
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
    assert cs.get_balance(cid) == 100.0  # pendente não conta


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
```

- [ ] **Step 2: Rodar os testes (devem falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_credit_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.credit_service'`

- [ ] **Step 3: Implementar `credit_service.py`**

`app/services/credit_service.py`:

```python
"""Ledger de créditos do cliente.

Lançamentos:
  - credit (confirmed): gerado no cancelamento de pacote pago.
  - debit (pending):    reserva ao gerar PIX com crédito; não conta no saldo.
  - debit (confirmed):  abate efetivo (PIX pago ou cobertura total sem PIX).

Saldo = SUM(credit confirmed) − SUM(debit confirmed). Fonte única de verdade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.credit")


def _client() -> SupabaseRestClient:
    return SupabaseRestClient.from_settings()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_embed(value: Any) -> Dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def get_balance(cliente_id: str) -> float:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="tipo,valor",
        filters=[("cliente_id", "eq", cliente_id), ("status", "eq", "confirmed")],
    )
    total = 0.0
    for r in rows or []:
        v = float(r.get("valor") or 0)
        total += v if r.get("tipo") == "credit" else -v
    return round(total, 2)


def get_ledger(cliente_id: str) -> List[Dict[str, Any]]:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="id,tipo,status,valor,pacote_id,venda_id,pagamento_id,descricao,created_at",
        filters=[("cliente_id", "eq", cliente_id)],
        order="created_at.desc",
    )
    return rows or []


def list_balances() -> List[Dict[str, Any]]:
    sb = _client()
    rows = sb.select_all(
        "creditos",
        columns="cliente_id,tipo,valor,cliente:cliente_id(nome,celular)",
        filters=[("status", "eq", "confirmed")],
    )
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        cid = r.get("cliente_id")
        if not cid:
            continue
        cliente = _normalize_embed(r.get("cliente"))
        entry = agg.setdefault(cid, {
            "cliente_id": cid,
            "nome": cliente.get("nome") or "",
            "celular": cliente.get("celular") or "",
            "saldo": 0.0,
        })
        v = float(r.get("valor") or 0)
        entry["saldo"] += v if r.get("tipo") == "credit" else -v
    result = [
        {**e, "saldo": round(e["saldo"], 2)}
        for e in agg.values()
        if round(e["saldo"], 2) > 0
    ]
    result.sort(key=lambda e: e["saldo"], reverse=True)
    return result


def add_credit(
    cliente_id: str,
    valor: float,
    *,
    pacote_id: Optional[str] = None,
    venda_id: Optional[str] = None,
    descricao: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    valor = round(float(valor), 2)
    if valor <= 0:
        return None
    sb = _client()
    if venda_id:
        existing = sb.select(
            "creditos",
            columns="id",
            filters=[("venda_id", "eq", venda_id), ("tipo", "eq", "credit")],
            limit=1,
        )
        if isinstance(existing, list) and existing:
            return existing[0]
    payload = {
        "cliente_id": cliente_id, "tipo": "credit", "status": "confirmed",
        "valor": valor, "pacote_id": pacote_id, "venda_id": venda_id,
        "descricao": descricao, "created_by": created_by, "created_at": _now_iso(),
    }
    rows = sb.insert("creditos", {k: v for k, v in payload.items() if v is not None})
    return rows[0] if rows else None


def _existing_debit(sb, pagamento_id, asaas_payment_id):
    filters = [("tipo", "eq", "debit")]
    if pagamento_id:
        filters.append(("pagamento_id", "eq", pagamento_id))
    elif asaas_payment_id:
        filters.append(("asaas_payment_id", "eq", asaas_payment_id))
    else:
        raise ValueError("pagamento_id ou asaas_payment_id obrigatório")
    rows = sb.select("creditos", columns="id", filters=filters, limit=1)
    return rows[0] if isinstance(rows, list) and rows else None


def _add_debit(
    cliente_id: str,
    valor: float,
    *,
    status: str,
    pagamento_id: Optional[str] = None,
    asaas_payment_id: Optional[str] = None,
    descricao: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    valor = round(float(valor), 2)
    if valor <= 0:
        return None
    sb = _client()
    found = _existing_debit(sb, pagamento_id, asaas_payment_id)
    if found:
        return found
    payload = {
        "cliente_id": cliente_id, "tipo": "debit", "status": status,
        "valor": valor, "pagamento_id": pagamento_id,
        "asaas_payment_id": asaas_payment_id, "descricao": descricao,
        "created_at": _now_iso(),
    }
    rows = sb.insert("creditos", {k: v for k, v in payload.items() if v is not None})
    return rows[0] if rows else None


def add_pending_debit(cliente_id, valor, *, pagamento_id=None, asaas_payment_id=None, descricao=None):
    return _add_debit(cliente_id, valor, status="pending",
                      pagamento_id=pagamento_id, asaas_payment_id=asaas_payment_id, descricao=descricao)


def add_confirmed_debit(cliente_id, valor, *, pagamento_id=None, asaas_payment_id=None, descricao=None):
    return _add_debit(cliente_id, valor, status="confirmed",
                      pagamento_id=pagamento_id, asaas_payment_id=asaas_payment_id, descricao=descricao)


def confirm_debit(*, pagamento_id: Optional[str] = None, asaas_payment_id: Optional[str] = None) -> None:
    sb = _client()
    filters = [("tipo", "eq", "debit"), ("status", "eq", "pending")]
    if pagamento_id:
        filters.append(("pagamento_id", "eq", pagamento_id))
    elif asaas_payment_id:
        filters.append(("asaas_payment_id", "eq", asaas_payment_id))
    else:
        raise ValueError("pagamento_id ou asaas_payment_id obrigatório")
    sb.update("creditos", {"status": "confirmed"}, filters=filters)
```

- [ ] **Step 4: Rodar os testes (devem passar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_credit_service.py -v`
Expected: PASS (11 testes)

- [ ] **Step 5: Commit**

```bash
git add app/services/credit_service.py tests/unit/test_credit_service.py
git commit -m "feat(creditos): credit_service (saldo, extrato, débito pending/confirmed)"
```

---

## Task 3: Gerar crédito no cancelamento de pacote pago

**Files:**
- Modify: `app/services/package_cancellation_service.py:176-232` (loop de cancelamento + retorno) e `preview_cancel`
- Test: `tests/unit/test_package_cancellation_service.py`

- [ ] **Step 1: Escrever o teste (estilo do arquivo — fake client + spy no credit_service)**

Adicionar em `tests/unit/test_package_cancellation_service.py`:

```python
def test_cancel_paid_generates_credit(monkeypatch):
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 6,
            "total_amount": 120, "cliente_id": "C1",
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": {"id": "P1", "status": "paid", "paid_at": "2026-05-01T00:00:00Z"},
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    credits = []
    from app.services import credit_service
    monkeypatch.setattr(
        credit_service, "add_credit",
        lambda cliente_id, valor, **kw: credits.append({"cliente_id": cliente_id, "valor": valor, **kw}),
    )

    result = pcs.cancel_package("PKG-1", force=True, cancelled_by="admin")

    # creditou 100% do valor pago
    assert len(credits) == 1
    assert credits[0]["cliente_id"] == "C1"
    assert credits[0]["valor"] == 120
    assert credits[0]["venda_id"] == "V1"
    # venda e pagamento pagos viram cancelled
    patched = {(c["path"].split("?")[0], c["payload"].get("status")) for c in fake.patch_calls}
    assert ("/rest/v1/vendas", "cancelled") in patched
    assert ("/rest/v1/pagamentos", "cancelled") in patched
    assert result["credited_total"] == 120
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_package_cancellation_service.py::test_cancel_paid_generates_credit -v`
Expected: FAIL — `add_credit` não chamado / `credited_total` ausente.

- [ ] **Step 3: Buscar o friendly_id do pacote em `_fetch_package`**

Em `package_cancellation_service.py`, alterar `_fetch_package` para trazer também o `friendly_id`:

```python
def _fetch_package(sb: SupabaseRestClient, package_id: str) -> Optional[Dict[str, Any]]:
    rows = sb.select(
        "pacotes",
        columns="id,status,enquete_id,friendly_id",
        filters=[("id", "eq", package_id)],
        limit=1,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return None
```

- [ ] **Step 4: Alterar o loop de cancelamento para creditar os pagos**

Em `cancel_package`, substituir o ramo dos pagos. O bloco atual:

```python
    now = _now_iso()
    cancelled_sales = 0
    cancelled_payments = 0

    for v in sales:
        pag = v.get("pagamento") or {}
        pag_status = str(pag.get("status") or "").lower()
        venda_id = str(v.get("id") or "")
        pagamento_id = str(pag.get("id") or "")

        if pag_status == PAID_STATUS:
            # preservado: venda e pagamento continuam como estão
            continue

        if str(v.get("status") or "").lower() != "cancelled" and venda_id:
```

vira:

```python
    from app.services import credit_service

    now = _now_iso()
    cancelled_sales = 0
    cancelled_payments = 0
    credited_total = 0.0
    credited_clients = 0
    friendly = pkg.get("friendly_id") or package_id

    for v in sales:
        pag = v.get("pagamento") or {}
        pag_status = str(pag.get("status") or "").lower()
        venda_id = str(v.get("id") or "")
        pagamento_id = str(pag.get("id") or "")

        if pag_status == PAID_STATUS:
            # paga: gera crédito 100% e cancela venda+pagamento (produto não sai)
            cliente_id = str(v.get("cliente_id") or "")
            valor = float(v.get("total_amount") or 0)
            if cliente_id and valor > 0:
                credit_service.add_credit(
                    cliente_id, valor,
                    pacote_id=package_id, venda_id=venda_id,
                    descricao=f"Cancelamento pacote #{friendly}",
                    created_by=cancelled_by or "admin",
                )
                credited_total += valor
                credited_clients += 1
            if venda_id:
                sb._request(
                    "PATCH", f"/rest/v1/vendas?id=eq.{venda_id}",
                    payload={"status": "cancelled", "updated_at": now},
                    prefer="return=minimal",
                )
                cancelled_sales += 1
            if pagamento_id:
                sb._request(
                    "PATCH", f"/rest/v1/pagamentos?id=eq.{pagamento_id}",
                    payload={"status": "cancelled", "updated_at": now},
                    prefer="return=minimal",
                )
                cancelled_payments += 1
            continue

        if str(v.get("status") or "").lower() != "cancelled" and venda_id:
```

(o restante do loop — ramo não-pago — fica intacto).

- [ ] **Step 5: Incluir os novos campos no retorno**

No `return` final de `cancel_package`, adicionar `credited_total` e `credited_clients`:

```python
    return {
        "package_id": package_id,
        "cancelled_sales": cancelled_sales,
        "cancelled_payments": cancelled_payments,
        "preserved_paid": 0,
        "credited_total": round(credited_total, 2),
        "credited_clients": credited_clients,
        "paid_clients": paid,
    }
```

(`preserved_paid` agora é sempre 0 — pagos viram crédito, não são mais preservados. O docstring do módulo deve ser atualizado: trocar a seção "Status resultantes" para refletir que pagos viram `cancelled` + crédito.)

- [ ] **Step 6: Atualizar o docstring do módulo**

Substituir as linhas do docstring que descrevem a preservação dos pagos pela nova regra:

```
Status resultantes:
  pacote.status               -> 'cancelled' (+ cancelled_at/cancelled_by)
  vendas[nao_paga].status     -> 'cancelled'
  pagamentos[nao_paga].status -> 'cancelled'
  vendas[paga].status         -> 'cancelled'  (produto não sai)
  pagamentos[paga].status     -> 'cancelled'
  creditos                    -> 1 'credit' por venda paga (valor = total_amount)
```

- [ ] **Step 7: Adicionar `credit_total` ao `preview_cancel`**

Em `preview_cancel`, antes do `return`, somar o que será creditado e incluir no dict:

```python
    credit_total = round(sum(float(p.get("total_amount") or 0) for p in paid), 2)
    return {
        "package_id": package_id,
        "package_status": pkg.get("status"),
        "paid_count": len(paid),
        "paid_clients": paid,
        "pending_count": pending_count,
        "credit_total": credit_total,
    }
```

- [ ] **Step 8: Rodar os testes do módulo (todos)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_package_cancellation_service.py -v`
Expected: PASS, incluindo o novo `test_cancel_paid_generates_credit`.

Nota: o teste existente `test_cancel_forced_preserves_paid` (se houver) agora deve esperar que o pago seja cancelado+creditado em vez de preservado. Ajustar suas asserções para o novo comportamento (procurar por `preserved_paid` / "preserva" no arquivo e atualizar para `credited_total`).

- [ ] **Step 9: Commit**

```bash
git add app/services/package_cancellation_service.py tests/unit/test_package_cancellation_service.py
git commit -m "feat(creditos): cancelamento de pacote pago gera crédito 100% e cancela a venda"
```

---

## Task 4: Abater crédito no PIX combinado ("Pagar todos")

**Files:**
- Modify: `app/services/portal_service.py` (`create_combined_pix` + helper `_apply_credit`)
- Test: `tests/unit/test_portal_credit.py`

- [ ] **Step 1: Escrever os testes**

`tests/unit/test_portal_credit.py`:

```python
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


@pytest.fixture
def env(monkeypatch):
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    # cliente com CPF
    monkeypatch.setattr(ps, "_client", lambda: _FakeSb())
    monkeypatch.setattr(ps, "runtime_state_enabled", lambda: False, raising=False)
    return asaas


class _FakeSb:
    def select(self, table, columns=None, filters=None, limit=None):
        if table == "clientes":
            return [{"nome": "Ana", "celular": "5511999", "cpf_cnpj": "12345678900"}]
        return []
    def update(self, *a, **k):
        return []


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
    from app.services.runtime_state_service import save_runtime_state  # noqa

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
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_credit.py -v`
Expected: FAIL — `_apply_credit`/campos novos inexistentes.

- [ ] **Step 3: Adicionar helpers `_apply_credit` e `_mark_paid_with_credit`**

Em `portal_service.py`, antes de `create_combined_pix`:

```python
def _apply_credit(cliente_id: str, total: float):
    """Retorna (saldo_antes, credito_aplicado, cobranca) sem alterar o ledger."""
    from app.services import credit_service
    saldo = credit_service.get_balance(cliente_id)
    aplicado = round(min(saldo, total), 2)
    cobranca = round(total - aplicado, 2)
    return saldo, aplicado, cobranca


def _mark_paid_with_credit(pagamento_ids: List[str]) -> None:
    """Marca pagamentos como paid quando o crédito cobre 100% (sem PIX)."""
    client = _client()
    for pid in pagamento_ids:
        client.update(
            "pagamentos",
            {"status": "paid", "paid_at": _now().isoformat(), "updated_at": _now().isoformat()},
            filters=[("id", "eq", pid)],
        )
```

- [ ] **Step 4: Alterar `create_combined_pix`**

Após o cálculo de `total`, `pagamento_ids` e `item_count`, e ANTES de criar o customer/PIX no Asaas, inserir a lógica de crédito. Substituir o trecho que vai de `# Buscar dados do cliente` até o `return` final por:

```python
    from app.services import credit_service

    saldo_antes, credito_aplicado, cobranca = _apply_credit(cliente_id, total)

    # Crédito cobre 100% → quita sem PIX
    if cobranca <= 0:
        _mark_paid_with_credit(pagamento_ids)
        credit_service.add_confirmed_debit(
            cliente_id, credito_aplicado,
            descricao=f"Pago com crédito — {item_count} pedido{'s' if item_count > 1 else ''}",
        )
        return {
            "pix_payload": "", "payment_link": "", "qr_code_base64": "",
            "total": total, "item_count": item_count,
            "saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado,
            "cobranca": 0.0, "pago_com_credito": True, "asaas_id": None,
        }

    # Buscar dados do cliente
    client = _client()
    cliente_rows = client.select(
        "clientes", columns="nome,celular,cpf_cnpj",
        filters=[("id", "eq", cliente_id)], limit=1,
    )
    cliente_info = cliente_rows[0] if isinstance(cliente_rows, list) and cliente_rows else {}
    cpf = (cliente_info.get("cpf_cnpj") or "").strip()
    if not cpf:
        raise CpfMissingError("cliente sem CPF cadastrado")

    from integrations.asaas.client import AsaasClient
    from datetime import date
    asaas = AsaasClient()
    customer = asaas.create_customer(
        name=cliente_info.get("nome") or "Cliente",
        phone=cliente_info.get("celular") or "", cpf_cnpj=cpf,
    )
    due = date.today().isoformat()
    description = f"Pagamento de {item_count} pedido{'s' if item_count > 1 else ''} - Raylook Assessoria"
    payment = asaas.create_payment_pix(customer["id"], cobranca, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])
    asaas_id = payment["id"]

    # Débito pendente — confirmado só quando o polling confirmar o pagamento
    if credito_aplicado > 0:
        credit_service.add_pending_debit(
            cliente_id, credito_aplicado, asaas_payment_id=asaas_id,
            descricao=f"Crédito aplicado em {item_count} pedido{'s' if item_count > 1 else ''}",
        )

    from app.services.runtime_state_service import save_runtime_state, runtime_state_enabled
    if runtime_state_enabled():
        save_runtime_state(
            f"{COMBINED_PIX_STATE_PREFIX}{asaas_id}",
            {"pagamento_ids": pagamento_ids, "cliente_id": cliente_id,
             "total": cobranca, "created_at": _now().isoformat()},
        )

    pix_payload = pix_data.get("pix_payload") or ""
    payment_link = pix_data.get("paymentLink") or payment.get("invoiceUrl") or ""
    return {
        "pix_payload": pix_payload, "payment_link": payment_link,
        "qr_code_base64": _generate_qr_base64(pix_payload) if pix_payload else "",
        "total": total, "item_count": item_count,
        "saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado,
        "cobranca": cobranca, "pago_com_credito": False, "asaas_id": asaas_id,
    }
```

- [ ] **Step 5: Rodar os testes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_credit.py -v`
Expected: PASS (2 testes desta task).

- [ ] **Step 6: Commit**

```bash
git add app/services/portal_service.py tests/unit/test_portal_credit.py
git commit -m "feat(creditos): abate crédito no PIX combinado (parcial gera débito pending; cobertura total quita sem PIX)"
```

---

## Task 5: Abater crédito no PIX individual

**Files:**
- Modify: `app/services/portal_service.py` (`get_or_create_pix`)
- Test: `tests/unit/test_portal_credit.py`

- [ ] **Step 1: Escrever os testes**

Adicionar em `tests/unit/test_portal_credit.py`:

```python
class _FakeSbIndiv:
    def __init__(self, pix_already=False, paid_status="sent"):
        self.pix_already = pix_already
        self.paid_status = paid_status
        self.updates = []
    def select(self, table, columns=None, filters=None, limit=None):
        if table == "pagamentos":
            row = {"id": "P1", "venda_id": "V1", "status": self.paid_status,
                   "provider_payment_id": None, "payment_link": None, "pix_payload": None}
            if self.pix_already:
                row.update({"pix_payload": "OLD", "payment_link": "http://old"})
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


def test_individual_partial_credit(monkeypatch):
    fake = _FakeSbIndiv()
    monkeypatch.setattr(ps, "_client", lambda: fake)
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    monkeypatch.setattr("app.services.credit_service.get_balance", lambda cid: 50.0)
    pend = []
    monkeypatch.setattr("app.services.credit_service.add_pending_debit",
                        lambda cid, valor, **kw: pend.append((valor, kw)))

    out = ps.get_or_create_pix("P1", "C1")

    assert out["credito_aplicado"] == 50.0
    assert out["cobranca"] == 150.0
    assert asaas.created == [150.0]
    assert pend[0][0] == 50.0 and pend[0][1].get("pagamento_id") == "P1"


def test_individual_full_coverage(monkeypatch):
    fake = _FakeSbIndiv()
    monkeypatch.setattr(ps, "_client", lambda: fake)
    asaas = FakeAsaas()
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    monkeypatch.setattr("app.services.credit_service.get_balance", lambda cid: 500.0)
    conf = []
    monkeypatch.setattr("app.services.credit_service.add_confirmed_debit",
                        lambda cid, valor, **kw: conf.append(valor))

    out = ps.get_or_create_pix("P1", "C1")

    assert out["pago_com_credito"] is True
    assert out["cobranca"] == 0.0
    assert asaas.created == []
    assert conf == [200.0]
    assert any(t == "pagamentos" and p.get("status") == "paid" for t, p in fake.updates)
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_credit.py -k individual -v`
Expected: FAIL.

- [ ] **Step 3: Alterar `get_or_create_pix`**

No início do corpo, após validar ownership (`if str(venda.get("cliente_id")) != str(cliente_id): raise PermissionError(...)`) e ANTES do bloco `# Se já tem pix_payload...`, inserir o cálculo de crédito e o caminho de cobertura total:

```python
    from app.services import credit_service
    total = float(venda.get("total_amount") or 0)
    saldo_antes, credito_aplicado, cobranca = _apply_credit(cliente_id, total)

    # Crédito cobre 100% → quita sem PIX (só se ainda não pago)
    if cobranca <= 0 and pagamento.get("status") != "paid":
        client.update(
            "pagamentos",
            {"status": "paid", "paid_at": _now().isoformat(), "updated_at": _now().isoformat()},
            filters=[("id", "eq", pagamento["id"])],
        )
        credit_service.add_confirmed_debit(
            cliente_id, credito_aplicado, pagamento_id=pagamento["id"],
            descricao="Pago com crédito",
        )
        return {
            "pix_payload": "", "payment_link": "", "qr_code_base64": "",
            "status": "paid", "saldo_antes": saldo_antes,
            "credito_aplicado": credito_aplicado, "cobranca": 0.0,
            "pago_com_credito": True,
        }
```

Em seguida, no bloco que cria o pagamento no Asaas (atual `amount = float(venda.get("total_amount") or 0)`), trocar para cobrar a diferença e registrar o débito pendente:

```python
    from datetime import date
    due = date.today().isoformat()
    amount = cobranca  # cobra só a diferença após o crédito
    produto = venda.get("produto") or {}
    description = f"{produto.get('nome', 'Produto')} - {venda.get('qty', 1)} peça(s)"

    payment = asaas.create_payment_pix(customer["id"], amount, due, description)
    pix_data = asaas.get_payment_pix_with_retry(payment["id"])

    if credito_aplicado > 0:
        credit_service.add_pending_debit(
            cliente_id, credito_aplicado, pagamento_id=pagamento["id"],
            descricao="Crédito aplicado",
        )
```

E nos `return _build_pix_response(...)` desta função, enriquecer a resposta com os campos de crédito. Trocar `_build_pix_response` para aceitar extras: alterar a assinatura para

```python
def _build_pix_response(pagamento: Dict, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = pagamento.get("pix_payload") or ""
    qr_b64 = _generate_qr_base64(payload) if payload else ""
    out = {
        "pix_payload": payload,
        "payment_link": pagamento.get("payment_link") or "",
        "qr_code_base64": qr_b64,
        "status": pagamento.get("status") or "pending",
    }
    if extra:
        out.update(extra)
    return out
```

e nas chamadas de `get_or_create_pix` passar `extra={"saldo_antes": saldo_antes, "credito_aplicado": credito_aplicado, "cobranca": cobranca, "pago_com_credito": False}`.

**Re-entrância:** no bloco `if pagamento.get("pix_payload") and pagamento.get("payment_link"):` (PIX já gerado), buscar o débito pendente já existente para não reaplicar saldo:

```python
        existing = credit_service._existing_debit(client, pagamento["id"], None)
        credito_ja = 0.0
        if existing:
            row = client.select("creditos", columns="valor", filters=[("id", "eq", existing["id"])], limit=1)
            if isinstance(row, list) and row:
                credito_ja = float(row[0].get("valor") or 0)
        return _build_pix_response(pagamento, extra={
            "saldo_antes": saldo_antes, "credito_aplicado": credito_ja,
            "cobranca": round(total - credito_ja, 2), "pago_com_credito": False,
        })
```

- [ ] **Step 4: Rodar os testes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_credit.py -v`
Expected: PASS (todos da Task 4 e 5).

- [ ] **Step 5: Commit**

```bash
git add app/services/portal_service.py tests/unit/test_portal_credit.py
git commit -m "feat(creditos): abate crédito no PIX individual + re-entrância"
```

---

## Task 6: Confirmar débito quando o pagamento é confirmado (polling Asaas)

**Files:**
- Modify: `app/services/asaas_sync_service.py` (Caminho 1 individual + Caminho 2 combinado)
- Test: `tests/unit/test_asaas_sync_service.py`

- [ ] **Step 1: Escrever o teste**

Adicionar em `tests/unit/test_asaas_sync_service.py` (seguir o estilo de mock já usado no arquivo; o ponto-chave é assertar que `confirm_debit` é chamado com a chave certa):

```python
import asyncio
from unittest.mock import MagicMock


def test_individual_paid_confirms_debit(monkeypatch):
    from app.services import asaas_sync_service as ass

    monkeypatch.setattr(ass, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.config.settings.RAYLOOK_SANDBOX", False, raising=False)

    sb = MagicMock()
    sb.select_all.return_value = [{"id": "P1", "provider_payment_id": "pay_1", "status": "sent"}]
    monkeypatch.setattr(ass, "SupabaseRestClient",
                        MagicMock(from_settings=MagicMock(return_value=sb)))

    asaas = MagicMock()
    asaas.get_payment_status.return_value = "RECEIVED"
    asaas.get_payment.return_value = {"status": "RECEIVED", "paymentDate": "2026-05-10"}
    monkeypatch.setattr("integrations.asaas.client.AsaasClient", lambda: asaas)
    # combinados: nenhum
    async def _no_combined(sb_, asaas_):
        return 0
    monkeypatch.setattr(ass, "_sync_combined_pix", _no_combined)

    confirmed = []
    monkeypatch.setattr("app.services.credit_service.confirm_debit",
                        lambda **kw: confirmed.append(kw))

    asyncio.run(ass.sync_asaas_payments())

    assert {"pagamento_id": "P1"} in confirmed
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_asaas_sync_service.py::test_individual_paid_confirms_debit -v`
Expected: FAIL — `confirm_debit` não chamado.

- [ ] **Step 3: Confirmar débito no Caminho 1 (individual)**

Em `asaas_sync_service.py`, dentro do loop `for p in pending:`, logo após o `sb.update("pagamentos", {"status": "paid", ...})` e `updated_count += 1`, adicionar:

```python
                    from app.services import credit_service
                    credit_service.confirm_debit(pagamento_id=pag_id)
```

- [ ] **Step 4: Confirmar débito no Caminho 2 (combinado)**

Em `_sync_combined_pix`, após o loop que marca os pagamentos individuais como paid (quando `status in ASAAS_PAID_STATUSES`), adicionar uma chamada por combinado confirmado:

```python
        from app.services import credit_service
        credit_service.confirm_debit(asaas_payment_id=asaas_id)
```

(colocar logo após o `for pag_id in pag_ids:` interno, ainda dentro do `for state in states:`, no ramo em que `status in ASAAS_PAID_STATUSES`).

- [ ] **Step 5: Rodar o teste**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_asaas_sync_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/asaas_sync_service.py tests/unit/test_asaas_sync_service.py
git commit -m "feat(creditos): confirma débito do crédito ao confirmar pagamento no polling Asaas"
```

---

## Task 7: Endpoint `GET /api/finance/credits`

**Files:**
- Modify: `app/routers/finance.py`
- Test: `tests/unit/test_finance_router.py`

- [ ] **Step 1: Escrever o teste**

Adicionar em `tests/unit/test_finance_router.py` (seguir o estilo do arquivo; usa TestClient):

```python
def test_get_credits(monkeypatch, client):  # 'client' = fixture TestClient existente no arquivo
    from app.services import credit_service
    monkeypatch.setattr(
        credit_service, "list_balances",
        lambda: [{"cliente_id": "C1", "nome": "Ana", "celular": "5511999", "saldo": 100.0}],
    )
    monkeypatch.setattr(
        credit_service, "get_ledger",
        lambda cid: [{"id": "L1", "tipo": "credit", "status": "confirmed",
                      "valor": 100.0, "descricao": "Cancelamento pacote #A-1",
                      "created_at": "2026-05-01T00:00:00Z"}],
    )
    resp = client.get("/api/finance/credits?cliente_id=C1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["balances"][0]["saldo"] == 100.0
    assert body["ledger"][0]["tipo"] == "credit"
```

(Se o arquivo não tiver fixture `client`, criar uma local: `from fastapi.testclient import TestClient; from main import app; client = TestClient(app)` — checar o topo do arquivo e reusar o padrão existente.)

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_router.py::test_get_credits -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implementar o endpoint**

Em `app/routers/finance.py`, adicionar o import e a rota:

```python
from app.services import credit_service


@router.get("/credits")
async def get_credits(cliente_id: Optional[str] = None) -> Dict[str, Any]:
    """Saldos por cliente (lista) + extrato de um cliente (se cliente_id)."""
    try:
        balances = credit_service.list_balances()
        ledger = credit_service.get_ledger(cliente_id) if cliente_id else []
        return {"balances": balances, "ledger": ledger}
    except Exception as e:
        logger.exception("Erro ao carregar créditos")
        return JSONResponse(status_code=500, content={"error": str(e)})
```

- [ ] **Step 4: Rodar o teste**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_router.py::test_get_credits -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/finance.py tests/unit/test_finance_router.py
git commit -m "feat(creditos): GET /api/finance/credits (saldos + extrato)"
```

---

## Task 8: Saldo de crédito no portal (backend + template)

**Files:**
- Modify: `app/routers/portal.py` (rotas `portal_pedidos` e a versão read-only)
- Modify: `templates/portal_pedidos.html` (KPI de saldo)
- Test: `tests/unit/test_portal_router.py`

- [ ] **Step 1: Escrever o teste**

Adicionar em `tests/unit/test_portal_router.py` (seguir o estilo de mock de sessão já usado no arquivo). O essencial: a rota injeta `credit_balance` no contexto.

```python
def test_portal_pedidos_includes_credit_balance(monkeypatch, client):
    # reusar helpers de auth/sessão já existentes no arquivo para logar como C1
    from app.services import portal_service, credit_service
    monkeypatch.setattr(portal_service, "get_client_orders", lambda cid: [])
    monkeypatch.setattr(portal_service, "get_client_kpis", lambda o: {
        "total_pending": 0, "total_paid": 0, "pending_count": 0, "paid_count": 0})
    monkeypatch.setattr(credit_service, "get_balance", lambda cid: 75.5)
    # ... (autenticar conforme o padrão do arquivo) ...
    resp = client.get("/portal/pedidos", cookies=auth_cookies)
    assert resp.status_code == 200
    assert "75,50" in resp.text  # KPI renderizado
```

(Adaptar autenticação ao padrão do arquivo; se for complexo, validar via asserção do contexto mockando `TemplateResponse`.)

- [ ] **Step 2: Rodar (deve falhar)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_router.py -k credit_balance -v`
Expected: FAIL.

- [ ] **Step 3: Injetar `credit_balance` nas rotas do portal**

Em `app/routers/portal.py`, nas rotas `portal_pedidos` (~linha 314) e na read-only (~linha 351), após `kpis = ps.get_client_kpis(orders)`:

```python
    from app.services import credit_service
    credit_balance = credit_service.get_balance(client["id"])
```

e adicionar `"credit_balance": credit_balance,` ao dict do `TemplateResponse` de ambas.

- [ ] **Step 4: Adicionar o KPI no template**

Em `templates/portal_pedidos.html`, no bloco de KPIs (após o card "Pago", ~linha 48), adicionar:

```html
            {% if credit_balance and credit_balance > 0 %}
            <div class="kpi-card credit">
                <div class="kpi-title"><i class="fas fa-wallet"></i> Crédito disponível</div>
                <div class="kpi-value">R$ {{ "%.2f" | format(credit_balance) | replace('.', ',') }}</div>
                <div class="kpi-sub">abatido automaticamente na próxima compra</div>
            </div>
            {% endif %}
```

- [ ] **Step 5: Rodar o teste**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_portal_router.py -k credit_balance -v`
Expected: PASS.

- [ ] **Step 6: Verificação no browser (UI)**

Subir local e logar como um cliente com crédito (criar via SQLite). Conferir que o card "Crédito disponível" aparece com o valor correto e some quando saldo = 0.

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 main.py
```
Abrir `http://127.0.0.1:8000/portal` (Playwright MCP) e validar visualmente.

- [ ] **Step 7: Commit**

```bash
git add app/routers/portal.py templates/portal_pedidos.html tests/unit/test_portal_router.py
git commit -m "feat(creditos): saldo de crédito no portal do cliente"
```

---

## Task 9: Crédito aplicado visível na tela do PIX (portal)

**Files:**
- Modify: `templates/portal_pedidos.html` (JS do modal de PIX que consome `/portal/pix` e `create_combined_pix`)

- [ ] **Step 1: Localizar o handler de PIX no template**

Run:
```bash
grep -n "pix\|qr_code_base64\|credito\|pago_com_credito\|combined\|Pagar tudo\|fetch" templates/portal_pedidos.html | head -40
```
Identificar onde a resposta do PIX (individual e "pagar todos") é renderizada no modal.

- [ ] **Step 2: Exibir crédito aplicado e total a pagar**

No render do modal de PIX, usar os novos campos da resposta (`saldo_antes`, `credito_aplicado`, `cobranca`, `pago_com_credito`). Quando `credito_aplicado > 0`, mostrar:

```html
<div class="pix-credito">
  <span>Crédito aplicado</span>
  <strong>− R$ {{credito_formatado}}</strong>
</div>
<div class="pix-total">
  <span>Total a pagar</span>
  <strong>R$ {{cobranca_formatado}}</strong>
</div>
```

Quando `pago_com_credito === true`: esconder QR/copia-e-cola e mostrar mensagem de sucesso "Pago integralmente com crédito ✓" + atualizar a lista de pedidos para `paid`. (Implementar no JS inline do template, seguindo o padrão dos outros fetches do arquivo. Formatar moeda como os KPIs: `toFixed(2).replace('.', ',')`.)

- [ ] **Step 3: Verificação no browser (UI)**

Com um cliente com crédito parcial: gerar PIX e confirmar que aparece "Crédito aplicado − R$ X" e "Total a pagar R$ Y" com o QR do valor reduzido. Com crédito que cobre tudo: confirmar "Pago integralmente com crédito" sem QR.

- [ ] **Step 4: Commit**

```bash
git add templates/portal_pedidos.html
git commit -m "feat(creditos): tela do PIX mostra crédito aplicado e total a pagar"
```

---

## Task 10: Aba "Créditos" no financeiro (dashboard admin)

**Files:**
- Modify: `templates/dashboard_v2.html` (rail step + seção da view)
- Modify: `static/js/dashboard_v2.js` (fetch + render)

- [ ] **Step 1: Adicionar o rail step "Créditos"**

Em `templates/dashboard_v2.html`, junto aos `data-fin-view` existentes (`receivable` ~linha 995, `paid` ~linha 1003), adicionar:

```html
                <div class="rail-step" data-fin-view="credits">
                    <span class="rail-ico"><i class="fas fa-wallet"></i></span>
                    <span class="rail-label">Créditos</span>
                </div>
```

- [ ] **Step 2: Adicionar a seção/tabela da view de créditos**

Após a seção da view "paid" (`finance-paid-table`, ~linha 1177), adicionar uma `fin-table-wrap` análoga para créditos:

```html
            <div class="fin-table-wrap" id="fin-view-credits" style="display:none;">
                <table class="fin-table" id="credits-table">
                    <thead>
                        <tr><th>Cliente</th><th>Celular</th><th>Saldo</th><th></th></tr>
                    </thead>
                    <tbody id="credits-table-body"></tbody>
                </table>
                <div id="credits-ledger" class="fin-ledger"></div>
            </div>
```

(Conferir como as outras views são mostradas/escondidas ao clicar no rail step e replicar o mesmo mecanismo de toggle para `credits`.)

- [ ] **Step 3: Render no `dashboard_v2.js`**

Localizar a função que troca de view ao clicar em `data-fin-view` e o fetch das outras abas:

```bash
grep -n "data-fin-view\|fin-view\|/api/finance/\|receivable\|renderPaid\|switchFin" static/js/dashboard_v2.js | head
```

Adicionar uma função `loadCredits()` que faz `fetch('/api/finance/credits')`, popula `#credits-table-body` (uma linha por cliente: nome, celular, `R$ saldo`, botão "Ver extrato") e, ao clicar em "Ver extrato", faz `fetch('/api/finance/credits?cliente_id=<id>')` e renderiza o extrato em `#credits-ledger` (data, tipo `credit`/`debit`, valor com sinal, descrição). Chamar `loadCredits()` quando a view `credits` é ativada. Seguir o estilo de formatação de moeda/data já usado no arquivo.

- [ ] **Step 4: Verificação no browser (UI)**

Subir local, ir ao financeiro → aba "Créditos". Confirmar:
- lista de clientes com saldo > 0;
- clicar "Ver extrato" mostra os lançamentos (crédito do cancelamento e débitos de uso);
- valores e sinais corretos.

Cenário de ponta-a-ponta local (SQLite): cancelar um pacote com pagamento `paid` pelo dashboard → aba Créditos mostra o cliente com saldo = valor pago.

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard_v2.html static/js/dashboard_v2.js
git commit -m "feat(creditos): aba Créditos no financeiro (saldos + extrato)"
```

---

## Task 11: Migration de produção

**Files:**
- Create: `deploy/migrations/2026-05-29-creditos.sql`

- [ ] **Step 1: Escrever a migration idempotente**

`deploy/migrations/2026-05-29-creditos.sql`:

```sql
-- Cria a tabela de créditos (ledger). Idempotente e transacional.
BEGIN;

CREATE TABLE IF NOT EXISTS creditos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    cliente_id text NOT NULL REFERENCES clientes(id),
    tipo text NOT NULL CHECK (tipo IN ('credit', 'debit')),
    status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed')),
    valor numeric NOT NULL CHECK (valor > 0),
    pacote_id text REFERENCES pacotes(id),
    venda_id text REFERENCES vendas(id),
    pagamento_id text REFERENCES pagamentos(id),
    asaas_payment_id text,
    descricao text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON creditos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_creditos_pagamento ON creditos(pagamento_id);
CREATE INDEX IF NOT EXISTS idx_creditos_asaas ON creditos(asaas_payment_id);

COMMIT;

-- Verificação pós-migration:
--   SELECT to_regclass('public.creditos');   -> deve retornar 'creditos'
```

- [ ] **Step 2: Testar a migration no Postgres dedicado do raylook (staging do próprio banco)**

Aplicar em um schema/instância de teste do Postgres raylook (NÃO em prod). Rodar duas vezes para confirmar idempotência (segunda execução não erra).
Expected: `to_regclass('public.creditos')` retorna `creditos` nas duas vezes.

- [ ] **Step 3: Commit**

```bash
git add deploy/migrations/2026-05-29-creditos.sql
git commit -m "chore(creditos): migration idempotente da tabela creditos"
```

> **Deploy:** a tabela já está no `schema.sql` canônico (Task 1). Como o banco é dedicado (`raylook_*`), aplicar a migration no Postgres de prod no momento do deploy (via CI/SSH) **antes** da nova imagem subir. Confirmar com o usuário antes de rodar em prod.

---

## Self-Review (preenchido pelo autor do plano)

**Cobertura do spec:**
- Tabela `creditos` ledger (status pending/confirmed, FKs) → Task 1 ✓
- `credit_service` (get_balance/get_ledger/list_balances/add_credit/add_pending_debit/confirm_debit/add_confirmed_debit) → Task 2 ✓
- Cancelamento credita 100% + cancela venda/pagamento; `preview_cancel.credit_total` → Task 3 ✓
- Abate no PIX combinado (parcial pending; cobertura total quita sem PIX) → Task 4 ✓
- Abate no PIX individual + re-entrância → Task 5 ✓
- Débito confirmado no polling (individual + combinado) → Task 6 ✓
- Aba Créditos no financeiro (endpoint + UI) → Tasks 7, 10 ✓
- Saldo no portal + crédito aplicado na tela do PIX → Tasks 8, 9 ✓
- Migration Postgres idempotente → Task 11 ✓
- Sem estorno / sem ajuste manual / sem expiração → não implementado (fora de escopo) ✓

**Consistência de tipos/assinaturas:** `add_pending_debit`/`add_confirmed_debit`/`confirm_debit` usam as mesmas chaves (`pagamento_id`, `asaas_payment_id`) em service, portal e sync. `_apply_credit` retorna `(saldo_antes, credito_aplicado, cobranca)` consistentemente. Respostas de PIX expõem `saldo_antes`/`credito_aplicado`/`cobranca`/`pago_com_credito` em ambos os fluxos.

**Placeholders:** tarefas de UI (9, 10) dependem de inspeção do JS/template existente — os steps incluem o `grep` exato para localizar os pontos e o markup/lógica concretos a inserir, com verificação no browser (norma do projeto para UI).
