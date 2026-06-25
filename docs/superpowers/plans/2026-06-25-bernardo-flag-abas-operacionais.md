# Bernardo: badge de flag + abas operacionais — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar um badge "Bernardo" nos cards de pacote e liberar as abas Estoque/Logística/Financeiro pro usuário `bernardo`, filtradas só pros itens com flag Bernardo.

**Architecture:** Backend ganha um filtro de sessão opcional nos builders de finance (reusando `app.sessions.session_for_title`) e os endpoints forçam esse filtro pelo role logado. `visible_groups("bernardo")` passa a expor os 3 grupos. Frontend renderiza o badge quando `p.session==="Bernardo"`, torna a sessão de Estoque/Logística dependente do role, e esconde a view Créditos pro bernardo.

**Tech Stack:** FastAPI, Python 3.11+, pytest com `FakeSupabaseClient` (DB fake em memória — integração, não mock), JS vanilla, Jinja2.

## Global Constraints

- Match de sessão é **único**: `app.sessions.session_for_title(titulo)` → substring case-insensitive "Bernardo". Nunca reimplementar a regra.
- Testes: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v`. Seeding via `FakeSupabaseClient` (sem mock de DB).
- `RAYLOOK_SANDBOX=true` local; nada bate em API externa.
- Não reescrever `/api/dashboard/packages` — pacotes seguem filtrados no front.
- Créditos fica **oculto** pro bernardo (não filtrado).
- **Não fazer push** (regra do usuário). Commits locais ok.
- Cache-bust: bump `?v=` do `dashboard_v2.js` quando o JS mudar.

## Dependências / paralelismo

- **Wave A (paralelo, arquivos disjuntos):** Task 1 (finance_service), Task 2 (auth), Task 4 (frontend).
- **Wave B (após Task 1):** Task 3 (endpoints de finance — consome o param `session=` da Task 1).

---

### Task 1: Filtro de sessão nos builders de finance

**Files:**
- Modify: `app/services/finance_service.py` (imports + 2 helpers novos + 4 builders)
- Test: `tests/unit/test_finance_session_filter.py` (criar)

**Interfaces:**
- Consumes: `app.sessions.session_for_title(titulo) -> Optional[dict]` (chave `name`).
- Produces:
  - `build_receivables_by_client(now_iso=None, since=None, until=None, session: str | None = None)`
  - `build_aging_summary(now_iso=None, since=None, until=None, session: str | None = None)`
  - `build_paid_by_client(now_iso=None, since=None, until=None, session: str | None = None)`
  - `build_paid_summary(now_iso=None, since=None, until=None, session: str | None = None)`
  - `_title_matches_session(titulo, session) -> bool`
  - `_allowed_venda_ids_for_session(client, venda_by_id, session) -> set[str] | None`

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/unit/test_finance_session_filter.py`:

