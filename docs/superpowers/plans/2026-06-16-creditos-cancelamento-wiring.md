# Créditos no Cancelamento — Religar Geração + Fechar Gap do Débito — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o cancelamento de pacote do dashboard ativo gerar crédito (hoje não gera) e garantir que o crédito aplicado seja abatido do saldo em todos os caminhos que marcam pagamento como pago.

**Architecture:** Bug 1 — a UI ativa (`dashboard_v2`) cancela via `POST /api/dashboard/packages/{id}/cancel` (`dashboard.py:1035`), um flip de status que **não** chama `package_cancellation_service.cancel_package`. Religamos esse endpoint ao serviço (que já gera crédito e cancela vendas/pagamentos em cascata), espelhando o handler `main.py:2213`, e ensinamos o frontend a tratar o 409 `blocked_paid` (clientes que já pagaram → vira crédito) com força explícita. Bug 2 — `confirm_debit` (abate o crédito do saldo) só roda no polling Asaas; adicionamos a chamada nos caminhos admin que marcam pago (`advance_package` confirmado→pago e `mark client paid`). `confirm_debit` é idempotente (só toca débito `pending`), então é seguro chamar sempre.

**Tech Stack:** FastAPI + Jinja2, PostgREST/Postgres, JS vanilla (`static/dashboard/lib.js`, `static/dashboard/modal.js`, `static/js/dashboard_v2.js`), pytest.

---

## Contexto / root cause (já investigado, read-only)

