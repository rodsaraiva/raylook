# Sessão Bernardo com abas da Comercial — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A sessão Bernardo passa a usar o layout da Comercial (abas Aberto/Fechado/Aguardando Pagamento/Pago/Cancelados); pacotes de enquete com "Bernardo" no título aparecem só em Bernardo, e a Comercial os esconde.

**Architecture:** O backend marca cada pacote com `session` (`"Bernardo"` ou `null`) por `session_for_title`. O front ganha uma dimensão `activeSession` ("comercial"|"bernardo"|"all") que filtra a lista e as contagens do rail; o grupo Bernardo deixa de ser painel e vira grupo de estados igual à Comercial. O fechamento por acúmulo continua via botão "Fechar pacote" no detalhe do pacote aberto.

**Tech Stack:** Python/FastAPI, JS vanilla, pytest, Jinja2.

## Global Constraints

- Match de sessão: título contém `"Bernardo"` (substring case-insensitive) via `app/sessions.py::session_for_title`; nome da sessão = `"Bernardo"`.
- **Comercial inalterada** exceto por esconder os pacotes de título "Bernardo". Abas, fluxo, fechamento em 24, detalhe — idênticos.
- **Estoque/Logística mostram tudo** (sessão `"all"`, sem filtro).
- **Mantém o acúmulo**: enquetes Bernardo não fecham em 24; fecham via "Fechar pacote" → `POST /api/bernardo/sessions/Bernardo/close`. Sem mudança em `whatsapp_domain_service.py`.
- Sem migration de banco.
- Testes: `DASHBOARD_AUTH_DISABLED=true PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -m pytest <arquivo> -v`.
- UI validada em servidor scratch **porta 8023** + SQLite scratch (`DATA_DIR`); **porta 8000 é PROD — não usar**.
- `git commit` termina com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Backend — campo `session` por pacote (`dashboard.py`)

**Files:**
- Modify: `app/routers/dashboard.py` (import + dict `item` ~L462)
- Test: `tests/unit/test_dashboard_packages_endpoint.py` (adicionar teste)

**Interfaces:**
- Produces: cada item de `packages_by_state[*]` e `cancelled` ganha `"session"`: `"Bernardo"` se o título da enquete casar, senão `None`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/unit/test_dashboard_packages_endpoint.py`:

```python
def test_packages_tagged_with_session_by_title(monkeypatch):
    from tests._helpers.fake_supabase import FakeSupabaseClient, install_fake
    fake = FakeSupabaseClient({
        "pacotes": [
            {"id": "pk-b", "status": "open", "sequence_no": 1, "enquete_id": "e-bern",
             "capacidade_total": 24, "total_qty": 0,
             "opened_at": "2026-06-01T10:00:00+00:00",
             "created_at": "2026-06-01T10:00:00+00:00",
             "updated_at": "2026-06-01T10:00:00+00:00"},
            {"id": "pk-c", "status": "open", "sequence_no": 2, "enquete_id": "e-com",
             "capacidade_total": 24, "total_qty": 0,
             "opened_at": "2026-06-01T11:00:00+00:00",
             "created_at": "2026-06-01T11:00:00+00:00",
             "updated_at": "2026-06-01T11:00:00+00:00"},
        ],
        "enquetes": [
            {"id": "e-bern", "produto_id": "p1", "titulo": "Bernardo — Inverno", "external_poll_id": "wa-b"},
            {"id": "e-com", "produto_id": "p1", "titulo": "Camiseta Preta", "external_poll_id": "wa-c"},
        ],
        "produtos": [{"id": "p1", "nome": "X", "valor_unitario": 50.0}],
        "clientes": [], "pacote_clientes": [], "vendas": [], "pagamentos": [], "votos": [],
    })
    install_fake(monkeypatch, fake)
    import main as main_module
    from fastapi.testclient import TestClient
    client = TestClient(main_module.app)
    body = client.get("/api/dashboard/packages").json()
    by_id = {it["id"]: it for it in body["packages_by_state"]["aberto"]}
    assert by_id["pk-b"]["session"] == "Bernardo"
    assert by_id["pk-c"]["session"] is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -m pytest tests/unit/test_dashboard_packages_endpoint.py -k session -v`
Expected: FAIL (`KeyError: 'session'`).

- [ ] **Step 3: Implementar**

Em `app/routers/dashboard.py`, garantir o import (perto dos outros imports do topo):

```python
from app.sessions import session_for_title
```

No dict `item = { ... }` (o que tem `"enquete_title": enq.get("titulo")`), adicionar a chave:

```python
            "session": (session_for_title(enq.get("titulo")) or {}).get("name"),
