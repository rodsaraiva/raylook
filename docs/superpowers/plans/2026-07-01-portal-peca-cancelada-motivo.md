# Peça paga cancelada visível no portal com motivo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer uma peça já paga que a admin cancela (gatilho do crédito) voltar a aparecer no portal da cliente com a tag "Cancelado" e o motivo digitado no cancelamento.

**Architecture:** O cancelamento grava `pacotes.cancel_reason` (nova coluna). O portal para de filtrar peças canceladas **que foram pagas** (`paid_at` presente) e passa o motivo pro template, que já tem o badge "Cancelado". A admin digita o motivo (obrigatório) num modal novo no dashboard.

**Tech Stack:** FastAPI + Jinja2, Postgres/PostgREST (prod) e SQLite (dev), JS vanilla no dashboard, pytest.

## Global Constraints

- Idioma pt-BR em código/copy/commits.
- **Sem `git push` sem aprovação do usuário.** Commits locais são OK.
- Migration roda em **prod só com aprovação**; raylook tem Postgres dedicado (não usa `alana_staging`). Validar local com SQLite.
- Escopo: só peça **paga → cancelada** aparece; cancelada-nunca-paga segue oculta.
- Motivo: texto livre, **obrigatório na UI**; backend aceita vazio (`None`) sem quebrar.
- Onde guardar: `pacotes.cancel_reason` (um por pacote).
- Integração > mock, especialmente DB.
- Rodar testes: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/ -v`.
- Branch atual: `feat/portal-peca-cancelada-motivo`.

---

### Task 1: Coluna `cancel_reason` (migration + schema canônico)

**Files:**
- Create: `deploy/postgres/migrations/F067_pacotes_cancel_reason.sql`
- Modify: `deploy/postgres/schema.sql` (bloco `CREATE TABLE ... pacotes`, após `cancelled_by text,`)
- Modify: `deploy/sqlite/schema.sql` (bloco `pacotes`, após `cancelled_by TEXT,`)

**Interfaces:**
- Produces: coluna `pacotes.cancel_reason` (text/TEXT, nullable) usada pelas Tasks 2 e 4.

- [ ] **Step 1: Criar a migration Postgres**

Criar `deploy/postgres/migrations/F067_pacotes_cancel_reason.sql`:

```sql
-- F067: motivo de cancelamento do pacote, exibido pra cliente no portal.
--
-- Quando a admin cancela um pacote com peças já pagas (gatilho do crédito),
-- a peça volta a aparecer no portal com a tag "Cancelado". cancel_reason
-- guarda a explicação (texto livre) digitada no momento do cancelamento.

BEGIN;

ALTER TABLE pacotes
    ADD COLUMN IF NOT EXISTS cancel_reason text;

COMMIT;
```

- [ ] **Step 2: Espelhar no schema canônico Postgres**

Em `deploy/postgres/schema.sql`, na tabela `pacotes`, trocar:

```sql
    cancelled_at timestamptz,
    cancelled_by text,
    created_via text NOT NULL DEFAULT 'poll',
```

por:

```sql
    cancelled_at timestamptz,
    cancelled_by text,
    cancel_reason text,
    created_via text NOT NULL DEFAULT 'poll',
```

- [ ] **Step 3: Espelhar no schema canônico SQLite**

Em `deploy/sqlite/schema.sql`, na tabela `pacotes`, trocar:

```sql
    cancelled_at TEXT,
    cancelled_by TEXT,
    created_via TEXT NOT NULL DEFAULT 'poll',
```

por:

```sql
    cancelled_at TEXT,
    cancelled_by TEXT,
    cancel_reason TEXT,
    created_via TEXT NOT NULL DEFAULT 'poll',