- Tabela `creditos` **vazia** em prod, apesar de 3 pacotes pagos cancelados após o deploy da feature (merge PR#3, 01/06): PAC020/0206, PAC018/1305, PAC004/1506.
- Causa: dois endpoints de cancelamento divergiram. Só `main.py:2213` (`/api/packages/{id}/cancel`, chamado pelo dashboard **legado** `static/js/dashboard.js`) gera crédito. O dashboard **ativo** (`dashboard_v2.html` → `lib.js` `doAction("cancel")` → `dashboard.py:1035`) faz só `status='cancelled'`.
- Gap secundário: `confirm_debit` só é chamado em `asaas_sync_service.py:112` e `:219`. Os caminhos admin `dashboard.py:912` (advance confirmado→pago) e `dashboard.py:1286` (mark client paid) não confirmam o débito pending do crédito.

## File Structure

- `app/routers/dashboard.py` — **Modify**: endpoint `cancel_package` (linha 1035) passa a delegar ao serviço; `advance_package` (confirmado→pago, ~912) e `mark client paid` (~1286) ganham `confirm_debit`; novos imports no topo.
- `tests/unit/test_dashboard_cancel_credit.py` — **Create**: testes de delegação do cancel, do 409 bloqueado e do `confirm_debit` no advance/mark-paid.
- `static/dashboard/lib.js` — **Modify**: `doAction` trata 409 `blocked_paid` → 2ª confirmação → retry com `force:true`.
- `static/dashboard/modal.js` — **Modify**: botão "Cancelar pacote" do modal trata o mesmo 409.

## Out of scope

- **Backfill** dos 3 cancelamentos antigos: decidido "só daqui pra frente". Tratados manualmente fora deste plano.
- Dashboard legado (`static/js/dashboard.js` / `index.html`) já chama a rota correta — sem mudança.

## Constraint conhecida

`package_cancellation_service.cancel_package` exige `supabase_domain_enabled()` (backend Postgres). Em dev local (SQLite) o cancelamento pelo dashboard passará a levantar `RuntimeError` em vez do flip silencioso — comportamento aceitável (a feature é prod-only e os testes mockam o serviço). Validação real é em prod/staging Postgres.

---

### Task 1: Religar o endpoint de cancelamento do dashboard ao serviço de crédito

**Files:**
- Modify: `app/routers/dashboard.py` (imports no topo + função `cancel_package`, ~1035-1052)
- Test: `tests/unit/test_dashboard_cancel_credit.py`

- [ ] **Step 1: Adicionar imports necessários no topo de `dashboard.py`**

Logo após a linha `from app.services import auth_service as _auth` (linha 37), adicione:

```python
import asyncio

from fastapi.responses import JSONResponse
from app.services import credit_service
```

(`asyncio` e `JSONResponse` são usados no novo `cancel_package`; `credit_service` é usado na Task 2.)

- [ ] **Step 2: Escrever o teste que falha — delegação ao serviço**

Crie `tests/unit/test_dashboard_cancel_credit.py`:

```python
"""Testes do wiring de crédito no cancelamento/avanço do dashboard ativo.

Bug 1: POST /api/dashboard/packages/{id}/cancel deve delegar a
package_cancellation_service.cancel_package (que gera crédito), não fazer
um flip de status. Bug 2: confirm_debit deve rodar nos caminhos admin que
marcam pagamento como pago.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import dashboard as dashboard_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dashboard_module.router)
    return app


def _silence_snapshots(monkeypatch):
    """Os refresh de snapshot são lazy-imports dentro do endpoint — no-op."""
    import app.services.finance_service as fs
    import app.services.customer_service as cs
    monkeypatch.setattr(fs, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fs, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cs, "refresh_customer_rows_snapshot", lambda: None)


def test_cancel_delegates_to_service_and_returns_credit(monkeypatch):
    _silence_snapshots(monkeypatch)
    called = {}

    def fake_cancel(package_id, force=False, cancelled_by=None):
        called["args"] = (package_id, force, cancelled_by)
        return {"cancelled_sales": 2, "credited_clients": 1, "credited_total": 300.0}

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post("/api/dashboard/packages/PKG-1/cancel", json={})

    assert resp.status_code == 200
    assert called["args"] == ("PKG-1", False, "admin")
    body = resp.json()
    assert body["new_state"] == "cancelled"
    assert body["credited_clients"] == 1
    assert body["credited_total"] == 300.0
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py::test_cancel_delegates_to_service_and_returns_credit -v`
Expected: FAIL — o endpoint atual retorna `{"status":"ok","new_state":"cancelled"}` sem `credited_clients` e nunca chama `fake_cancel` (`called` fica vazio → KeyError/assert).

- [ ] **Step 4: Reescrever `cancel_package` para delegar ao serviço**

Substitua a função inteira (linhas ~1035-1052) por:

```python
@router.post("/packages/{pacote_id}/cancel")
async def cancel_package(pacote_id: str, request: Request) -> Dict[str, Any]:
    """Cancela o pacote em cascata gerando crédito pros que já pagaram.

    Sem `force` e havendo pagamentos pagos: retorna 409 `blocked_paid` com a
    lista de clientes pagos pra UI confirmar. Com `force=true`: cancela tudo e
    o valor pago de cada cliente vira crédito na plataforma.
    """
    role = _role_from(request)
    if not _auth.can_cancel(role):
        raise HTTPException(403, "Apenas o administrador pode cancelar pacotes.")

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
    except pcs.PackageNotFound:
        raise HTTPException(404, "Pacote não encontrado")
    except pcs.PackageCancelBlocked as exc:
        return JSONResponse(
            status_code=409,
            content={
                "status": "blocked_paid",
                "paid_count": len(exc.paid_info),
                "paid_clients": exc.paid_info,
            },
        )

    try:
        from app.services.finance_service import (
            refresh_charge_snapshot, refresh_dashboard_stats,
        )
        from app.services.customer_service import refresh_customer_rows_snapshot
        await asyncio.to_thread(refresh_charge_snapshot)
        await asyncio.to_thread(refresh_dashboard_stats)
        await asyncio.to_thread(refresh_customer_rows_snapshot)
    except Exception:
        logger.warning("cancel_package: refresh de snapshots falhou", exc_info=True)

    return {"status": "ok", "new_state": "cancelled", **result}
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py::test_cancel_delegates_to_service_and_returns_credit -v`
Expected: PASS

- [ ] **Step 6: Escrever o teste do caminho bloqueado (clientes pagos, sem force)**

Adicione ao mesmo arquivo de teste:

```python
def test_cancel_blocked_when_paid_clients(monkeypatch):
    _silence_snapshots(monkeypatch)
    from app.services.package_cancellation_service import PackageCancelBlocked

    def fake_cancel(package_id, force=False, cancelled_by=None):
        raise PackageCancelBlocked([
            {"cliente_nome": "Ana", "total_amount": 150.0, "pagamento_id": "PG1"},
        ])

    monkeypatch.setattr(
        "app.services.package_cancellation_service.cancel_package", fake_cancel
    )

    client = TestClient(_make_app())
    resp = client.post("/api/dashboard/packages/PKG-1/cancel", json={})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "blocked_paid"
    assert body["paid_count"] == 1
    assert body["paid_clients"][0]["cliente_nome"] == "Ana"
```

- [ ] **Step 7: Rodar o teste e confirmar que passa**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py -v`
Expected: PASS (os dois testes)

- [ ] **Step 8: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_dashboard_cancel_credit.py
git commit -m "fix(creditos): cancelamento do dashboard ativo gera crédito (delega ao package_cancellation_service)"
```

---

### Task 2: Confirmar o débito do crédito nos caminhos admin de marcar pago

**Files:**
- Modify: `app/routers/dashboard.py` (`advance_package` confirmado→pago, ~912-916; `mark client paid`, ~1286-1288)
- Test: `tests/unit/test_dashboard_cancel_credit.py` (mesmo arquivo)

- [ ] **Step 1: Escrever o teste que falha — advance confirmado→pago confirma o débito**

Adicione ao arquivo de teste:

```python
def test_advance_confirmado_confirms_credit_debit(monkeypatch):
    confirmed = []
    monkeypatch.setattr(
        dashboard_module.credit_service,
        "confirm_debit",
        lambda **kw: confirmed.append(kw.get("pagamento_id")),
    )

    fake = MagicMock()
    pkg = {"id": "PKG-1", "status": "approved"}
    vendas = [{"id": "V1"}, {"id": "V2"}]
    pags = [{"id": "PG1", "status": "sent"}, {"id": "PG2", "status": "sent"}]

    def fake_select(table, **kwargs):
        if table == "pacotes":
            return pkg
        if table == "vendas":
            return vendas
        if table == "pagamentos":
            return pags
        if table == "pacote_clientes":
            return []
        return None

    fake.select.side_effect = fake_select
    fake.now_iso.return_value = "2026-06-16T00:00:00Z"

    monkeypatch.setattr(
        dashboard_module.SupabaseRestClient, "from_settings", staticmethod(lambda: fake)
    )
    monkeypatch.setattr(dashboard_module, "_derive_state", lambda *a, **k: "confirmado")

    req = SimpleNamespace(state=SimpleNamespace(role="admin"))
    asyncio.run(dashboard_module.advance_package("PKG-1", req, to=None))

    assert confirmed == ["PG1", "PG2"]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py::test_advance_confirmado_confirms_credit_debit -v`
Expected: FAIL — `confirmed` fica `[]` porque o branch confirmado→pago hoje não chama `confirm_debit`.

- [ ] **Step 3: Adicionar `confirm_debit` no branch confirmado→pago**

Em `advance_package`, no branch `if state == "confirmado":` (linhas ~912-916), dentro do loop, após o `client.update(... "paid" ...)`, adicione a confirmação:

```python
    if state == "confirmado":
        # Marca TODOS os pagamentos como pagos → vira "pago" (aguardando gerente
        # validar antes de liberar pra estoque).
        for p in pags:
            if (p.get("status") or "") != "paid":
                client.update("pagamentos",
                              {"status": "paid", "paid_at": now},
                              filters=[("id", "eq", p["id"])])
                # Confirma o débito do crédito aplicado (se houver) — fora do
                # polling Asaas esse é o único ponto que abate do saldo.
                credit_service.confirm_debit(pagamento_id=p["id"])
        return {"status": "ok", "previous": "confirmado", "new_state": "pago"}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py::test_advance_confirmado_confirms_credit_debit -v`
Expected: PASS

- [ ] **Step 5: Escrever o teste que falha — mark client paid confirma o débito**

Adicione ao arquivo de teste:

```python
def test_mark_client_paid_confirms_credit_debit(monkeypatch):
    confirmed = []
    monkeypatch.setattr(
        dashboard_module.credit_service,
        "confirm_debit",
        lambda **kw: confirmed.append(kw.get("pagamento_id")),
    )

    fake = MagicMock()

    def fake_select(table, **kwargs):
        if table == "pacote_clientes":
            return {"id": "PC1"}
        if table == "vendas":
            return {"id": "V1"}
        if table == "pagamentos":
            return {"id": "PG9", "status": "sent"}
        return None

    fake.select.side_effect = fake_select
    fake.now_iso.return_value = "2026-06-16T00:00:00Z"
    monkeypatch.setattr(
        dashboard_module.SupabaseRestClient, "from_settings", staticmethod(lambda: fake)
    )

    req = SimpleNamespace(state=SimpleNamespace(role="admin"))
    asyncio.run(
        dashboard_module.mark_client_paid("PKG-1", "CLI-1", req)
    )

    assert confirmed == ["PG9"]
```

> Nota: ajuste o nome da função (`mark_client_paid`) e a assinatura no `asyncio.run(...)` para baterem com a definição real do endpoint em `dashboard.py:~1255` (route `POST /packages/{pacote_id}/clients/{cliente_id}/mark-paid` ou equivalente). Confira o `def` antes de rodar.

- [ ] **Step 6: Rodar o teste e confirmar que falha**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py::test_mark_client_paid_confirms_credit_debit -v`
Expected: FAIL — `confirmed` fica `[]`.

- [ ] **Step 7: Adicionar `confirm_debit` no endpoint mark client paid**

Em `dashboard.py:~1286`, após o `client.update("pagamentos", {"status":"paid","paid_at":now}, filters=[("id","eq",pag["id"])])`, adicione antes do `return`:

```python
    credit_service.confirm_debit(pagamento_id=pag["id"])
    return {"status": "ok", "action": "client_marked_paid", "cliente_id": cliente_id}
```

- [ ] **Step 8: Rodar o teste e confirmar que passa**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_dashboard_cancel_credit.py -v`
Expected: PASS (todos os testes do arquivo)

- [ ] **Step 9: Rodar a suíte de crédito completa pra garantir que nada quebrou**

Run: `DASHBOARD_AUTH_DISABLED=true .venv/bin/pytest tests/unit/test_credit_service.py tests/unit/test_portal_credit.py tests/unit/test_package_cancellation_service.py tests/unit/test_dashboard_cancel_credit.py -v`
Expected: PASS (tudo)

- [ ] **Step 10: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_dashboard_cancel_credit.py
git commit -m "fix(creditos): confirma débito do crédito ao marcar pago fora do polling Asaas (advance + mark client paid)"
```

---

### Task 3: Frontend `lib.js` — tratar 409 blocked_paid no cancel com confirmação forçada

**Files:**
- Modify: `static/dashboard/lib.js` (`doAction`, linhas ~153-194)

> Sem harness JS no raylook (testes são pytest) → validação é no browser (Task 5).

- [ ] **Step 1: Interceptar o 409 antes do throw e refazer com force**

Em `doAction`, troque o bloco que checa `if (!resp.ok)` (linhas ~173-176) para, no caso de cancelamento bloqueado, pedir 2ª confirmação e refazer com `force:true`:

```javascript
            const resp = await fetch(url, init);
            if (resp.status === 409 && action === "cancel") {
                const info = await resp.json().catch(() => ({}));
                const lista = (info.paid_clients || [])
                    .map(c => `  • ${c.cliente_nome || "cliente"} — R$ ${Number(c.total_amount || 0).toFixed(2)}`)
                    .join("\n");
                const aviso =
                    `⚠️ ${info.paid_count} cliente(s) já pagaram este pacote:\n\n${lista}\n\n` +
                    `Se cancelar assim mesmo:\n` +
                    `  • O valor pago de cada um vira CRÉDITO na plataforma (abatido nas próximas compras).\n` +
                    `  • Não há estorno em dinheiro.\n  • Todos os pedidos (pagos e pendentes) serão cancelados.\n\n` +
                    `Continuar?`;
                const ask2 = window.RaylookModal?.confirm
                    ? window.RaylookModal.confirm(aviso, { danger: true, okLabel: "Cancelar mesmo assim" })
                    : Promise.resolve(window.confirm(aviso));
                if (!await ask2) return false;
                const resp2 = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ force: true }),
                });
                if (!resp2.ok) {
                    const e2 = await resp2.json().catch(() => ({ detail: `HTTP ${resp2.status}` }));
                    throw new Error(e2.detail || "Falha");
                }
                const payload2 = await resp2.json();
                if (window.RaylookModal) {
                    window.RaylookModal.toast(successText || msgForAction(action, payload2), "success");
                }
                if (window.RaylookReload) await window.RaylookReload();
                return payload2;
            }
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                throw new Error(e.detail || "Falha");
            }