```

- [ ] **Step 4: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -m pytest tests/unit/test_dashboard_packages_endpoint.py -v`
Expected: PASS (todos, incluindo o novo).

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_dashboard_packages_endpoint.py
git commit -m "feat(bernardo): taggear pacotes com session por título no /packages"
```

---

### Task 2: Frontend — dimensão de sessão no rail e na lista (`dashboard_v2.js`)

Grupo Bernardo vira grupo de estados (igual Comercial); `activeSession` filtra lista e contagens. Validação no **browser**.

**Files:**
- Modify: `static/js/dashboard_v2.js` (var `activeSession`; `currentItems`/helpers ~L332-335; `RAIL_GROUPS` ~L343-362; `groupOpen` ~L365-368; default `activeState` ~L236-237; `renderRail` ~L376-474)

**Interfaces:**
- Consumes: `item.session` (Task 1).
- Produces: `activeSession` ("comercial"|"bernardo"|"all"); `itemsFor(session,state)`, `sessionCount(state,session)`, `currentItems()` filtram por sessão; rail-step carrega `data-session`.

- [ ] **Step 1: Declarar `activeSession` no topo**

Logo após `let activeState = null;` (~L6), adicionar:

```javascript
    let activeSession = "comercial";
```

- [ ] **Step 2: Helpers de filtro por sessão**

Substituir a função `currentItems` (L332-335):

```javascript
    function currentItems() {
        if (activeState === "cancelled") return data.cancelled || [];
        return data.packages_by_state[activeState] || [];
    }
```

por:

```javascript
    function itemsFor(session, state) {
        const list = state === "cancelled" ? (data.cancelled || []) : (data.packages_by_state[state] || []);
        if (session === "bernardo") return list.filter(p => p.session === "Bernardo");
        if (session === "comercial") return list.filter(p => p.session !== "Bernardo");
        return list; // "all" — estoque/logística
    }
    function sessionCount(state, session) { return itemsFor(session, state).length; }
    function currentItems() { return itemsFor(activeSession, activeState); }
```

- [ ] **Step 3: `activeState` inicial ciente da sessão**

Trocar (L236-237):

```javascript
        if (!activeState) {
            activeState = L.STATES.find(s => (data.packages_by_state[s] || []).length > 0) || "aberto";
        }
```

por:

```javascript
        if (!activeState) {
            activeState = L.STATES.find(s => itemsFor(activeSession, s).length > 0) || "aberto";
        }
```

- [ ] **Step 4: `RAIL_GROUPS` — Bernardo vira grupo de estados**

Substituir o array `RAIL_GROUPS` (L343-362) por:

```javascript
    const RAIL_GROUPS = [
        { id: "comercial", label: "Comercial", session: "comercial",
          states: ["aberto", "fechado", "confirmado", "pago"], extras: ["cancelled"] },
        { id: "bernardo", label: "Bernardo", session: "bernardo",
          states: ["aberto", "fechado", "confirmado", "pago"], extras: ["cancelled"] },
        { id: "estoque", label: "Estoque", session: "all",
          states: ["pago", "pendente", "separado"], labels: { pago: "Fila de separação" } },
        { id: "logistica", label: "Logística", session: "all",
          states: ["separado", "enviado"] },
    ];
```

- [ ] **Step 5: `groupOpen` + init de `activeSession`**

Substituir (L365-368):

```javascript
    const groupOpen = { comercial: false, estoque: false, logistica: false };
    if (visibleGroups.has("comercial")) groupOpen.comercial = true;
    else if (visibleGroups.has("estoque")) groupOpen.estoque = true;
    else if (visibleGroups.has("logistica")) groupOpen.logistica = true;