```python
"""Filtro de sessão Bernardo nos builders de finance."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
from app.services import finance_service
from app.services.finance_service import (
    build_receivables_by_client,
    build_aging_summary,
    build_paid_by_client,
    build_paid_summary,
)

NOW = "2026-05-12T00:00:00+00:00"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    f.tables["clientes"].extend([
        {"id": "c1", "nome": "Ana", "celular": "5511999990001"},
        {"id": "c2", "nome": "Bia", "celular": "5511999990002"},
    ])
    # v1 → enquete Bernardo; v2 → enquete comum
    f.tables["vendas"].extend([
        {"id": "v1", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 400.0,
         "commission_amount": 40.0},
        {"id": "v2", "cliente_id": "c2", "pacote_id": "p2", "total_amount": 600.0,
         "commission_amount": 60.0},
    ])
    f.tables["pacotes"].extend([
        {"id": "p1", "enquete_id": "e1", "sequence_no": 1},
        {"id": "p2", "enquete_id": "e2", "sequence_no": 2},
    ])
    f.tables["enquetes"].extend([
        {"id": "e1", "titulo": "Pacote Bernardo 24"},
        {"id": "e2", "titulo": "Coleção Verão"},
    ])
    return f


def _seed_pending(f):
    f.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "sent", "created_at": "2026-05-01T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "sent", "created_at": "2026-05-01T10:00:00+00:00"},
    ])


def _seed_paid(f):
    f.tables["pagamentos"].extend([
        {"id": "pg1", "venda_id": "v1", "status": "paid",
         "created_at": "2026-05-01T10:00:00+00:00", "paid_at": "2026-05-02T10:00:00+00:00"},
        {"id": "pg2", "venda_id": "v2", "status": "paid",
         "created_at": "2026-05-01T10:00:00+00:00", "paid_at": "2026-05-02T10:00:00+00:00"},
    ])


def test_receivables_none_returns_all(fake):
    _seed_pending(fake)
    rows = build_receivables_by_client(now_iso=NOW, session=None)
    assert {r["cliente_id"] for r in rows} == {"c1", "c2"}


def test_receivables_bernardo_filters_to_bernardo(fake):
    _seed_pending(fake)
    rows = build_receivables_by_client(now_iso=NOW, session="Bernardo")
    assert {r["cliente_id"] for r in rows} == {"c1"}


def test_aging_summary_bernardo_only_counts_bernardo(fake):
    _seed_pending(fake)
    full = build_aging_summary(now_iso=NOW, session=None)
    bern = build_aging_summary(now_iso=NOW, session="Bernardo")
    assert full["total_receivable"] == 1000.0 and full["count"] == 2
    assert bern["total_receivable"] == 400.0 and bern["count"] == 1
    assert bern["clients_count"] == 1


def test_paid_by_client_bernardo_filters(fake):
    _seed_paid(fake)
    rows = build_paid_by_client(now_iso=NOW, session="Bernardo")
    assert {r["cliente_id"] for r in rows} == {"c1"}


def test_paid_summary_bernardo_only_counts_bernardo(fake):
    _seed_paid(fake)
    full = build_paid_summary(now_iso=NOW, session=None)
    bern = build_paid_summary(now_iso=NOW, session="Bernardo")
    assert full["total_paid"] == 1000.0 and full["count"] == 2
    assert bern["total_paid"] == 400.0 and bern["count"] == 1
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_session_filter.py -v`
Expected: FAIL — `build_receivables_by_client() got an unexpected keyword argument 'session'`

- [ ] **Step 3: Adicionar import + helpers**

No topo de `app/services/finance_service.py`, junto aos outros imports de `app.`:

```python
from app.sessions import session_for_title
```

Adicionar os dois helpers logo acima de `def build_receivables_by_client(` (perto de `_classify_bucket`):

```python
def _title_matches_session(titulo: str | None, session: str | None) -> bool:
    """True quando não há filtro (session falsy) ou quando o título casa
    com a sessão alvo. Reusa a regra única de session_for_title."""
    if not session:
        return True
    s = session_for_title(titulo or "")
    return bool(s) and s.get("name") == session


def _allowed_venda_ids_for_session(
    client: "SupabaseRestClient",
    venda_by_id: Dict[str, Dict[str, Any]],
    session: str | None,
) -> set[str] | None:
    """Set de venda_ids cujo título de enquete casa com a sessão.
    Retorna None quando session é falsy (sem filtro). Resolve a cadeia
    venda→pacote→enquete só quando o filtro é pedido."""
    if not session:
        return None
    pacote_ids = list({str(v.get("pacote_id")) for v in venda_by_id.values() if v.get("pacote_id")})
    pacotes = _select_in_batches(
        client, "pacotes", columns="id,enquete_id",
        filter_field="id", values=pacote_ids,
    )
    enquete_ids = list({str(p["enquete_id"]) for p in pacotes if p.get("enquete_id")})
    enquetes = _select_in_batches(
        client, "enquetes", columns="id,titulo",
        filter_field="id", values=enquete_ids,
    )
    title_by_enquete = {str(e["id"]): (e.get("titulo") or "") for e in enquetes}
    title_by_pacote = {
        str(p["id"]): title_by_enquete.get(str(p.get("enquete_id") or ""), "")
        for p in pacotes
    }
    allowed: set[str] = set()
    for vid, v in venda_by_id.items():
        titulo = title_by_pacote.get(str(v.get("pacote_id") or ""), "")
        if _title_matches_session(titulo, session):
            allowed.add(str(vid))
    return allowed
```

- [ ] **Step 4: Adicionar `session` aos 4 builders**

**4a. `build_receivables_by_client`** — adicionar param e o guard inline (o `enquete_titulo` já é resolvido).