```

- [ ] **Step 2: Bump do cache-bust do `dashboard_v2.js`/assets se necessário**

Os `<script>` em `templates/dashboard_v2.html` usam `?v=...`. `lib.js` está com `?v=2` (linha 1389). Suba para `?v=3` pra forçar reload no browser:

Run: confira a linha e edite `templates/dashboard_v2.html:1389` de `lib.js?v=2` para `lib.js?v=3`.

- [ ] **Step 3: Commit**

```bash
git add static/dashboard/lib.js templates/dashboard_v2.html
git commit -m "feat(dashboard): cancel trata 409 blocked_paid com confirmação de crédito (force)"
```

---

### Task 4: Frontend `modal.js` — mesmo tratamento no botão "Cancelar pacote" do modal

**Files:**
- Modify: `static/dashboard/modal.js` (`wire`, bloco do `data-action="cancel"`, linhas ~255-270)

- [ ] **Step 1: Tratar o 409 no fetch do modal**

No handler de `[data-action]` (após o `confirmModal("Cancelar esse pacote?")`), substitua o `fetch` direto por uma versão que trata 409 igual ao `lib.js`:

```javascript
                if (action === "cancel" && !await confirmModal("Cancelar esse pacote?", { okLabel: "Cancelar pacote", danger: true })) return;
                btn.disabled = true;
                const old = btn.textContent;
                btn.textContent = "…";
                try {
                    const url = `/api/dashboard/packages/${pacoteId}/${action}`;
                    let resp = await fetch(url, { method: "POST", credentials: "include" });
                    if (resp.status === 409 && action === "cancel") {
                        const info = await resp.json().catch(() => ({}));
                        const lista = (info.paid_clients || [])
                            .map(c => `  • ${c.cliente_nome || "cliente"} — R$ ${Number(c.total_amount || 0).toFixed(2)}`)
                            .join("\n");
                        const aviso =
                            `⚠️ ${info.paid_count} cliente(s) já pagaram este pacote:\n\n${lista}\n\n` +
                            `O valor pago de cada um vira CRÉDITO na plataforma. Não há estorno em dinheiro.\n\nContinuar?`;
                        if (!await confirmModal(aviso, { okLabel: "Cancelar mesmo assim", danger: true })) {
                            btn.disabled = false; btn.textContent = old; return;
                        }
                        resp = await fetch(url, {
                            method: "POST",
                            credentials: "include",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ force: true }),
                        });
                    }
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                        throw new Error(err.detail || "Falha");
                    }
                    const payload = await resp.json();
                    toast(messageFor(action, payload), "success");
                    close();