```

por:

```javascript
    const groupOpen = { comercial: false, bernardo: false, estoque: false, logistica: false };
    if (visibleGroups.has("comercial")) groupOpen.comercial = true;
    else if (visibleGroups.has("bernardo")) groupOpen.bernardo = true;
    else if (visibleGroups.has("estoque")) groupOpen.estoque = true;
    else if (visibleGroups.has("logistica")) groupOpen.logistica = true;
    activeSession = groupOpen.comercial ? "comercial" : (groupOpen.bernardo ? "bernardo" : "all");
```

- [ ] **Step 6: Reescrever `renderRail` (remove painel, adiciona sessão)**

Substituir a função `renderRail` inteira (L376-474) por:

```javascript
    function renderRail() {
        const rail = document.getElementById("rail");
        const groupsHtml = RAIL_GROUPS.filter(g => visibleGroups.has(g.id)).map(g => {
            const open = groupOpen[g.id];
            const totalCount = g.states.reduce((sum, s) => sum + sessionCount(s, g.session), 0);
            const stepsHtml = g.states.map((s, i) => {
                const label = g.labels?.[s] ?? L.STATE_LABELS[s];
                const isActive = s === activeState && g.session === activeSession;
                return `
                <div class="rail-step ${isActive ? "active" : ""}" data-state="${s}" data-session="${g.session}">
                    <div class="num">${i + 1}</div>
                    <div>
                        <div class="label">${label}</div>
                        <div class="sub">${DESCS[s]}</div>
                    </div>
                    <div class="count">${sessionCount(s, g.session)}</div>
                </div>`;
            }).join("");
            const extrasHtml = (g.extras || []).map(s => {
                if (s !== "cancelled") return "";
                const isActive = activeState === "cancelled" && g.session === activeSession;
                return `
                <div class="rail-step rail-cancelled ${isActive ? "active" : ""}" data-state="cancelled" data-session="${g.session}">
                    <div class="num" style="background:rgba(248,113,113,0.15);color:var(--danger);">×</div>
                    <div><div class="label">Cancelados</div><div class="sub">histórico</div></div>
                    <div class="count">${sessionCount("cancelled", g.session)}</div>
                </div>`;
            }).join("");
            return `
                <div class="rail-group ${open ? "open" : ""}" data-group="${g.id}">
                    <div class="rail-group-header" data-toggle="${g.id}">
                        <span class="rail-group-label">${g.label}</span>
                        <span class="rail-group-total">${totalCount}</span>
                        <i class="fas fa-chevron-down rail-group-chevron"></i>
                    </div>
                    <div class="rail-group-body">${stepsHtml}${extrasHtml}</div>
                </div>`;
        }).join("");

        rail.innerHTML = groupsHtml;

        rail.querySelectorAll(".rail-group-header").forEach(h =>
            h.addEventListener("click", () => {
                const id = h.dataset.toggle;
                const willOpen = !groupOpen[id];
                Object.keys(groupOpen).forEach(k => { groupOpen[k] = false; });
                groupOpen[id] = willOpen;
                if (willOpen && window._financeOpen) window.toggleFinanceView();
                if (willOpen && window._clientesOpen) window._clientesClose?.();
                if (willOpen && window._enquetesOpen) window._enquetesClose?.();
                if (willOpen) {
                    const group = RAIL_GROUPS.find(g => g.id === id);
                    activeSession = group.session;
                    const firstState = group?.states?.[0];
                    if (firstState) {
                        activeState = firstState;
                        listPage = 1;
                        const pkgs = currentItems();
                        selectedId = pkgs[0] ? pkgs[0].id : null;
                    }
                    render();
                } else {
                    renderRail();
                }
            })
        );
        rail.querySelectorAll(".rail-step").forEach(el =>
            el.addEventListener("click", () => {
                if (window._financeOpen) window.toggleFinanceView();
                if (window._clientesOpen) window._clientesClose?.();
                if (window._enquetesOpen) window._enquetesClose?.();
                activeSession = el.dataset.session;
                activeState = el.dataset.state;
                listPage = 1;
                const pkgs = currentItems();
                selectedId = pkgs[0] ? pkgs[0].id : null;
                render();
            })
        );
    }