Assinatura:
```python
def build_receivables_by_client(
    now_iso: str | None = None,
    since: str | None = None,
    until: str | None = None,
    session: str | None = None,
) -> List[Dict[str, Any]]:
```

No loop `for pag in pagamentos:`, logo após o bloco que calcula `enquete_titulo`
(antes de `created_at = _parse_dt(...)`):
```python
        if not _title_matches_session(enquete_titulo, session):
            continue
```

**4b. `build_paid_by_client`** — idêntico: adicionar param `session` na assinatura
e, no loop `for pag in pagamentos:`, logo após o bloco que calcula `enquete_titulo`
(antes de `paid_at_raw = ...`):
```python
        if not _title_matches_session(enquete_titulo, session):
            continue
```

**4c. `build_aging_summary`** — adicionar param `session`. Trocar a coluna de vendas
pra incluir `pacote_id`:
```python
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,total_amount,pacote_id",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}
    allowed_vendas = _allowed_venda_ids_for_session(client, venda_by_id, session)
```
No loop `for pag in pagamentos_pendentes:`, logo após `if not venda: continue`:
```python
        if allowed_vendas is not None and str(pag.get("venda_id")) not in allowed_vendas:
            continue
```

**4d. `build_paid_summary`** — adicionar param `session`. Trocar a coluna de vendas
pra incluir `pacote_id`:
```python
    vendas = _select_in_batches(
        client, "vendas",
        columns="id,cliente_id,total_amount,commission_amount,pacote_id",
        filter_field="id", values=venda_ids,
    )
    venda_by_id = {str(v["id"]): v for v in vendas}
    allowed_vendas = _allowed_venda_ids_for_session(client, venda_by_id, session)
```
No loop `for pag in pagamentos:`, logo após `if not venda: continue`:
```python
        if allowed_vendas is not None and str(pag.get("venda_id")) not in allowed_vendas:
            continue
```

> Nota: `paid_rate_30d` no aging summary continua global (rolling, KPI secundário). Fora de escopo filtrar.

- [ ] **Step 5: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_session_filter.py tests/unit/test_finance_receivables.py tests/unit/test_finance_aging.py -v`
Expected: PASS (novos + regressão dos existentes verde)

- [ ] **Step 6: Commit**

```bash
git add app/services/finance_service.py tests/unit/test_finance_session_filter.py
git commit -m "feat(finance): filtro opcional de sessão (Bernardo) nos builders"
```

---

### Task 2: visible_groups do bernardo expõe abas operacionais

**Files:**
- Modify: `app/services/auth_service.py:130-131`
- Test: `tests/unit/test_auth_service.py:9-10` (atualizar a asserção existente)

**Interfaces:**
- Produces: `visible_groups("bernardo") == ("bernardo", "estoque", "logistica", "financeiro")`

- [ ] **Step 1: Atualizar o teste (que vai falhar)**

Em `tests/unit/test_auth_service.py`, substituir a função
`test_visible_groups_bernardo_only_sees_bernardo` por:

```python
def test_visible_groups_bernardo_sees_operational_tabs():
    assert auth.visible_groups("bernardo") == (
        "bernardo", "estoque", "logistica", "financeiro",
    )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_auth_service.py -v`
Expected: FAIL — assert `("bernardo",) == ("bernardo","estoque","logistica","financeiro")`

- [ ] **Step 3: Atualizar `visible_groups`**

Em `app/services/auth_service.py`, trocar:
```python
    if role == "bernardo":
        return ("bernardo",)
```
por:
```python
    if role == "bernardo":
        return ("bernardo", "estoque", "logistica", "financeiro")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_auth_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/unit/test_auth_service.py
git commit -m "feat(auth): bernardo enxerga Estoque/Logística/Financeiro"
```

---

### Task 3: Endpoints de finance forçam a sessão pelo role

**Depende da Task 1** (param `session=` nos builders).

**Files:**
- Modify: `app/routers/finance.py` (import `Request`, helper, 4 endpoints)
- Test: `tests/unit/test_finance_endpoints.py` (adicionar testes do helper)

**Interfaces:**
- Consumes: `build_*(..., session=...)` da Task 1; `request.state.role` (setado pelo middleware de auth).
- Produces: `_session_for_request(request) -> Optional[str]`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/unit/test_finance_endpoints.py`:

```python
def test_session_forced_for_bernardo_role():
    import types
    from app.routers.finance import _session_for_request
    req = types.SimpleNamespace(state=types.SimpleNamespace(role="bernardo"))
    assert _session_for_request(req) == "Bernardo"


def test_session_none_for_admin_role():
    import types
    from app.routers.finance import _session_for_request
    req = types.SimpleNamespace(state=types.SimpleNamespace(role="admin"))
    assert _session_for_request(req) is None


def test_session_none_when_role_absent():
    import types
    from app.routers.finance import _session_for_request
    req = types.SimpleNamespace(state=types.SimpleNamespace())
    assert _session_for_request(req) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_endpoints.py -k session -v`
Expected: FAIL — `ImportError: cannot import name '_session_for_request'`

- [ ] **Step 3: Implementar helper + plugar nos endpoints**

Em `app/routers/finance.py`, trocar o import do FastAPI:
```python
from fastapi import APIRouter, HTTPException, Request
```
Adicionar o helper logo após a criação do `router`:
```python
def _session_for_request(request: Request) -> Optional[str]:
    """Força a sessão Bernardo pro role bernardo (segrega no backend, não via
    query do cliente). Demais roles: None = vê tudo."""
    return "Bernardo" if getattr(request.state, "role", None) == "bernardo" else None
```

Editar os 4 endpoints pra receber `request: Request` e repassar `session`:

```python
@router.get("/receivables")
async def get_receivables(
    request: Request,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        return build_receivables_by_client(
            since=since, until=until, session=_session_for_request(request))
    except Exception:
        logger.exception("Erro ao agregar receivables")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.get("/aging-summary")
async def get_aging_summary(
    request: Request,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return build_aging_summary(
            since=since, until=until, session=_session_for_request(request))
    except Exception:
        logger.exception("Erro ao construir aging summary")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.get("/paid")
async def get_paid(
    request: Request,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        return build_paid_by_client(
            since=since, until=until, session=_session_for_request(request))
    except Exception:
        logger.exception("Erro ao agregar pagos")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.get("/paid-summary")
async def get_paid_summary(
    request: Request,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return build_paid_summary(
            since=since, until=until, session=_session_for_request(request))
    except Exception:
        logger.exception("Erro ao construir paid summary")
        return JSONResponse(status_code=500, content={"error": "internal"})
```

- [ ] **Step 4: Rodar e ver passar (com regressão)**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_finance_endpoints.py tests/unit/test_finance_router.py -v`
Expected: PASS (helper novo + endpoints existentes verdes — admin/test recebem `session=None`, comportamento intocado)

- [ ] **Step 5: Commit**

```bash
git add app/routers/finance.py tests/unit/test_finance_endpoints.py
git commit -m "feat(finance): endpoints forçam sessão Bernardo pelo role logado"
```

---

### Task 4: Frontend — badge, sessão operacional por role, esconder Créditos

**Files:**
- Modify: `static/js/dashboard_v2.js` (credits-hide, opSession, RAIL_GROUPS, badge x2)
- Modify: `templates/dashboard_v2.html` (CSS `.badge-bernardo` + cache-bust)

**Interfaces:**
- Consumes: `currentRole` (global, já resolvido pós-`/api/me`); `p.session === "Bernardo"` (já vem do backend); `itemsFor(session, state)` já trata `"bernardo"`/`"all"`.

- [ ] **Step 1: Esconder Créditos pro bernardo**

Em `static/js/dashboard_v2.js`, logo após o bloco que esconde `clientes-group`
(após a linha `if (!visibleGroups.has("clientes")) { ... }`), adicionar:

```javascript
    // Bernardo vê Financeiro, mas Créditos é saldo por cliente (não segrega por
    // enquete) — esconde a view.
    if (currentRole === "bernardo") {
        document.querySelector('#fin-group .rail-step[data-fin-view="credits"]')
            ?.style.setProperty("display", "none");
    }
