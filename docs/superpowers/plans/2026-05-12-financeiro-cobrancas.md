# Aba Financeiro — Gestão de Contas a Receber — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reformular a aba Financeiro existente pra focar em gestão de contas a receber: KPIs com aging, agregação por cliente, write-off por cobrança, modal de histórico. Modo "Por cobrança" preservado via toggle.

**Architecture:** 4 endpoints novos em `app/routers/finance.py` + 4 funções novas em `app/services/finance_service.py` consultando `pagamentos`/`vendas`/`clientes`. Frontend extrai lógica financeira de `dashboard.js` pra novo `static/js/finance.js`. Persistência: 1 migration Postgres adicionando status `written_off` + 2 colunas em `pagamentos`.

**Tech Stack:** FastAPI · Python 3.11 · Postgres (prod) / SQLite (sandbox) · JS vanilla · Jinja2 · pytest + FakeSupabaseClient

**Spec:** `docs/superpowers/specs/2026-05-12-financeiro-cobrancas-design.md`

---

## File Structure

| Path | Status | Responsabilidade |
|---|---|---|
| `deploy/postgres/migrations/F062_pagamento_written_off_status.sql` | NEW | Adiciona status `written_off` ao CHECK constraint + colunas `written_off_at` / `written_off_reason` |
| `deploy/sqlite/schema.sql` | MOD | Paridade do schema pra dev local |
| `app/services/finance_service.py` | MOD | `build_receivables_by_client`, `build_aging_summary`, `build_payment_history`, `mark_payment_written_off` |
| `app/routers/finance.py` | MOD | `GET /receivables`, `GET /aging-summary`, `POST /pagamentos/{id}/write-off`, `GET /pagamentos/{id}/history` |
| `tests/unit/test_finance_receivables.py` | NEW | Agregação por cliente, filtros de status, ordenação |
| `tests/unit/test_finance_aging.py` | NEW | Buckets, idade média ponderada, `paid_rate_30d` |
| `tests/unit/test_finance_history.py` | NEW | Timeline derivada |
| `tests/unit/test_finance_writeoff.py` | NEW | Service e endpoint write-off |
| `templates/index.html` | MOD | Bloco `section-finance` (linhas 211-292) |
| `static/js/finance.js` | NEW | Toda lógica da aba Financeiro (extraída de `dashboard.js` + features novas) |
| `static/js/dashboard.js` | MOD | Remover funções financeiras que foram pra `finance.js` |
| `static/css/dashboard.css` | MOD | Classes: `aging-bar`, `aging-bucket-*`, `aging-badge`, `receivables-expand` |

---

## Fase 1: Migration + schema

### Task 1: Criar migration Postgres

**Files:**
- Create: `deploy/postgres/migrations/F062_pagamento_written_off_status.sql`

- [ ] **Step 1: Escrever a migration**

```sql
-- F-062: status 'written_off' em pagamentos.
-- Permite write-off manual de cobranças que o cliente abandonou
-- sem confundir com 'cancelled' (que é cancelamento ativo).

BEGIN;

ALTER TABLE pagamentos DROP CONSTRAINT IF EXISTS pagamentos_status_check;
ALTER TABLE pagamentos ADD CONSTRAINT pagamentos_status_check
  CHECK (status IN ('created','sent','paid','failed','cancelled','written_off'));

ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_at TIMESTAMPTZ;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_reason TEXT;

CREATE INDEX IF NOT EXISTS pagamentos_written_off_at_idx
  ON pagamentos (written_off_at)
  WHERE written_off_at IS NOT NULL;

COMMIT;
```

- [ ] **Step 2: Commit**

```bash
git add deploy/postgres/migrations/F062_pagamento_written_off_status.sql
git commit -m "db: F-062 adiciona status written_off em pagamentos"
```

### Task 2: Espelhar schema no SQLite

**Files:**
- Modify: `deploy/sqlite/schema.sql:255-271`

- [ ] **Step 1: Editar bloco `CREATE TABLE pagamentos`**

Trocar o CHECK constraint e adicionar duas colunas. O bloco final deve ficar:

```sql
CREATE TABLE IF NOT EXISTS pagamentos (
    id TEXT PRIMARY KEY,
    venda_id TEXT NOT NULL REFERENCES vendas(id),
    provider TEXT NOT NULL DEFAULT 'asaas'
        CHECK (provider IN ('asaas', 'mercadopago')),
    provider_customer_id TEXT,
    provider_payment_id TEXT,
    payment_link TEXT,
    pix_payload TEXT,
    due_date TEXT,
    paid_at TEXT,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'sent', 'paid', 'failed', 'cancelled', 'written_off')),
    written_off_at TEXT,
    written_off_reason TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 2: Apagar `data/raylook.db` se existir (forçar regeneração no próximo boot)**

```bash
rm -f data/raylook.db
```

- [ ] **Step 3: Commit**

```bash
git add deploy/sqlite/schema.sql
git commit -m "db: espelhar status written_off no schema SQLite local"
```

---

## Fase 2: Backend — service functions + unit tests

Padrão TDD: teste → falha → impl → passa → commit.

### Task 3: `build_receivables_by_client` — agregação por cliente

**Files:**
- Test: `tests/unit/test_finance_receivables.py`
- Modify: `app/services/finance_service.py` (adicionar no final do arquivo)

- [ ] **Step 1: Escrever testes**

```python
"""Testes de build_receivables_by_client."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_setup(monkeypatch):
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    fake.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana Silva", "celular": "5511999990001"},
        {"id": "c2", "nome": "Bia Costa", "celular": "5511999990002"},
    ])
    fake.tables["vendas"].extend([
        {"id": "v1", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 400.0},
        {"id": "v2", "cliente_id": "c1", "pacote_id": "p2", "total_amount": 800.0},
        {"id": "v3", "cliente_id": "c2", "pacote_id": "p1", "total_amount": 600.0},
    ])
    fake.tables["pacotes"].extend([
        {"id": "p1", "enquete_id": "e1", "sequence_no": 1},
        {"id": "p2", "enquete_id": "e2", "sequence_no": 2},
    ])
    fake.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Enquete 1"},
        {"id": "e2", "titulo": "Enquete 2"},
    ])
    return fake


def test_groups_pagamentos_by_cliente(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "created",
         "created_at": "2026-04-20T10:00:00+00:00"},
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-05-05T10:00:00+00:00"},
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")

    assert len(rows) == 2
    ana = next(r for r in rows if r["cliente_id"] == "c1")
    assert ana["nome"] == "Ana Silva"
    assert ana["total"] == 1200.0
    assert ana["count"] == 2
    assert ana["oldest_age_days"] == 22  # 2026-05-12 - 2026-04-20
    assert len(ana["charges"]) == 2


def test_excludes_paid_and_written_off(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "paid",
         "created_at": "2026-04-20T10:00:00+00:00"},
        {"id": "pg3", "venda_id": "v3", "status": "written_off",
         "created_at": "2026-03-01T10:00:00+00:00"},
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")

    assert len(rows) == 1
    assert rows[0]["cliente_id"] == "c1"
    assert rows[0]["count"] == 1


def test_sorted_by_oldest_age_desc(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-10T10:00:00+00:00"},  # 2d
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-04-01T10:00:00+00:00"},  # 41d
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")
    assert [r["cliente_id"] for r in rows] == ["c2", "c1"]


def test_bucket_classification(fake_setup):
    fake = fake_setup
    fake.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent",
         "created_at": "2026-05-10T00:00:00+00:00"},  # 2d → 0-7
        {"id": "pg3", "venda_id": "v3", "status": "sent",
         "created_at": "2026-03-25T00:00:00+00:00"},  # 48d → 30+
    ])
    from app.services.finance_service import build_receivables_by_client
    rows = build_receivables_by_client(now_iso="2026-05-12T00:00:00+00:00")
    by_id = {r["cliente_id"]: r for r in rows}
    assert by_id["c1"]["bucket"] == "0-7"
    assert by_id["c2"]["bucket"] == "30+"
```

- [ ] **Step 2: Rodar testes — devem falhar com ImportError**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_receivables.py -v
```

Expected: `ImportError: cannot import name 'build_receivables_by_client'`

- [ ] **Step 3: Implementar a função**

Adicionar no final de `app/services/finance_service.py`:

```python
# ---------------------------------------------------------------------------
# F-062: Gestão de contas a receber
# ---------------------------------------------------------------------------

PENDING_RECEIVABLE_STATUSES = ("created", "sent")
AGING_BUCKETS = (
    ("0-7", 0, 7),
    ("8-15", 8, 15),
    ("16-30", 16, 30),
    ("30+", 31, 10_000),
)


def _classify_bucket(age_days: int) -> str:
    for label, lo, hi in AGING_BUCKETS:
        if lo <= age_days <= hi:
            return label
    return "30+"


def _now_dt(now_iso: str | None) -> datetime:
    if now_iso:
        return datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return datetime.now(tz=ZoneInfo("UTC"))


def build_receivables_by_client(now_iso: str | None = None) -> List[Dict[str, Any]]:
    """Agrega pagamentos pendentes (created/sent) por cliente.

    Retorna lista ordenada por idade do débito mais antigo desc.
    Cada item: {cliente_id, nome, celular_last4, total, count, oldest_age_days,
                bucket, charges:[{pagamento_id, pacote_id, enquete_titulo, valor,
                                  age_days, status}]}.
    """
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    pagamentos = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("status", "in", list(PENDING_RECEIVABLE_STATUSES))],
        order="created_at.asc",
    )
    if not pagamentos:
        return []

    venda_ids = list({str(p["venda_id"]) for p in pagamentos if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,pacote_id,total_amount",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}

    cliente_ids = list({str(v["cliente_id"]) for v in vendas if v.get("cliente_id")})
    clientes = _select_in_batches(
        client, "clientes",
        columns="id,nome,celular",
        filter_field="id", values=cliente_ids,
    )
    cliente_by_id = {str(c["id"]): c for c in clientes}

    pacote_ids = list({str(v["pacote_id"]) for v in vendas if v.get("pacote_id")})
    pacotes = _select_in_batches(
        client, "pacotes",
        columns="id,enquete_id,sequence_no",
        filter_field="id", values=pacote_ids,
    )
    pacote_by_id = {str(p["id"]): p for p in pacotes}

    enquete_ids = list({str(p["enquete_id"]) for p in pacotes if p.get("enquete_id")})
    enquetes = _select_in_batches(
        client, "enquetes",
        columns="id,titulo",
        filter_field="id", values=enquete_ids,
    )
    enquete_by_id = {str(e["id"]): e for e in enquetes}

    now = _now_dt(now_iso)
    by_cliente: Dict[str, Dict[str, Any]] = {}

    for pag in pagamentos:
        venda = venda_by_id.get(str(pag.get("venda_id")))
        if not venda:
            continue
        cliente_id = str(venda.get("cliente_id") or "")
        cliente = cliente_by_id.get(cliente_id)
        if not cliente:
            continue
        pacote = pacote_by_id.get(str(venda.get("pacote_id") or ""))
        enquete_titulo = ""
        if pacote:
            enq = enquete_by_id.get(str(pacote.get("enquete_id") or ""))
            if enq:
                enquete_titulo = enq.get("titulo") or ""

        created_at = _parse_dt(pag.get("created_at"))
        age_days = (now - created_at).days if created_at else 0
        valor = float(venda.get("total_amount") or 0)

        bucket = by_cliente.setdefault(cliente_id, {
            "cliente_id": cliente_id,
            "nome": cliente.get("nome") or "",
            "celular_last4": str(cliente.get("celular") or "")[-4:],
            "total": 0.0,
            "count": 0,
            "oldest_age_days": 0,
            "bucket": "0-7",
            "charges": [],
        })
        bucket["total"] += valor
        bucket["count"] += 1
        if age_days > bucket["oldest_age_days"]:
            bucket["oldest_age_days"] = age_days
            bucket["bucket"] = _classify_bucket(age_days)
        bucket["charges"].append({
            "pagamento_id": str(pag["id"]),
            "pacote_id": str(venda.get("pacote_id") or ""),
            "enquete_titulo": enquete_titulo,
            "valor": valor,
            "age_days": age_days,
            "status": pag.get("status"),
        })

    rows = list(by_cliente.values())
    rows.sort(key=lambda r: r["oldest_age_days"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
    return rows
```

`_select_in_batches` já existe no arquivo. `_parse_dt` também. `ZoneInfo` já está importado.

- [ ] **Step 4: Rodar testes — devem passar**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_receivables.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance_service.py tests/unit/test_finance_receivables.py
git commit -m "feat(finance): build_receivables_by_client agrega pendentes por cliente"
```

### Task 4: `build_aging_summary` — KPIs com aging buckets

**Files:**
- Test: `tests/unit/test_finance_aging.py`
- Modify: `app/services/finance_service.py` (acrescentar após `build_receivables_by_client`)

- [ ] **Step 1: Escrever testes**

```python
"""Testes de build_aging_summary."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, f)
    f.tables["clientes"].append({"id": "c1", "nome": "X", "celular": "5511999990001"})
    f.tables["vendas"].extend([
        {"id": f"v{i}", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 100.0}
        for i in range(1, 8)
    ])
    f.tables["pacotes"].append({"id": "p1", "enquete_id": "e1"})
    f.tables["enquetes"].append({"id": "e1", "titulo": "E"})
    return f


def _add_pag(fake, idx, status, created_at):
    fake.tables["pagamentos"].append({
        "id": f"pg{idx}", "venda_id": f"v{idx}", "status": status,
        "created_at": created_at,
    })