```

- [ ] **Step 7: `node --check` + validar no browser**

```bash
node --check static/js/dashboard_v2.js
```

Subir o scratch (porta 8023; **não** 8000):

```bash
SCRATCH=/tmp/claude-0/-root-rodrigo-raylook/01d0bfe4-55ac-4f98-ba5b-9a9c94bd27c3/scratchpad
# cp de uma db com schema, ou usar a já semeada do scratch; seed: 1 enquete "Bernardo …" (open + votos) + 1 comum com pacotes em vários estados
export RAYLOOK_USER_ADMIN_HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'admin123',bcrypt.gensalt()).decode())")
export RAYLOOK_USER_BERNARDO_HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'Bernard0',bcrypt.gensalt()).decode())")
export RAYLOOK_USER_ESTOQUE_HASH="$RAYLOOK_USER_ADMIN_HASH" RAYLOOK_USER_LOGISTICA_HASH="$RAYLOOK_USER_ADMIN_HASH"
export SESSION_SECRET=devsecret PORTAL_SECURE_COOKIES=false DATA_DIR="$SCRATCH" DATA_BACKEND=sqlite RAYLOOK_SANDBOX=true
PYTHONPATH=.venv/lib/python3.12/site-packages:. nohup python3 -m uvicorn main:app --host 127.0.0.1 --port 8023 >/tmp/bn-abas.log 2>&1 &
```

Login via `form.submit()` no `evaluate` (o `.click()` do Playwright-MCP não dispara o submit nem o rail neste ambiente — usar `elemento.click()` nativo via `browser_evaluate`). Validar:
1. Admin: grupo **Bernardo** mostra as 5 abas (Aberto/Fechado/Ag. Pagamento/Pago/Cancelados); pacote Bernardo aparece **só** em Bernardo; pacote comum **só** em Comercial; contagens por sessão batem.
2. **Comercial** segue com as mesmas abas, sem os Bernardo.
3. **Estoque/Logística** mostram todos (inclui Bernardo em separado/enviado).
4. Usuário `bernardo`: vê só o grupo Bernardo com as abas.

- [ ] **Step 8: Commit**

```bash
git add static/js/dashboard_v2.js
git commit -m "feat(bernardo): grupo de estados por sessão (Comercial esconde Bernardo)"
```

---

### Task 3: Botão "Fechar pacote" no detalhe (`dashboard_v2.js`)

**Files:**
- Modify: `static/js/dashboard_v2.js` (`renderDetail` — bloco `.detail-actions` ~L848-858 e handlers ~L860+)

**Interfaces:**
- Consumes: `p.session`, `p.enquete_id`, `state`, `load()`.

- [ ] **Step 1: Botão condicional no `.detail-actions`**

Dentro do template de `.detail-actions` (renderDetail), logo após a linha do botão "Cancelar pacote" (`data-cancel`), adicionar:

```javascript
                ${(p.session === "Bernardo" && state === "aberto") ? `<button class="btn-primary" data-fechar-bernardo>Fechar pacote</button>` : ""}
```

- [ ] **Step 2: Handler do botão**

Logo após o bloco `detail.querySelectorAll("[data-advance]")...` (e antes do fim de `renderDetail`), adicionar:

```javascript
        detail.querySelector("[data-fechar-bernardo]")?.addEventListener("click", async () => {
            if (!confirm("Fechar o pacote acumulado desta enquete agora?")) return;
            const MSG = {
                no_votes: "Sem votos pra fechar.",
                not_session: "Enquete não pertence à sessão.",
                not_found: "Enquete não encontrada.",
                no_product: "Enquete sem produto associado.",
                rpc_error: "Falha ao fechar o pacote (tente de novo).",
            };
            try {
                const r = await fetch("/api/bernardo/sessions/Bernardo/close", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "same-origin",
                    body: JSON.stringify({ enquete_id: p.enquete_id }),
                });
                const out = await r.json();
                if (out.status === "ok") {
                    selectedId = null;
                    load();
                } else {
                    alert(MSG[out.status] || ("Não foi possível fechar: " + (out.status || "erro")));
                }
            } catch (e) {
                alert("Erro: " + e.message);
            }
        });