```

- [ ] **Step 4: Validar que o SQLite carrega**

Run: `sqlite3 ":memory:" ".read deploy/sqlite/schema.sql" "SELECT 1;"`
Expected: imprime `1` sem erro de sintaxe.

- [ ] **Step 5: Commit**

```bash
git add deploy/postgres/migrations/F067_pacotes_cancel_reason.sql deploy/postgres/schema.sql deploy/sqlite/schema.sql
git commit -m "feat(schema): coluna pacotes.cancel_reason (motivo do cancelamento)"
```

---

### Task 2: Serviço grava o motivo (`cancel_package(reason=...)`)

**Files:**
- Modify: `app/services/package_cancellation_service.py:144-258`
- Test: `tests/unit/test_package_cancellation_service.py`

**Interfaces:**
- Consumes: coluna `pacotes.cancel_reason` (Task 1).
- Produces: `cancel_package(package_id, force=False, cancelled_by=None, reason=None)` — grava `cancel_reason` no PATCH final de `pacotes`. Usada pela Task 3.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `tests/unit/test_package_cancellation_service.py`:

```python
def test_cancel_grava_motivo_no_pacote(monkeypatch):
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

    from app.services import credit_service
    monkeypatch.setattr(credit_service, "add_credit", lambda *a, **k: None)

    pcs.cancel_package("PKG-1", force=True, cancelled_by="admin",
                       reason="Fornecedor sem estoque")

    pkg_patch = [c for c in fake.patch_calls if "/pacotes?" in c["path"]]
    assert len(pkg_patch) == 1
    assert pkg_patch[0]["payload"]["cancel_reason"] == "Fornecedor sem estoque"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_package_cancellation_service.py::test_cancel_grava_motivo_no_pacote -v`
Expected: FAIL — `cancel_package() got an unexpected keyword argument 'reason'`.

- [ ] **Step 3: Adicionar o parâmetro `reason` na assinatura**

Em `app/services/package_cancellation_service.py`, trocar:

```python
def cancel_package(
    package_id: str,
    force: bool = False,
    cancelled_by: Optional[str] = None,
) -> Dict[str, Any]:
```

por:

```python
def cancel_package(
    package_id: str,
    force: bool = False,
    cancelled_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
```

- [ ] **Step 4: Gravar o motivo no PATCH final do pacote**

No mesmo arquivo, trocar o PATCH de `pacotes` (por volta da linha 248):

```python
    sb._request(
        "PATCH",
        f"/rest/v1/pacotes?id=eq.{package_id}",
        payload={
            "status": "cancelled",
            "cancelled_at": now,
            "cancelled_by": cancelled_by or "admin",
            "updated_at": now,
        },
        prefer="return=minimal",
    )
```

por:

```python
    sb._request(
        "PATCH",
        f"/rest/v1/pacotes?id=eq.{package_id}",
        payload={
            "status": "cancelled",
            "cancelled_at": now,
            "cancelled_by": cancelled_by or "admin",
            "cancel_reason": reason,
            "updated_at": now,
        },
        prefer="return=minimal",
    )
```

- [ ] **Step 5: Rodar o teste do serviço e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_package_cancellation_service.py -v`
Expected: PASS (todos, incluindo o novo).

- [ ] **Step 6: Commit**

```bash
git add app/services/package_cancellation_service.py tests/unit/test_package_cancellation_service.py
git commit -m "feat(cancel): cancel_package grava motivo em pacotes.cancel_reason"
```

---

### Task 3: Endpoint repassa o motivo do body

**Files:**
- Modify: `app/routers/dashboard.py:1056-1068`
- Test: `tests/unit/test_dashboard_cancel_credit.py`

**Interfaces:**
- Consumes: `cancel_package(..., reason=...)` (Task 2).
- Produces: `POST /api/dashboard/packages/{id}/cancel` aceita `cancel_reason` no JSON body.

- [ ] **Step 1: Atualizar os fakes existentes + escrever o teste novo**

Em `tests/unit/test_dashboard_cancel_credit.py`:

Trocar a assinatura do fake em `test_cancel_delegates_to_service_and_returns_credit`:

```python
    def fake_cancel(package_id, force=False, cancelled_by=None):
        called["args"] = (package_id, force, cancelled_by)
        return {"cancelled_sales": 2, "credited_clients": 1, "credited_total": 300.0}
```

por:

```python
    def fake_cancel(package_id, force=False, cancelled_by=None, reason=None):
        called["args"] = (package_id, force, cancelled_by)
        return {"cancelled_sales": 2, "credited_clients": 1, "credited_total": 300.0}
```

Trocar a assinatura do fake em `test_cancel_blocked_when_paid_clients`:

```python
    def fake_cancel(package_id, force=False, cancelled_by=None):
        raise PackageCancelBlocked([
            {"cliente_nome": "Ana", "total_amount": 150.0, "pagamento_id": "PG1"},
        ])
```

por:

```python
    def fake_cancel(package_id, force=False, cancelled_by=None, reason=None):
        raise PackageCancelBlocked([
            {"cliente_nome": "Ana", "total_amount": 150.0, "pagamento_id": "PG1"},
        ])
```

Adicionar o teste novo ao final do arquivo:

```python
def test_cancel_forwards_reason(monkeypatch):
    _silence_snapshots(monkeypatch)
    called = {}

    def fake_cancel(package_id, force=False, cancelled_by=None, reason=None):
        called["reason"] = reason
        called["force"] = force
        return {"cancelled_sales": 1, "credited_clients": 1, "credited_total": 120.0}

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post(
        "/api/dashboard/packages/PKG-9/cancel",
        json={"force": True, "cancel_reason": "  Fornecedor sem estoque  "},
    )

    assert resp.status_code == 200
    assert called["force"] is True
    assert called["reason"] == "Fornecedor sem estoque"  # trim aplicado
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_dashboard_cancel_credit.py::test_cancel_forwards_reason -v`
Expected: FAIL — `called["reason"]` é `None` (endpoint ainda não lê `cancel_reason`).

- [ ] **Step 3: Ler `cancel_reason` do body e repassar**

Em `app/routers/dashboard.py`, trocar:

```python
    force = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force") or False)
    except Exception:
        pass

    from app.services import package_cancellation_service as pcs
    try:
        result = await asyncio.to_thread(
            pcs.cancel_package, pacote_id, force=force, cancelled_by=role
        )
```

por:

```python
    force = False
    reason = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force") or False)
            reason = (str(body.get("cancel_reason") or "")).strip() or None
    except Exception:
        pass

    from app.services import package_cancellation_service as pcs
    try:
        result = await asyncio.to_thread(
            pcs.cancel_package, pacote_id, force=force, cancelled_by=role, reason=reason
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_dashboard_cancel_credit.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_dashboard_cancel_credit.py
git commit -m "feat(cancel): endpoint repassa cancel_reason pro serviço"
```

---

### Task 4: Portal exibe a peça paga cancelada com motivo

**Files:**
- Modify: `app/services/portal_service.py:388-494`
- Test: `tests/unit/test_portal_service.py` (2 testes já escritos: `test_peca_paga_e_cancelada_aparece_com_motivo`, `test_peca_paga_cancelada_sem_motivo_ainda_aparece`)

**Interfaces:**
- Consumes: `pacotes.cancel_reason` (Task 1).
- Produces: cada order de `get_client_orders` ganha a chave `cancel_reason: str` (`""` quando não há). Usada pela Task 5.

- [ ] **Step 1: Confirmar que os testes já existentes falham**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_portal_service.py -k "paga" -v`
Expected: 2 FAIL (`assert 0 == 1`) — a peça cancelada ainda é filtrada.

- [ ] **Step 2: Incluir `cancel_reason` no embed do pacote**

Em `app/services/portal_service.py::get_client_orders`, trocar a linha do embed:

```python
            "pacote:pacote_id(id,friendly_id,status,shipped_at,pdf_sent_at,pending_reasons,pending_observations,"
```

por:

```python
            "pacote:pacote_id(id,friendly_id,status,shipped_at,pdf_sent_at,pending_reasons,pending_observations,cancel_reason,"
```

- [ ] **Step 3: Reestruturar os filtros do loop (deixar passar paga→cancelada)**

Trocar o trecho:

```python
    for venda in vendas:
        if venda.get("status") == "cancelled":
            continue
        produto = venda.get("produto") or {}
        pacote = venda.get("pacote") or {}
        enquete = pacote.get("enquete") or {}
        pagamento = pag_by_venda.get(str(venda["id"]), {})
        # Se a cobrança foi excluída (venda existe mas pagamento não), não mostrar
        if not pagamento:
            continue
        # Pacote ainda formando (aberto/fechado) ou já cancelado não aparece
        # no portal — só faz sentido após approve.
        pkg_status = (pacote.get("status") or "").lower()
        if pkg_status in ("open", "closed", "cancelled"):
            continue

        pag_status = str(pagamento.get("status") or venda.get("status") or "pending").lower()
        if pag_status in CANCELLED_STATUSES:
            continue
        pc_row = pc_by_id.get(str(venda.get("pacote_cliente_id") or ""))
        delivery = _delivery_status(pag_status, pacote, pc_row)
```

por:

```python
    for venda in vendas:
        produto = venda.get("produto") or {}
        pacote = venda.get("pacote") or {}
        enquete = pacote.get("enquete") or {}
        pagamento = pag_by_venda.get(str(venda["id"]), {})
        # Se a cobrança foi excluída (venda existe mas pagamento não), não mostrar
        if not pagamento:
            continue

        pkg_status = (pacote.get("status") or "").lower()
        pag_status = str(pagamento.get("status") or venda.get("status") or "pending").lower()
        venda_status = str(venda.get("status") or "").lower()
        was_paid = bool(pagamento.get("paid_at"))
        is_cancelled = (
            pag_status in CANCELLED_STATUSES
            or venda_status == "cancelled"
            or pkg_status == "cancelled"
        )

        if is_cancelled:
            # Só peça que foi paga (virou crédito) reaparece com tag "Cancelado"
            # + motivo. Cancelada que a cliente nunca pagou continua oculta.
            if not was_paid:
                continue
            pag_status = "cancelled"
        elif pkg_status in ("open", "closed"):
            # Pacote ainda formando não aparece — só após approve.
            continue

        pc_row = pc_by_id.get(str(venda.get("pacote_cliente_id") or ""))
        delivery = _delivery_status(pag_status, pacote, pc_row)
```

- [ ] **Step 4: Adicionar `cancel_reason` ao dict do pedido**

No `orders.append({...})`, logo após a linha `"pending_observations": delivery["observations"],`, inserir:

```python
            "cancel_reason": pacote.get("cancel_reason") or "",
```

- [ ] **Step 5: Rodar os testes do portal e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/test_portal_service.py -v`
Expected: PASS — inclusive os 2 novos e os antigos (`test_venda_cancelada_excluida`, `test_pacote_open_closed_cancelled_sao_omitidos`, `test_pagamento_cancelado_e_omitido` continuam verdes porque usam `paid_at=None`).

- [ ] **Step 6: Commit**

```bash
git add app/services/portal_service.py tests/unit/test_portal_service.py
git commit -m "feat(portal): peça paga cancelada reaparece com motivo (não filtra mais)"
```

---

### Task 5: Template mostra o motivo sob o badge "Cancelado"

**Files:**
- Modify: `templates/portal_pedidos.html:128-137`

**Interfaces:**
- Consumes: `order.cancel_reason` (Task 4) e `ds == 'cancelled'`.

- [ ] **Step 1: Adicionar o bloco do motivo**

Em `templates/portal_pedidos.html`, logo após o bloco de `pendente_logistica` (o `{% endif %}` da linha 137), inserir:

```html
                    {% if ds == 'cancelled' and order.cancel_reason %}
                    <div class="order-pending-reasons">
                        <div class="reason-obs">Motivo: {{ order.cancel_reason }}</div>
                    </div>
                    {% endif %}
```

(Reaproveita as classes `.order-pending-reasons` e `.reason-obs` já existentes — sem CSS novo.)

- [ ] **Step 2: Commit**

```bash
git add templates/portal_pedidos.html
git commit -m "feat(portal): exibe motivo do cancelamento sob a tag Cancelado"
```

---

### Task 6: Dashboard captura o motivo (modal obrigatório) nos 2 pontos de cancelamento

**Files:**
- Modify: `templates/dashboard_v2.html:1351` (adicionar modal após `pending-reasons-modal`)
- Modify: `static/js/dashboard_v2.js` (nova `promptCancelReason`, expor global, ligar no `[data-cancel]`)
- Modify: `static/dashboard/lib.js:192` (force POST preserva o body)
- Modify: `static/dashboard/modal.js:255-281` (drill-down usa o motivo)

**Interfaces:**
- Produces: `window.RaylookCancelReason(): Promise<{cancel_reason: string} | null>`.
- Envia `{cancel_reason}` (e `{...body, force:true}` no 409) pro endpoint da Task 3.

- [ ] **Step 1: Adicionar o modal HTML**

Em `templates/dashboard_v2.html`, logo após o `</div>` que fecha `pending-reasons-modal` (linha 1351), inserir:

```html
<!-- Modal de motivo pra cancelar pacote (obrigatório, aparece pra cliente) -->
<div id="cancel-reason-overlay" class="admin-pwd-overlay"></div>
<div id="cancel-reason-modal" class="admin-pwd-modal pending-modal" role="dialog" aria-labelledby="cancel-reason-title">
    <h3 id="cancel-reason-title">Cancelar pacote</h3>
    <p class="admin-pwd-help">Explique o motivo do cancelamento. Esse texto aparece pra cliente no portal.</p>

    <div class="pending-obs-wrap">
        <label for="cancel-reason-text" class="pending-label">Motivo <span class="pending-required">*</span></label>
        <textarea id="cancel-reason-text" class="pending-textarea" placeholder="Ex.: Fornecedor cancelou o pedido, sem estoque." rows="3"></textarea>
    </div>

    <div id="cancel-reason-error" class="admin-pwd-error"></div>
    <div class="admin-pwd-actions">
        <button type="button" class="btn-ghost" id="cancel-reason-cancel">Voltar</button>
        <button type="button" class="btn-primary" id="cancel-reason-ok">Cancelar pacote</button>
    </div>
</div>
```

- [ ] **Step 2: Adicionar `promptCancelReason()` no dashboard_v2.js**

Em `static/js/dashboard_v2.js`, logo após a função `promptPendingReasons()` (após a linha 111), inserir:

```javascript
    // Modal de motivo pra cancelar pacote (texto livre obrigatório). Retorna
    // { cancel_reason } ou null se cancelado. O texto aparece pra cliente.
    function promptCancelReason() {
        return new Promise((resolve) => {
            const ov = document.getElementById("cancel-reason-overlay");
            const md = document.getElementById("cancel-reason-modal");
            const ta = document.getElementById("cancel-reason-text");
            const ok = document.getElementById("cancel-reason-ok");
            const cancel = document.getElementById("cancel-reason-cancel");
            const err = document.getElementById("cancel-reason-error");
            if (!ov || !md || !ta || !ok) { resolve(null); return; }

            ta.value = "";
            err.textContent = "";
            ov.classList.add("open");
            md.classList.add("open");
            setTimeout(() => ta.focus(), 30);

            function cleanup() {
                ov.classList.remove("open");
                md.classList.remove("open");
                ok.removeEventListener("click", onOk);
                cancel.removeEventListener("click", onCancel);
                ov.removeEventListener("click", onCancel);
            }
            function onOk() {
                const reason = ta.value.trim();
                if (!reason) { err.textContent = "Preencha o motivo do cancelamento."; return; }
                cleanup();
                resolve({ cancel_reason: reason });
            }
            function onCancel() { cleanup(); resolve(null); }
            ok.addEventListener("click", onOk);
            cancel.addEventListener("click", onCancel);
            ov.addEventListener("click", onCancel);
        });
    }
    window.RaylookCancelReason = promptCancelReason;
```

- [ ] **Step 3: Ligar o motivo no cancel do painel de detalhe**

Em `static/js/dashboard_v2.js`, trocar o handler do `[data-cancel]` (linha ~875):

```javascript
        detail.querySelector("[data-cancel]")?.addEventListener("click", async () => {
            await L.doAction(p.id, "cancel", { confirmText: "Cancelar esse pacote?", okLabel: "Cancelar pacote", danger: true });
        });
```

por:

```javascript
        detail.querySelector("[data-cancel]")?.addEventListener("click", async () => {
            const r = await promptCancelReason();
            if (!r) return;
            await L.doAction(p.id, "cancel", { body: { cancel_reason: r.cancel_reason }, okLabel: "Cancelar pacote", danger: true });
        });
```

- [ ] **Step 4: Preservar o body no force POST do lib.js**

Em `static/dashboard/lib.js`, no handler de 409, trocar:

```javascript
                const resp2 = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ force: true }),
                });
```

por:

```javascript
                const resp2 = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ...(body || {}), force: true }),
                });
```

(`body` já vem do destructuring `const { ..., body, ... } = opts;` no topo do `doAction`.)

- [ ] **Step 5: Ligar o motivo no cancel do drill-down (modal.js)**

Em `static/dashboard/modal.js`, dentro do handler `[data-action]`, trocar:

```javascript
                if (action === "cancel" && !await confirmModal("Cancelar esse pacote?", { okLabel: "Cancelar pacote", danger: true })) return;
                btn.disabled = true;
                const old = btn.textContent;
                btn.textContent = "…";
                try {
                    const url = `/api/dashboard/packages/${pacoteId}/${action}`;
                    let resp = await fetch(url, { method: "POST", credentials: "include" });
```

por:

```javascript
                let cancelReason = null;
                if (action === "cancel") {
                    const r = window.RaylookCancelReason ? await window.RaylookCancelReason() : null;
                    if (!r) return;
                    cancelReason = r.cancel_reason;
                }
                btn.disabled = true;
                const old = btn.textContent;
                btn.textContent = "…";
                try {
                    const url = `/api/dashboard/packages/${pacoteId}/${action}`;
                    const initBody = cancelReason ? { cancel_reason: cancelReason } : undefined;
                    let resp = await fetch(url, {
                        method: "POST",
                        credentials: "include",
                        ...(initBody ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(initBody) } : {}),
                    });
```

E, no force POST logo abaixo (dentro do `if (resp.status === 409 ...)`), trocar:

```javascript
                        resp = await fetch(url, {
                            method: "POST",
                            credentials: "include",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ force: true }),
                        });
```

por:

```javascript
                        resp = await fetch(url, {
                            method: "POST",
                            credentials: "include",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ cancel_reason: cancelReason, force: true }),
                        });
```

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard_v2.html static/js/dashboard_v2.js static/dashboard/lib.js static/dashboard/modal.js
git commit -m "feat(dashboard): modal de motivo obrigatório ao cancelar pacote"
```

---

### Task 7: Validação end-to-end (browser) + suíte completa

**Files:** nenhum (validação).

- [ ] **Step 1: Rodar a suíte unitária inteira**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/python -m pytest tests/unit/ -v`
Expected: tudo verde.

- [ ] **Step 2: Subir o app local (SQLite/sandbox)**

Run (background): `PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 main.py`
Expected: sobe em `127.0.0.1:8000` sem erro de schema (coluna `cancel_reason` existe no SQLite recriado).

- [ ] **Step 3: Fluxo no dashboard (Playwright MCP)**

Abrir o dashboard, achar um pacote com pagamento pago, clicar "Cancelar pacote":
- Verificar que o modal de motivo aparece e **bloqueia** com textarea vazia ("Preencha o motivo…").
- Preencher o motivo, confirmar; no aviso de clientes pagos (409), confirmar "Cancelar mesmo assim".
- Verificar toast de sucesso.

- [ ] **Step 4: Fluxo no portal da cliente**

Abrir `/portal/preview/{cliente_id}` (ou logar como a cliente) e confirmar que a peça cancelada aparece com o badge "Cancelado" e a linha "Motivo: …" com o texto digitado.

- [ ] **Step 5: Checar regressão visual**

Confirmar que peças pendentes/pagas continuam renderizando normalmente (nada sumiu nem duplicou).

- [ ] **Step 6: Parar o app local**

Run: `pkill -f "python3 main.py"`

---

## Notas de deploy (pós-aprovação)

1. **Migration em prod ANTES do código** (só com OK do usuário): rodar `F067_pacotes_cancel_reason.sql` no Postgres do raylook (`raylook_postgres`). `ADD COLUMN IF NOT EXISTS` é idempotente e não bloqueia. A migration já inclui `NOTIFY pgrst, 'reload schema';` — recarrega o schema-cache do PostgREST no COMMIT.
2. **Ordem obrigatória (não opcional):** migration + reload do PostgREST **antes** do deploy do código. O portal (`get_client_orders`) passa a pedir `cancel_reason` no embed; se o código subir antes do PostgREST enxergar a coluna, `select_all` recebe 4xx e a página de pedidos do cliente quebra pra todos. Como merge em `main` dispara o deploy via CI, rodar a migration primeiro (ou reiniciar `raylook_postgrest` se o NOTIFY não pegar).
3. Deploy do código via GitHub Actions (push em `main`) — **só após aprovação do push** e depois da migration.