```

- [ ] **Step 2: Sessão de Estoque/Logística dependente do role**

Em `static/js/dashboard_v2.js`, na declaração do `const RAIL_GROUPS = [`,
adicionar a linha do `opSession` imediatamente antes e trocar o `session: "all"`
de Estoque e Logística por `session: opSession`:

```javascript
    // Pro role bernardo, Estoque/Logística só mostram pacotes Bernardo.
    const opSession = currentRole === "bernardo" ? "bernardo" : "all";
    const RAIL_GROUPS = [
        { id: "comercial", label: "Comercial", session: "comercial",
          states: ["aberto", "fechado", "confirmado", "pago"], extras: ["cancelled"] },
        { id: "bernardo", label: "Bernardo", session: "bernardo",
          states: ["aberto", "fechado", "confirmado", "pago"], extras: ["cancelled"] },
        { id: "estoque", label: "Estoque", session: opSession,
          states: ["pago", "pendente", "separado"], labels: { pago: "Fila de separação" } },
        { id: "logistica", label: "Logística", session: opSession,
          states: ["separado", "enviado"] },
    ];
```

- [ ] **Step 3: Badge "Bernardo" no card da lista**

Em `static/js/dashboard_v2.js`, na renderização da lista (`renderList`), trocar a
linha do nome:
```javascript
                    <div class="name">${L.escapeHtml(meta.item)}</div>
```
por:
```javascript
                    <div class="name">${L.escapeHtml(meta.item)}${p.session === "Bernardo" ? ` <span class="badge-bernardo">Bernardo</span>` : ""}</div>
```

- [ ] **Step 4: Badge "Bernardo" no detalhe**

Em `static/js/dashboard_v2.js`, na renderização do detalhe (`renderDetail`), trocar a
linha do subtitle:
```javascript
                <div class="subtitle">${L.pill(state)} · ${L.escapeHtml(p.external_poll_id || "")}</div>
```
por:
```javascript
                <div class="subtitle">${L.pill(state)} · ${L.escapeHtml(p.external_poll_id || "")}${p.session === "Bernardo" ? ` <span class="badge-bernardo">Bernardo</span>` : ""}</div>
```

- [ ] **Step 5: CSS do badge**

Em `templates/dashboard_v2.html`, logo após a regra `.unit-tag, .row-unit { ... }`
(perto da linha 299), adicionar:

```css
    .badge-bernardo {
        display: inline-block; padding: 1px 8px; border-radius: 999px;
        font-size: 10px; font-weight: 700; letter-spacing: 0.03em;
        background: rgba(167,139,250,0.16); color: #a78bfa;
        border: 1px solid rgba(167,139,250,0.30); vertical-align: middle;
    }
```

- [ ] **Step 6: Cache-bust**

Em `templates/dashboard_v2.html` (linha ~1392), trocar:
```html
<script src="/static/js/dashboard_v2.js?v=20260625b"></script>
```
por:
```html
<script src="/static/js/dashboard_v2.js?v=20260625c"></script>
```

- [ ] **Step 7: Validar no browser (porta de dev 8023, NUNCA 8000)**

Subir o app local (SQLite + sandbox), abrir como admin e como bernardo,
conferir manualmente:
- Admin → Estoque/Logística com badge "Bernardo" nos pacotes Bernardo; Comercial sem badge.
- Bernardo → vê Bernardo/Estoque/Logística/Financeiro; Estoque/Logística só pacotes Bernardo; Financeiro sem a view Créditos.

(Validação detalhada e screenshots na fase de review.)

- [ ] **Step 8: Commit**

```bash
git add static/js/dashboard_v2.js templates/dashboard_v2.html
git commit -m "feat(bernardo): badge de flag + abas operacionais filtradas por role"
```

---

## Self-Review (preenchido)

- **Spec coverage:** badge (Task 4 §3-5) ✅; visible_groups (Task 2) ✅; filtro backend dos builders (Task 1) ✅; endpoints forçam por role (Task 3) ✅; esconder Créditos (Task 4 §1) ✅; sessão operacional por role (Task 4 §2) ✅; cache-bust (Task 4 §6) ✅; testes (Tasks 1-3) ✅.
- **Placeholders:** nenhum — todo passo tem código/comando.
- **Type consistency:** `session: str | None`, valor `"Bernardo"`, chave `name` consistentes entre Task 1 (builders), Task 3 (`_session_for_request` retorna `"Bernardo"`) e `session_for_title` (retorna `{"name": "Bernardo"}`). `itemsFor` filtra por `p.session === "Bernardo"` — string idêntica.