```

- [ ] **Step 3: `node --check` + validar no browser**

```bash
node --check static/js/dashboard_v2.js
```

No scratch: selecionar o pacote **Bernardo Aberto** → o detalhe mostra "Fechar pacote"; clicar → confirma → fecha (o pacote sai de Aberto e aparece em Fechado, com `total_qty` = soma real). Pacote **Comercial Aberto** **não** mostra o botão.

- [ ] **Step 4: Commit**

```bash
git add static/js/dashboard_v2.js
git commit -m "feat(bernardo): botão Fechar pacote no detalhe do pacote aberto Bernardo"
```

---

### Task 4: Remover o painel antigo + cache-bust

**Files:**
- Modify: `templates/dashboard_v2.html` (remove `#section-bernardo`, CSS `.bn-*`, `#section-bernardo` dos seletores de section, `<script>` do bernardo_section, bump `?v=`)
- Delete: `static/js/bernardo_section.js`
- Modify: `static/js/enquetes.js`, `static/js/finance-toggle.js`, `static/js/clientes.js` (remover `window._bernardoClose?.()`)

**Interfaces:**
- Nenhuma exportada. Remove o painel morto (Bernardo agora é grupo de estados).

- [ ] **Step 1: Remover markup, CSS e script do painel**

Em `templates/dashboard_v2.html`:
- Apagar o bloco `<div id="section-bernardo"> … </div><!-- /section-bernardo -->`.
- Remover `#section-bernardo` das 3 regras de CSS que o listam junto de `#section-clientes, #section-enquetes` (incluindo a `.active`).
- Apagar o bloco de estilos `#section-bernardo .bn-* { … }` (e `#section-bernardo h2`).
- Apagar a linha `<script src="/static/js/bernardo_section.js"></script>`.
- Trocar o cache-buster: `dashboard_v2.js?v=20260625` → `dashboard_v2.js?v=20260625b`.

(Manter `bernardo_cards.js`, a página `/bernardo` e `templates/bernardo.html` — seguem usados pelo standalone.)

- [ ] **Step 2: Deletar o módulo do painel**

```bash
git rm static/js/bernardo_section.js
```

- [ ] **Step 3: Remover as chamadas de cross-close**

Apagar a linha `window._bernardoClose?.();` (e comentário associado, se houver) em:
- `static/js/enquetes.js`
- `static/js/finance-toggle.js`
- `static/js/clientes.js`

- [ ] **Step 4: Sanidade + validar no browser**

```bash
grep -rn "_bernardo\|section-bernardo\|bernardo_section" static/js/dashboard_v2.js static/js/enquetes.js static/js/finance-toggle.js static/js/clientes.js templates/dashboard_v2.html
node --check static/js/enquetes.js && node --check static/js/finance-toggle.js && node --check static/js/clientes.js
```

Expected: o grep não retorna nada (todas as referências ao painel sumiram); `node --check` OK. No browser: dashboard carrega sem erro de console; Bernardo (grupo de estados) e Comercial seguem funcionando; abrir Enquetes/Financeiro/Clientes funciona normalmente.

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard_v2.html static/js/enquetes.js static/js/finance-toggle.js static/js/clientes.js
git commit -m "refactor(bernardo): remove o painel antigo (substituído pelo grupo de estados) + cache-bust"
```

---

### Fechamento

- [ ] `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_dashboard_packages_endpoint.py tests/unit/test_auth_service.py tests/unit/test_bernardo_api.py -v` → verde.
- [ ] `git diff main...HEAD --stat` revisado.
- [ ] PR (push só com aprovação do usuário).

## Self-review (cobertura do spec)

- Backend tagga `session` por título → Task 1. ✓
- Bernardo vira grupo de estados (5 abas) → Task 2 (Steps 4,6). ✓
- `activeSession` filtra lista + contagens; Comercial esconde Bernardo; estoque/logística mostram tudo → Task 2 (Steps 2,6). ✓
- Estado inicial por papel (admin→comercial, bernardo→bernardo) → Task 2 (Step 5). ✓
- Botão "Fechar pacote" no detalhe do Bernardo aberto (acúmulo mantido) → Task 3. ✓
- Remove painel antigo; standalone `/bernardo` fica → Task 4. ✓
- Comercial inalterada (só esconde Bernardo) → Task 2 (filtro `session !== "Bernardo"`), sem tocar no fluxo. ✓
- Sem migration; fechamento/acúmulo intactos → nenhuma task altera `whatsapp_domain_service.py`/schema. ✓