```

> Confira o restante do bloco `try/catch` original (reload/`close()`) e preserve-o — só o trecho do fetch muda. Ajuste `?v=` do `modal.js` em `dashboard_v2.html:1390` (de `?v=2` para `?v=3`).

- [ ] **Step 2: Commit**

```bash
git add static/dashboard/modal.js templates/dashboard_v2.html
git commit -m "feat(dashboard): modal de cancelar pacote trata 409 blocked_paid (force)"
```

---

### Task 5: Verificação em staging/browser (a feature mexe em $ real)

**Files:** nenhum (verificação manual)

- [ ] **Step 1: Subir local com Postgres OU validar em staging**

O serviço de cancelamento exige Postgres (`supabase_domain_enabled`). Validar contra o stack `raylook_*` em staging (não prod). Não rodar `docker service update` fora do CI.

- [ ] **Step 2: Cenário A — cancelar pacote SEM pagamentos pagos**

No dashboard, cancelar um pacote sem pagos. Esperado: pacote vira `cancelled`, vendas/pagamentos cancelados, **nenhum** crédito gerado. Confirmar (read-only) que `creditos` não ganhou linha pra esse cliente.

- [ ] **Step 3: Cenário B — cancelar pacote COM cliente que já pagou**

Cancelar um pacote com 1 cliente pago. Esperado: modal de aviso "vira crédito" → confirmar → pacote cancelado e **1 lançamento `credit`** criado pro cliente (valor = total pago). Verificar saldo no portal do cliente (`/portal/pedidos` ou preview no dashboard).

- [ ] **Step 4: Cenário C — gastar o crédito e marcar pago pelo fluxo admin**

Com o cliente do Cenário B tendo saldo, gerar um novo pedido, aplicar o crédito no portal (PIX parcial ou cobertura total). Depois marcar o pacote como pago pelo **fluxo admin** (advance confirmado→pago), não pelo PIX. Esperado: o débito pending vira `confirmed` e o **saldo do cliente baixa** (não fica inflado). Esse é o gap que a Task 2 fecha.

- [ ] **Step 5: Query read-only de sanidade (precisa de aprovação pra prod; livre em staging)**

```sql
-- débitos pending cujo pagamento já está paid (deveria voltar VAZIO após o fix)
SELECT c.id, c.cliente_id, c.valor, p.status
FROM creditos c JOIN pagamentos p ON p.id = c.pagamento_id
WHERE c.tipo='debit' AND c.status='pending' AND p.status='paid';
```

- [ ] **Step 6: Ship — push (deploy via GitHub Actions)**

```bash
git push
```

Push em `main` dispara o CI (build + `docker stack deploy`). Acompanhar o deploy (40-90s) e repetir os Cenários A-C em prod com 1 caso real pequeno, se possível.

---

## Self-Review

**1. Spec coverage:**
- Bug 1 (geração não dispara) → Task 1 (backend) + Tasks 3-4 (frontend force flow). ✅
- Bug 2 (confirm_debit gap) → Task 2 (advance + mark client paid). ✅
- Verificação end-to-end (mexe em dinheiro/crédito) → Task 5. ✅
- Backfill → explicitamente out of scope (decisão do usuário). ✅

**2. Placeholder scan:** Código real em todos os steps de backend; frontend com trechos completos e nota pra preservar o `try/catch` ao redor. Único ponto a confirmar em execução: nome/assinatura reais do endpoint `mark client paid` (Task 2 Step 5) — sinalizado explicitamente.

**3. Type consistency:** `confirm_debit(pagamento_id=...)` (kwarg) bate com a assinatura em `credit_service.py:215`. `cancel_package(package_id, force, cancelled_by)` bate com `package_cancellation_service.py:144`. Resposta 409 `blocked_paid`/`paid_clients`/`paid_count` consistente entre backend (Task 1 Step 4) e frontend (Tasks 3-4). `_role_from`/`can_cancel` já existem em `dashboard.py:42` e `auth_service.py:113`.