def test_buckets_boundaries(fake):
    # Cada pagamento R$100, datas escolhidas pra cair em cada bucket
    _add_pag(fake, 1, "sent", "2026-05-12T00:00:00+00:00")  # 0d → 0-7
    _add_pag(fake, 2, "sent", "2026-05-05T00:00:00+00:00")  # 7d → 0-7
    _add_pag(fake, 3, "sent", "2026-05-04T00:00:00+00:00")  # 8d → 8-15
    _add_pag(fake, 4, "sent", "2026-04-27T00:00:00+00:00")  # 15d → 8-15
    _add_pag(fake, 5, "sent", "2026-04-26T00:00:00+00:00")  # 16d → 16-30
    _add_pag(fake, 6, "sent", "2026-04-12T00:00:00+00:00")  # 30d → 16-30
    _add_pag(fake, 7, "sent", "2026-04-11T00:00:00+00:00")  # 31d → 30+

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")

    assert s["total_receivable"] == 700.0
    assert s["count"] == 7
    assert s["clients_count"] == 1
    assert s["buckets"]["0-7"]["amount"] == 200.0
    assert s["buckets"]["8-15"]["amount"] == 200.0
    assert s["buckets"]["16-30"]["amount"] == 200.0
    assert s["buckets"]["30+"]["amount"] == 100.0


def test_paid_rate_30d(fake):
    # 3 pagos + 1 pendente nos últimos 30d → paid_rate = 300/400 = 0.75
    _add_pag(fake, 1, "paid", "2026-05-01T00:00:00+00:00")
    _add_pag(fake, 2, "paid", "2026-04-28T00:00:00+00:00")
    _add_pag(fake, 3, "paid", "2026-04-20T00:00:00+00:00")
    _add_pag(fake, 4, "sent", "2026-05-05T00:00:00+00:00")
    # Fora da janela: deve ser ignorado
    _add_pag(fake, 5, "paid", "2026-01-01T00:00:00+00:00")

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["paid_rate_30d"] == pytest.approx(0.75)


def test_avg_age_weighted_by_value(fake):
    # v1=100/sent/10d, v2=300/sent/30d → avg = (100*10 + 300*30) / 400 = 25
    fake.tables["vendas"][0]["total_amount"] = 100.0
    fake.tables["vendas"][1]["total_amount"] = 300.0
    _add_pag(fake, 1, "sent", "2026-05-02T00:00:00+00:00")  # 10d
    _add_pag(fake, 2, "sent", "2026-04-12T00:00:00+00:00")  # 30d

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["avg_age_days"] == pytest.approx(25.0)


def test_empty_returns_zeros(fake):
    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["total_receivable"] == 0
    assert s["count"] == 0
    assert s["clients_count"] == 0
    assert s["avg_age_days"] == 0
    assert s["paid_rate_30d"] == 0
    for label in ("0-7", "8-15", "16-30", "30+"):
        assert s["buckets"][label] == {"amount": 0, "count": 0}
```

- [ ] **Step 2: Rodar testes — devem falhar**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_aging.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementar**

Adicionar após `build_receivables_by_client`:

```python
def build_aging_summary(now_iso: str | None = None) -> Dict[str, Any]:
    """KPIs de aging: total a receber, distribuição em buckets,
    idade média ponderada e taxa de conversão 30d.

    Retorna: {total_receivable, count, clients_count,
              buckets:{"0-7":{amount,count}, ...},
              avg_age_days, paid_rate_30d}.
    """
    empty_buckets = {label: {"amount": 0, "count": 0} for label, _, _ in AGING_BUCKETS}
    empty = {
        "total_receivable": 0, "count": 0, "clients_count": 0,
        "buckets": empty_buckets, "avg_age_days": 0, "paid_rate_30d": 0,
    }
    if not supabase_domain_enabled():
        return empty

    client = SupabaseRestClient.from_settings()
    now = _now_dt(now_iso)

    pagamentos_pendentes = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("status", "in", list(PENDING_RECEIVABLE_STATUSES))],
    )
    if not pagamentos_pendentes:
        empty_with_paid_rate = dict(empty)
        empty_with_paid_rate["paid_rate_30d"] = _paid_rate_30d(client, now)
        return empty_with_paid_rate

    venda_ids = list({str(p["venda_id"]) for p in pagamentos_pendentes if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,total_amount",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}

    buckets = {label: {"amount": 0.0, "count": 0} for label, _, _ in AGING_BUCKETS}
    total = 0.0
    weighted_age_sum = 0.0
    clientes = set()

    for pag in pagamentos_pendentes:
        venda = venda_by_id.get(str(pag.get("venda_id")))
        if not venda:
            continue
        valor = float(venda.get("total_amount") or 0)
        created_at = _parse_dt(pag.get("created_at"))
        age_days = (now - created_at).days if created_at else 0
        bucket = _classify_bucket(age_days)
        buckets[bucket]["amount"] += valor
        buckets[bucket]["count"] += 1
        total += valor
        weighted_age_sum += valor * age_days
        if venda.get("cliente_id"):
            clientes.add(str(venda["cliente_id"]))

    return {
        "total_receivable": round(total, 2),
        "count": sum(b["count"] for b in buckets.values()),
        "clients_count": len(clientes),
        "buckets": {k: {"amount": round(v["amount"], 2), "count": v["count"]}
                    for k, v in buckets.items()},
        "avg_age_days": round(weighted_age_sum / total, 2) if total > 0 else 0,
        "paid_rate_30d": _paid_rate_30d(client, now),
    }


def _paid_rate_30d(client: SupabaseRestClient, now: datetime) -> float:
    """% de R$ pago sobre o total confirmado nos últimos 30d (rolling)."""
    cutoff = (now - timedelta(days=30)).isoformat()
    pagamentos = client.select_all(
        "pagamentos",
        columns="id,venda_id,status,created_at",
        filters=[("created_at", "gte", cutoff)],
    )
    if not pagamentos:
        return 0
    venda_ids = list({str(p["venda_id"]) for p in pagamentos if p.get("venda_id")})
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,total_amount",
        filter_field="id", values=venda_ids,
    )
    valor_by_venda = {str(v["id"]): float(v.get("total_amount") or 0) for v in vendas}

    total = 0.0
    paid = 0.0
    for p in pagamentos:
        valor = valor_by_venda.get(str(p.get("venda_id")), 0)
        status = str(p.get("status") or "")
        if status in ("paid", "created", "sent"):
            total += valor
            if status == "paid":
                paid += valor
    return round(paid / total, 4) if total > 0 else 0
```

- [ ] **Step 4: Rodar testes**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_aging.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance_service.py tests/unit/test_finance_aging.py
git commit -m "feat(finance): build_aging_summary com buckets e paid_rate_30d"
```

### Task 5: `build_payment_history` — timeline derivada

**Files:**
- Test: `tests/unit/test_finance_history.py`
- Modify: `app/services/finance_service.py`

- [ ] **Step 1: Escrever testes**

```python
"""Testes de build_payment_history."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, f)
    f.tables["clientes"].append({
        "id": "c1", "nome": "Ana", "celular": "5511999990001",
        "session_expires_at": "2026-06-10T00:00:00+00:00",
    })
    f.tables["vendas"].append({"id": "v1", "cliente_id": "c1", "pacote_id": "p1"})
    return f


def test_basic_timeline(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-03T14:00:00+00:00",
        "pix_payload": "00020126...",
        "paid_at": None,
        "written_off_at": None,
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")

    kinds = [e["kind"] for e in events]
    assert "package_confirmed" in kinds
    assert "pix_generated" in kinds
    assert "last_portal_access" in kinds
    # Cronologicamente ordenado
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_paid_event_present(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "paid",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-04T10:00:00+00:00",
        "pix_payload": "00020126...",
        "paid_at": "2026-05-05T11:00:00+00:00",
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    assert any(e["kind"] == "paid" for e in events)


def test_no_pix_event_when_no_payload(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "created",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-01T10:00:00+00:00",
        "pix_payload": None,
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    assert not any(e["kind"] == "pix_generated" for e in events)


def test_written_off_event(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "written_off",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-10T10:00:00+00:00",
        "written_off_at": "2026-05-10T10:00:00+00:00",
        "written_off_reason": "Cliente sumiu",
    })

    from app.services.finance_service import build_payment_history
    events = build_payment_history("pg1")
    wo = [e for e in events if e["kind"] == "written_off"]
    assert len(wo) == 1
    assert wo[0]["reason"] == "Cliente sumiu"


def test_returns_empty_for_unknown_id(fake):
    from app.services.finance_service import build_payment_history
    assert build_payment_history("nope") == []
```

- [ ] **Step 2: Rodar testes — devem falhar**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_history.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementar**

Adicionar após `_paid_rate_30d`:

```python
def build_payment_history(pagamento_id: str) -> List[Dict[str, Any]]:
    """Timeline derivada dos campos do pagamento + sessão do cliente.

    Cada evento: {kind, timestamp, label, reason?}.
    """
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    pag = client.select(
        "pagamentos",
        columns="id,venda_id,status,created_at,updated_at,pix_payload,paid_at,"
                "written_off_at,written_off_reason",
        filters=[("id", "eq", pagamento_id)],
        single=True,
    )
    if not isinstance(pag, dict) or not pag.get("id"):
        return []

    events: List[Dict[str, Any]] = []
    if pag.get("created_at"):
        events.append({
            "kind": "package_confirmed",
            "timestamp": pag["created_at"],
            "label": "Pacote confirmado",
        })
    if pag.get("pix_payload") and pag.get("updated_at"):
        events.append({
            "kind": "pix_generated",
            "timestamp": pag["updated_at"],
            "label": "PIX gerado (última tentativa registrada)",
        })

    venda = client.select(
        "vendas", columns="cliente_id",
        filters=[("id", "eq", pag.get("venda_id"))], single=True,
    )
    if isinstance(venda, dict) and venda.get("cliente_id"):
        cliente = client.select(
            "clientes", columns="session_expires_at",
            filters=[("id", "eq", venda["cliente_id"])], single=True,
        )
        if isinstance(cliente, dict) and cliente.get("session_expires_at"):
            expires = _parse_dt(cliente["session_expires_at"])
            if expires:
                # Sessão dura 30 dias — último acesso é expires - 30d
                last_access = (expires - timedelta(days=30)).isoformat()
                events.append({
                    "kind": "last_portal_access",
                    "timestamp": last_access,
                    "label": "Último acesso ao portal",
                })

    if pag.get("paid_at"):
        events.append({
            "kind": "paid",
            "timestamp": pag["paid_at"],
            "label": "Pago",
        })
    if pag.get("written_off_at"):
        events.append({
            "kind": "written_off",
            "timestamp": pag["written_off_at"],
            "label": "Marcado como perdido",
            "reason": pag.get("written_off_reason") or "",
        })

    events.sort(key=lambda e: e["timestamp"])
    return events
```

- [ ] **Step 4: Rodar testes**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_history.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance_service.py tests/unit/test_finance_history.py
git commit -m "feat(finance): build_payment_history com timeline derivada"
```

### Task 6: `mark_payment_written_off` — write-off service

**Files:**
- Test: `tests/unit/test_finance_writeoff.py`
- Modify: `app/services/finance_service.py`

- [ ] **Step 1: Escrever testes**

```python
"""Testes de mark_payment_written_off."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, f)
    return f


def test_marks_as_written_off(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
    })
    from app.services.finance_service import mark_payment_written_off
    out = mark_payment_written_off("pg1", reason="Cliente abandonou")
    assert out["status"] == "written_off"
    assert out["written_off_reason"] == "Cliente abandonou"
    assert out["written_off_at"] == fake.now_iso()
    assert fake.tables["pagamentos"][0]["status"] == "written_off"


def test_returns_404_when_missing(fake):
    from app.services.finance_service import mark_payment_written_off, PaymentNotFound
    with pytest.raises(PaymentNotFound):
        mark_payment_written_off("ghost", reason="x")


def test_idempotent_when_already_written_off(fake):
    fake.tables["pagamentos"].append({
        "id": "pg1", "status": "written_off",
        "written_off_at": "2026-05-01T00:00:00+00:00",
        "written_off_reason": "Old",
    })
    from app.services.finance_service import mark_payment_written_off
    out = mark_payment_written_off("pg1", reason="Novo")
    # Não sobrescreve: mantém timestamp e reason originais
    assert out["written_off_at"] == "2026-05-01T00:00:00+00:00"
    assert out["written_off_reason"] == "Old"
```

- [ ] **Step 2: Rodar testes — falham**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_writeoff.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementar**

```python
class PaymentNotFound(Exception):
    pass


def mark_payment_written_off(pagamento_id: str, *, reason: str) -> Dict[str, Any]:
    """Marca um pagamento como perdido. Idempotente: se já está written_off,
    retorna o estado atual sem sobrescrever."""
    if not supabase_domain_enabled():
        raise PaymentNotFound(pagamento_id)
    client = SupabaseRestClient.from_settings()
    existing = client.select(
        "pagamentos",
        columns="id,status,written_off_at,written_off_reason",
        filters=[("id", "eq", pagamento_id)],
        single=True,
    )
    if not isinstance(existing, dict) or not existing.get("id"):
        raise PaymentNotFound(pagamento_id)
    if existing.get("status") == "written_off":
        return existing

    now_iso = client.now_iso() if hasattr(client, "now_iso") else \
        datetime.now(tz=ZoneInfo("UTC")).isoformat()
    rows = client.update(
        "pagamentos",
        {
            "status": "written_off",
            "written_off_at": now_iso,
            "written_off_reason": reason,
            "updated_at": now_iso,
        },
        filters=[("id", "eq", pagamento_id)],
        returning="representation",
    )
    return rows[0] if rows else {**existing, "status": "written_off",
                                  "written_off_at": now_iso,
                                  "written_off_reason": reason}
```

- [ ] **Step 4: Rodar testes**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_writeoff.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance_service.py tests/unit/test_finance_writeoff.py
git commit -m "feat(finance): mark_payment_written_off idempotente"
```

---

## Fase 3: Endpoints

### Task 7: 4 endpoints novos em `app/routers/finance.py`

**Files:**
- Modify: `app/routers/finance.py`
- Test: `tests/unit/test_finance_endpoints.py`

- [ ] **Step 1: Escrever testes dos endpoints**

```python
"""Testes dos endpoints /api/finance/* novos."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def client_fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, f)
    import main as main_module
    return TestClient(main_module.app), f


def test_get_receivables_returns_list(client_fake):
    client, fake = client_fake
    fake.tables["clientes"].append({"id": "c1", "nome": "A", "celular": "5511999990001"})
    fake.tables["vendas"].append({"id": "v1", "cliente_id": "c1", "pacote_id": "p1",
                                   "total_amount": 100.0})
    fake.tables["pacotes"].append({"id": "p1", "enquete_id": "e1"})
    fake.tables["enquetes"].append({"id": "e1", "titulo": "E"})
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
    })
    res = client.get("/api/finance/receivables")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["cliente_id"] == "c1"


def test_get_aging_summary(client_fake):
    client, fake = client_fake
    res = client.get("/api/finance/aging-summary")
    assert res.status_code == 200
    body = res.json()
    assert "total_receivable" in body
    assert "buckets" in body
    assert set(body["buckets"].keys()) == {"0-7", "8-15", "16-30", "30+"}


def test_write_off_marks_payment(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({"id": "pg1", "status": "sent", "venda_id": "v1"})
    res = client.post(
        "/api/finance/pagamentos/pg1/write-off",
        json={"reason": "Cliente sumiu"},
    )
    assert res.status_code == 200
    assert fake.tables["pagamentos"][0]["status"] == "written_off"


def test_write_off_404_for_unknown(client_fake):
    client, _ = client_fake
    res = client.post(
        "/api/finance/pagamentos/ghost/write-off",
        json={"reason": "x"},
    )
    assert res.status_code == 404


def test_write_off_400_when_reason_empty(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({"id": "pg1", "status": "sent", "venda_id": "v1"})
    res = client.post(
        "/api/finance/pagamentos/pg1/write-off",
        json={"reason": "  "},
    )
    assert res.status_code == 400


def test_history_returns_timeline(client_fake):
    client, fake = client_fake
    fake.tables["pagamentos"].append({
        "id": "pg1", "venda_id": "v1", "status": "sent",
        "created_at": "2026-05-01T10:00:00+00:00",
        "updated_at": "2026-05-01T10:00:00+00:00",
    })
    res = client.get("/api/finance/pagamentos/pg1/history")
    assert res.status_code == 200
    events = res.json()
    assert any(e["kind"] == "package_confirmed" for e in events)
```

- [ ] **Step 2: Rodar — falham com 404 ou 405**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_endpoints.py -v
```

Expected: 6 failed.

- [ ] **Step 3: Adicionar endpoints**

Em `app/routers/finance.py`, após `get_stats()`:

```python
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.finance_service import (
    build_receivables_by_client,
    build_aging_summary,
    build_payment_history,
    mark_payment_written_off,
    PaymentNotFound,
)


class WriteOffRequest(BaseModel):
    reason: str = Field(min_length=1)


@router.get("/receivables")
async def get_receivables() -> List[Dict[str, Any]]:
    """Contas a receber agregadas por cliente."""
    try:
        return build_receivables_by_client()
    except Exception:
        logger.exception("Erro ao agregar receivables")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.get("/aging-summary")
async def get_aging_summary() -> Dict[str, Any]:
    """KPIs de aging para o topo da aba."""
    try:
        return build_aging_summary()
    except Exception:
        logger.exception("Erro ao construir aging summary")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.post("/pagamentos/{pagamento_id}/write-off")
async def post_write_off(pagamento_id: str, body: WriteOffRequest) -> Dict[str, Any]:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")
    try:
        return mark_payment_written_off(pagamento_id, reason=reason)
    except PaymentNotFound:
        raise HTTPException(status_code=404, detail="pagamento not found")


@router.get("/pagamentos/{pagamento_id}/history")
async def get_payment_history(pagamento_id: str) -> List[Dict[str, Any]]:
    return build_payment_history(pagamento_id)
```

- [ ] **Step 4: Rodar testes**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_finance_endpoints.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Rodar suíte completa pra confirmar zero regressão**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/ -q
```

Expected: tudo verde.

- [ ] **Step 6: Commit**

```bash
git add app/routers/finance.py tests/unit/test_finance_endpoints.py
git commit -m "feat(finance): endpoints receivables/aging/write-off/history"
```

---

## Fase 4: Frontend

### Task 8: Substituir bloco `section-finance` no template

**Files:**
- Modify: `templates/index.html:211-292`

- [ ] **Step 1: Substituir o bloco**

Trocar o conteúdo entre `<div id="section-finance" style="display: none;">` e o `</div>` que fecha antes de `<div id="section-customers">` pelo seguinte:

```html
<div id="section-finance" style="display: none;">
    <!-- KPIs -->
    <div class="kpi-container finance-summary" style="margin-bottom: 2rem;">
        <div class="kpi-card">
            <div class="kpi-title">Total a receber</div>
            <div class="kpi-value" id="finance-receivable-total">R$ 0,00</div>
            <div class="kpi-diff" id="finance-receivable-meta">0 cobranças · 0 clientes</div>
        </div>
        <div class="kpi-card kpi-aging">
            <div class="kpi-title">Aging</div>
            <div class="aging-bar" id="finance-aging-bar">
                <div class="aging-bucket aging-bucket-0-7" data-bucket="0-7"></div>
                <div class="aging-bucket aging-bucket-8-15" data-bucket="8-15"></div>
                <div class="aging-bucket aging-bucket-16-30" data-bucket="16-30"></div>
                <div class="aging-bucket aging-bucket-30-plus" data-bucket="30+"></div>
            </div>
            <div class="aging-legend" id="finance-aging-legend"></div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Idade média do débito</div>
            <div class="kpi-value" id="finance-avg-age">0d</div>
            <div class="kpi-diff" id="finance-avg-age-trend">—</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">% pago vs confirmado (30d)</div>
            <div class="kpi-value" id="finance-paid-rate">0%</div>
        </div>
    </div>

    <div class="card-item full">
        <div class="card-title" style="display: flex; justify-content: space-between; align-items: center;">
            <span><i class="fas fa-wallet"></i> Contas a Receber</span>
            <div style="display: flex; gap: 0.75rem; align-items: center;">
                <div class="view-toggle">
                    <button class="toggle-btn active" data-mode="by-client">Por cliente</button>
                    <button class="toggle-btn" data-mode="by-charge">Por cobrança</button>
                </div>
                <button class="btn-refresh" id="btn-sync-asaas" style="padding: 0.45rem 0.75rem; font-size: 0.8rem;" title="Força verificação de pagamentos no Asaas">
                    <i class="fas fa-rotate"></i> Sincronizar
                </button>
                <div class="search-box">
                    <input type="text" id="finance-search" placeholder="Buscar cliente..." class="search-input">
                </div>
            </div>
        </div>

        <div class="finance-filters">
            <button class="filter-btn active" data-filter="all">Todos</button>
            <button class="filter-btn" data-filter="0-7">0-7d</button>
            <button class="filter-btn" data-filter="8-15">8-15d</button>
            <button class="filter-btn" data-filter="16-30">16-30d</button>
            <button class="filter-btn" data-filter="30+">30+d</button>
            <button class="filter-btn" data-filter="written_off">Perdidos</button>
        </div>

        <div class="finance-table-container">
            <table class="finance-table" id="finance-table">
                <thead id="finance-thead">
                    <tr>
                        <th>Cliente</th>
                        <th>Celular</th>
                        <th>Total devido</th>
                        <th>Cobranças</th>
                        <th>Idade do mais antigo</th>
                        <th style="text-align: right; width: 60px;"></th>
                    </tr>
                </thead>
                <tbody id="finance-table-body">
                    <!-- JS Loaded -->
                </tbody>
            </table>
        </div>
        <div class="pagination-bar">
            <div class="pagination-summary" id="finance-pagination-summary">Página 1</div>
            <div class="pagination-actions">
                <button type="button" class="pagination-btn" id="finance-page-prev">Anterior</button>
                <button type="button" class="pagination-btn" id="finance-page-next">Próxima</button>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Adicionar `<script src="/static/js/finance.js"></script>` antes do `</body>` em `index.html` (logo após o `<script src="/static/js/dashboard.js"></script>` existente)**

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "ui(finance): novo layout da aba (KPIs aging + tabela)"
```

### Task 9: Criar `static/js/finance.js`

**Files:**
- Create: `static/js/finance.js`

- [ ] **Step 1: Escrever o módulo**

```javascript
/* Aba Financeiro — Contas a Receber */
(function () {
    "use strict";

    const BUCKET_COLORS = {
        "0-7": "#22c55e",
        "8-15": "#eab308",
        "16-30": "#f97316",
        "30+": "#ef4444",
    };
    const fmtMoney = (v) =>
        "R$ " + Number(v || 0).toFixed(2).replace(".", ",");

    const state = {
        mode: "by-client",      // "by-client" | "by-charge"
        filter: "all",          // "all" | "0-7" | ... | "written_off"
        search: "",
        receivables: [],
        page: 1,
        pageSize: 25,
    };

    function el(id) { return document.getElementById(id); }

    // ---- KPIs ----
    async function loadAgingSummary() {
        const res = await fetch("/api/finance/aging-summary", { credentials: "same-origin" });
        if (!res.ok) return;
        const s = await res.json();

        el("finance-receivable-total").textContent = fmtMoney(s.total_receivable);
        el("finance-receivable-meta").textContent =
            `${s.count} cobranças · ${s.clients_count} clientes`;

        const total = s.total_receivable || 1;
        const bar = el("finance-aging-bar");
        bar.querySelectorAll(".aging-bucket").forEach((seg) => {
            const b = seg.dataset.bucket;
            const amount = (s.buckets[b] || {}).amount || 0;
            seg.style.width = ((amount / total) * 100).toFixed(1) + "%";
            seg.title = `${b}: ${fmtMoney(amount)} (${(s.buckets[b] || {}).count || 0})`;
        });
        el("finance-aging-legend").innerHTML = ["0-7", "8-15", "16-30", "30+"]
            .map((b) => {
                const item = s.buckets[b] || {};
                return `<span class="aging-legend-item" style="--c:${BUCKET_COLORS[b]}">
                    <span class="aging-legend-dot"></span>${b}d: ${fmtMoney(item.amount)}
                </span>`;
            }).join("");

        el("finance-avg-age").textContent = (s.avg_age_days || 0).toFixed(0) + "d";
        el("finance-paid-rate").textContent =
            ((s.paid_rate_30d || 0) * 100).toFixed(0) + "%";
    }

    // ---- Receivables ----
    async function loadReceivables() {
        const res = await fetch("/api/finance/receivables", { credentials: "same-origin" });
        if (!res.ok) return;
        state.receivables = await res.json();
        render();
    }

    function filterRows() {
        let rows = state.receivables;
        if (state.filter === "written_off") {
            return [];  // TODO Fase 5: endpoint /receivables?include_written_off
        }
        if (state.filter !== "all") {
            rows = rows.filter((r) => r.bucket === state.filter);
        }
        if (state.search) {
            const q = state.search.toLowerCase();
            rows = rows.filter((r) =>
                (r.nome || "").toLowerCase().includes(q) ||
                (r.celular_last4 || "").includes(q)
            );
        }
        return rows;
    }

    function render() {
        const tbody = el("finance-table-body");
        const rows = filterRows();
        const start = (state.page - 1) * state.pageSize;
        const pageRows = rows.slice(start, start + state.pageSize);
        tbody.innerHTML = "";

        if (state.mode === "by-charge") {
            renderByCharge(tbody, pageRows);
        } else {
            renderByClient(tbody, pageRows);
        }

        el("finance-pagination-summary").textContent =
            `Página ${state.page} de ${Math.max(1, Math.ceil(rows.length / state.pageSize))} (${rows.length} resultados)`;
    }

    function renderByClient(tbody, rows) {
        rows.forEach((r) => {
            const tr = document.createElement("tr");
            tr.className = "client-row";
            tr.innerHTML = `
                <td>${escapeHtml(r.nome)}</td>
                <td>***${escapeHtml(r.celular_last4 || "")}</td>
                <td>${fmtMoney(r.total)}</td>
                <td>${r.count}</td>
                <td><span class="aging-badge bucket-${r.bucket.replace("+","plus")}">${r.oldest_age_days}d</span></td>
                <td style="text-align:right;">
                    <button class="btn-expand" data-cliente="${r.cliente_id}"><i class="fas fa-chevron-right"></i></button>
                </td>
            `;
            tbody.appendChild(tr);

            const expandTr = document.createElement("tr");
            expandTr.className = "client-expand";
            expandTr.dataset.cliente = r.cliente_id;
            expandTr.style.display = "none";
            expandTr.innerHTML = `
                <td colspan="6">
                    <table class="charges-mini">
                        <thead>
                            <tr><th>Pacote</th><th>Valor</th><th>Idade</th><th>Status</th><th></th></tr>
                        </thead>
                        <tbody>
                            ${r.charges.map((c) => `
                                <tr>
                                    <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                                    <td>${fmtMoney(c.valor)}</td>
                                    <td>${c.age_days}d</td>
                                    <td>${c.status}</td>
                                    <td>
                                        <button class="btn-history" data-pag="${c.pagamento_id}" title="Histórico"><i class="fas fa-scroll"></i></button>
                                        <button class="btn-writeoff" data-pag="${c.pagamento_id}" data-cliente-nome="${escapeHtml(r.nome)}" data-valor="${c.valor}" data-pacote="${escapeHtml(c.enquete_titulo)}" title="Marcar como perdido"><i class="fas fa-times-circle"></i></button>
                                    </td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </td>
            `;
            tbody.appendChild(expandTr);
        });
    }

    function renderByCharge(tbody, rows) {
        // Flat list: 1 linha por cobrança
        const charges = [];
        rows.forEach((r) => r.charges.forEach((c) =>
            charges.push({ ...c, nome: r.nome, celular_last4: r.celular_last4 })
        ));
        charges.forEach((c) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${escapeHtml(c.nome)}</td>
                <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                <td>${fmtMoney(c.valor)}</td>
                <td>${c.status}</td>
                <td>${c.age_days}d</td>
                <td style="text-align:right;">
                    <button class="btn-history" data-pag="${c.pagamento_id}"><i class="fas fa-scroll"></i></button>
                    <button class="btn-writeoff" data-pag="${c.pagamento_id}" data-cliente-nome="${escapeHtml(c.nome)}" data-valor="${c.valor}" data-pacote="${escapeHtml(c.enquete_titulo)}"><i class="fas fa-times-circle"></i></button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // ---- Expand handler ----
    document.addEventListener("click", (ev) => {
        const expandBtn = ev.target.closest(".btn-expand");
        if (expandBtn) {
            const id = expandBtn.dataset.cliente;
            const row = document.querySelector(`.client-expand[data-cliente="${id}"]`);
            if (row) {
                const open = row.style.display !== "none";
                row.style.display = open ? "none" : "table-row";
                expandBtn.querySelector("i").className =
                    "fas " + (open ? "fa-chevron-right" : "fa-chevron-down");
            }
            return;
        }

        const wo = ev.target.closest(".btn-writeoff");
        if (wo) {
            openWriteOffModal(wo.dataset);
            return;
        }

        const hist = ev.target.closest(".btn-history");
        if (hist) {
            openHistoryModal(hist.dataset.pag);
            return;
        }
    });

    // ---- Toggle modo + filtros + busca ----
    document.querySelectorAll(".view-toggle .toggle-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".view-toggle .toggle-btn")
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.mode = btn.dataset.mode;
            state.page = 1;
            updateHead();
            render();
        });
    });

    function updateHead() {
        const thead = el("finance-thead");
        if (state.mode === "by-charge") {
            thead.innerHTML = `<tr><th>Cliente</th><th>Pacote</th><th>Valor</th><th>Status</th><th>Idade</th><th></th></tr>`;
        } else {
            thead.innerHTML = `<tr><th>Cliente</th><th>Celular</th><th>Total devido</th><th>Cobranças</th><th>Idade do mais antigo</th><th></th></tr>`;
        }
    }

    document.querySelectorAll("#section-finance .filter-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#section-finance .filter-btn")
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.filter = btn.dataset.filter;
            state.page = 1;
            render();
        });
    });

    const search = el("finance-search");
    if (search) {
        search.addEventListener("input", () => {
            state.search = search.value.trim();
            state.page = 1;
            render();
        });
    }

    el("finance-page-prev")?.addEventListener("click", () => {
        if (state.page > 1) { state.page -= 1; render(); }
    });
    el("finance-page-next")?.addEventListener("click", () => {
        state.page += 1; render();
    });

    // ---- Modais write-off + histórico ----
    function openWriteOffModal(data) {
        const reason = prompt(
            `Marcar como perdido?\n\nCliente: ${data.clienteNome}\nPacote: ${data.pacote}\nValor: ${fmtMoney(data.valor)}\n\nMotivo (obrigatório):`
        );
        if (!reason || !reason.trim()) return;
        fetch(`/api/finance/pagamentos/${data.pag}/write-off`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ reason: reason.trim() }),
        }).then((r) => {
            if (!r.ok) { alert("Erro ao marcar como perdido"); return; }
            return refreshAll();
        });
    }

    async function openHistoryModal(pagId) {
        const res = await fetch(`/api/finance/pagamentos/${pagId}/history`, { credentials: "same-origin" });
        if (!res.ok) { alert("Erro ao carregar histórico"); return; }
        const events = await res.json();
        const html = events.map((e) => `
            <div class="history-event">
                <strong>${escapeHtml(e.label)}</strong>
                <span class="history-ts">${formatTs(e.timestamp)}</span>
                ${e.reason ? `<div class="history-reason">${escapeHtml(e.reason)}</div>` : ""}
            </div>
        `).join("") || "<p>Sem eventos registrados.</p>";
        showModal("Histórico do pagamento", html);
    }

    function formatTs(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        return d.toLocaleDateString("pt-BR") + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function showModal(title, bodyHtml) {
        let backdrop = document.getElementById("finance-modal-backdrop");
        if (!backdrop) {
            backdrop = document.createElement("div");
            backdrop.id = "finance-modal-backdrop";
            backdrop.className = "modal-backdrop";
            backdrop.innerHTML = `
                <div class="modal-card">
                    <div class="modal-head"><h3 id="finance-modal-title"></h3>
                        <button class="modal-close">&times;</button></div>
                    <div class="modal-body" id="finance-modal-body"></div>
                </div>`;
            document.body.appendChild(backdrop);
            backdrop.querySelector(".modal-close").addEventListener("click",
                () => backdrop.style.display = "none");
        }
        document.getElementById("finance-modal-title").textContent = title;
        document.getElementById("finance-modal-body").innerHTML = bodyHtml;
        backdrop.style.display = "flex";
    }

    // ---- Refresh ----
    async function refreshAll() {
        await Promise.all([loadAgingSummary(), loadReceivables()]);
    }

    // ---- Hook na navegação: carrega quando aba abre ----
    const navItem = document.querySelector('.nav-item[data-target="finance"]');
    if (navItem) {
        navItem.addEventListener("click", () => {
            setTimeout(refreshAll, 50);  // após dashboard.js mostrar a section
        });
    }

    // Auto-load se já estiver na aba ao boot (URL com hash, etc)
    if (document.getElementById("section-finance")?.style.display !== "none") {
        refreshAll();
    }

    // Expor pra dashboard.js poder forçar refresh quando preciso
    window.financeRefresh = refreshAll;
})();
```

- [ ] **Step 2: Commit**

```bash
git add static/js/finance.js
git commit -m "ui(finance): finance.js novo (KPIs, tabela por cliente/cobranca, write-off, historico)"
```

### Task 10: Adicionar CSS

**Files:**
- Modify: `static/css/dashboard.css` (append no final)

- [ ] **Step 1: Acrescentar classes**

```css
/* F-062 — Aging bar + tabela financeiro */
.aging-bar {
    display: flex;
    width: 100%;
    height: 18px;
    border-radius: 9px;
    overflow: hidden;
    background: rgba(255,255,255,0.05);
    margin: 0.5rem 0;
}
.aging-bucket { height: 100%; transition: width .3s ease; }
.aging-bucket-0-7    { background: #22c55e; }
.aging-bucket-8-15   { background: #eab308; }
.aging-bucket-16-30  { background: #f97316; }
.aging-bucket-30-plus{ background: #ef4444; }

.aging-legend {
    display: flex;
    gap: 0.75rem;
    flex-wrap: wrap;
    font-size: 0.75rem;
    margin-top: 0.5rem;
}
.aging-legend-item { display: inline-flex; align-items: center; gap: 0.3rem; }
.aging-legend-dot  {
    width: 8px; height: 8px; border-radius: 50%; background: var(--c, #999);
}

.aging-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    color: #fff;
}
.aging-badge.bucket-0-7    { background: #22c55e; }
.aging-badge.bucket-8-15   { background: #eab308; color: #422; }
.aging-badge.bucket-16-30  { background: #f97316; }
.aging-badge.bucket-30plus { background: #ef4444; }

.view-toggle {
    display: inline-flex;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    overflow: hidden;
}
.view-toggle .toggle-btn {
    background: transparent;
    color: var(--text-muted, #aaa);
    padding: 0.4rem 0.8rem;
    border: none;
    cursor: pointer;
    font-size: 0.8rem;
}
.view-toggle .toggle-btn.active {
    background: rgba(255,255,255,0.08);
    color: #fff;
}

.client-expand td { background: rgba(255,255,255,0.03); padding: 0.5rem 1rem; }
.charges-mini { width: 100%; font-size: 0.8rem; }
.charges-mini th, .charges-mini td { padding: 0.3rem 0.6rem; }

.btn-expand, .btn-history, .btn-writeoff {
    background: transparent;
    border: none;
    color: var(--text-muted, #aaa);
    cursor: pointer;
    padding: 0.3rem;
}
.btn-writeoff:hover { color: #ef4444; }
.btn-history:hover  { color: #22c55e; }

.modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 9999;
}
.modal-card {
    background: #1a1a1a; color: #eee;
    border-radius: 12px;
    padding: 1.5rem;
    min-width: 320px;
    max-width: 600px;
    max-height: 80vh;
    overflow: auto;
}
.modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.modal-close {
    background: transparent; border: none; color: #aaa;
    font-size: 1.5rem; cursor: pointer;
}
.history-event { padding: 0.5rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
.history-ts    { margin-left: 0.5rem; color: #888; font-size: 0.8rem; }
.history-reason{ margin-top: 0.3rem; color: #ccc; font-style: italic; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/dashboard.css
git commit -m "ui(finance): css aging-bar, badges, expand e modal"
```

### Task 11: Limpar referências antigas em `dashboard.js`

**Files:**
- Modify: `static/js/dashboard.js`

- [ ] **Step 1: Identificar funções/handlers ligados à aba Financeiro antiga**

Buscar todas as referências no `dashboard.js` aos IDs antigos que foram removidos do template:

```bash
grep -nE "finance-pending-total|finance-paid-today|finance-conversion|finance-revenue-chart|finance-page-jump|btn-open-extract-modal" static/js/dashboard.js
```

- [ ] **Step 2: Remover blocos relacionados a esses IDs**

Para cada ocorrência, apagar o bloco que a usa (event listener, função de render dos KPIs antigos, chamada do chart). Manter referências a IDs que ainda existem (ex: `btn-sync-asaas`, `finance-search`, `finance-table-body`, `finance-page-prev`, `finance-page-next`) — esses agora são gerenciados por `finance.js`, então remover handlers duplicados em `dashboard.js` pra não conflitar.

Regra simples: qualquer função que toque em ID que não existe mais no template → apagar. Qualquer handler que duplica algo em `finance.js` → apagar do dashboard.js.

- [ ] **Step 3: Rodar testes (HTML não testado, mas confirma que backend continua verde)**

```bash
DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/ -q
```

Expected: tudo verde.

- [ ] **Step 4: Validar no browser**

```bash
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000 &
```

Acessar `http://127.0.0.1:8000`, ir na aba Financeiro:
- KPIs aparecem (mesmo que zerados em dev)
- Toggle "Por cliente / Por cobrança" funciona
- Filtros chips alternam
- Busca filtra (com dados de seed)
- Console do browser sem erros JS

- [ ] **Step 5: Commit**

```bash
git add static/js/dashboard.js
git commit -m "ui(finance): remover handlers antigos da aba Financeiro de dashboard.js"
```

---

## Fase 5: Verificação end-to-end e migration em prod

### Task 12: Aplicar migration em prod

**Files:** nenhum

- [ ] **Step 1: Aplicar a migration no Postgres de prod**

```bash
docker exec -i <postgres-container> psql -U raylook_owner -d raylook \
  < deploy/postgres/migrations/F062_pagamento_written_off_status.sql
```

Expected: `COMMIT` no final.

- [ ] **Step 2: Validar**

```bash
docker exec <postgres-container> psql -U raylook_owner -d raylook -c \
  "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='pagamentos_status_check';"
```

Expected: definição inclui `'written_off'`.

```bash
docker exec <postgres-container> psql -U raylook_owner -d raylook -c \
  "SELECT column_name FROM information_schema.columns WHERE table_name='pagamentos' AND column_name IN ('written_off_at','written_off_reason');"
```

Expected: 2 linhas.

### Task 13: Push e deploy

- [ ] **Step 1: Confirmar autorização do push (regra: não pushar sem consultar)**

Perguntar ao usuário: "Posso pushar o branch pra disparar deploy?"

- [ ] **Step 2: Push**

```bash
git push origin main
```

- [ ] **Step 3: Acompanhar deploy**

`https://github.com/rodsaraiva/raylook/actions` até o job `deploy` ficar verde.

- [ ] **Step 4: Smoke test em prod**

Acessar `https://raylook.v4smc.com` → aba Financeiro:
- 4 KPIs aparecem com valores reais
- Toggle "Por cliente / Por cobrança" funciona
- Filtros chips 0-7d/8-15d/16-30d/30+d/Perdidos filtram a tabela
- Expandir cliente mostra cobranças
- Clicar "Histórico" abre modal com timeline
- Clicar "Marcar como perdido" pede motivo, confirma, e cobrança some

- [ ] **Step 5: Verificar no banco**

```bash
docker exec <postgres-container> psql -U raylook_owner -d raylook -c \
  "SELECT status, COUNT(*) FROM pagamentos GROUP BY status;"
```

Expected: `written_off` aparece com pelo menos 1 (caso tenha testado em prod).

---

## Self-review

- [x] **Spec coverage:** todas seções (KPIs, tabela agrupada, toggle, write-off, histórico, migration, testes, plano 5 PRs) têm task correspondente.
- [x] **Placeholders:** nenhum "TBD", "TODO" (exceto um TODO sinalizado em `finance.js` pro filtro de perdidos — endpoint de "perdidos" ficou fora de escopo, é trabalho consciente futuro).
- [x] **Consistência de tipos:** `build_receivables_by_client`, `build_aging_summary`, `build_payment_history`, `mark_payment_written_off` têm assinaturas estáveis entre service, testes e endpoints; chaves de bucket (`"0-7"`, `"8-15"`, `"16-30"`, `"30+"`) idênticas em todo lugar.

**Caveat consciente:** filtro "Perdidos" no frontend retorna lista vazia até criar endpoint `GET /api/finance/receivables?include_written_off=true` (não está nesse plano). Documentado no spec e no código JS.
