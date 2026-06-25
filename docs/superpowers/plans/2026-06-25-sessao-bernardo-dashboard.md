# Sessão Bernardo no dashboard `/` + usuário `bernardo` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expor a sessão Bernardo (acúmulo + "fechar pacote") dentro do dashboard `/`, entre Comercial e Estoque, visível só a `admin` e a um novo usuário `bernardo` (senha `Bernard0`), que enxerga somente essa sessão.

**Architecture:** Novo papel `bernardo` em `auth_service.ROLES`; `visible_groups` vira fonte única de verdade dos blocos da sidebar. O render dos cards de acúmulo é extraído para um módulo compartilhado `static/js/bernardo_cards.js` (`window.BernardoCards.render`), reusado pela página standalone `/bernardo` (já em prod) e pela nova `#section-bernardo` no dashboard. O front (`dashboard_v2.js`/`.html`) injeta um header "Bernardo" no rail entre Comercial e Estoque. Guard no router restringe `/api/bernardo/*` + `/bernardo` a admin+bernardo. Hash bcrypt entra em `docker-stack.yml` + `deploy/.env`.

**Tech Stack:** Python 3.12 / FastAPI, bcrypt, Jinja2, JS vanilla, pytest, Docker Swarm.

## Global Constraints

- Senha inicial do usuário: `Bernard0` (literal). Username: `bernardo` (lowercase).
- Nome da sessão (match no título da enquete): `"Bernardo"` — já em `app/sessions.py`.
- Hash bcrypt custo 12 (igual aos demais usuários). **Nunca** commitar nem logar o hash.
- Testes rodam com `DASHBOARD_AUTH_DISABLED=true` → middleware injeta `role="admin"`.
- Comando de teste: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v`.
- Validação de UI: servidor scratch em **porta livre (8023)** + SQLite scratch; **porta 8000 é o container de PROD — não usar.**
- Render dos cards é fonte única em `window.BernardoCards.render(containerEl, session)` — sem duplicar a lógica entre página standalone e view do dashboard.
- Deploy só via push em `main` (CI lê `deploy/.env` do host). Sem migration de banco.
- `git commit` termina com a linha `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Papel `bernardo` + `visible_groups` (auth_service)

**Files:**
- Modify: `app/services/auth_service.py` (`ROLES` ~L21; `visible_groups` ~L121-129)
- Test: `tests/unit/test_auth_service.py` (criar)

**Interfaces:**
- Consumes: nada (base).
- Produces: `ROLES` inclui `"bernardo"`; `visible_groups(role)` devolve as tuplas novas; `verify_credentials("bernardo", pw)` valida contra `RAYLOOK_USER_BERNARDO_HASH`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_auth_service.py`:

```python
import bcrypt
from app.services import auth_service as auth


def test_bernardo_is_a_role():
    assert "bernardo" in auth.ROLES


def test_visible_groups_bernardo_only_sees_bernardo():
    assert auth.visible_groups("bernardo") == ("bernardo",)


def test_visible_groups_admin_includes_bernardo_and_clientes():
    g = auth.visible_groups("admin")
    assert "bernardo" in g and "clientes" in g and "comercial" in g


def test_visible_groups_stock_keeps_enquetes_and_clientes():
    assert auth.visible_groups("estoque") == ("estoque", "enquetes", "clientes")
    assert auth.visible_groups("logistica") == ("logistica", "enquetes", "clientes")


def test_verify_credentials_bernardo(monkeypatch):
    h = bcrypt.hashpw(b"Bernard0", bcrypt.gensalt()).decode()
    monkeypatch.setenv("RAYLOOK_USER_BERNARDO_HASH", h)
    assert auth.verify_credentials("bernardo", "Bernard0") == "bernardo"
    assert auth.verify_credentials("bernardo", "errada") is None


def test_bernardo_cannot_cancel():
    assert auth.can_cancel("bernardo") is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_auth_service.py -v`
Expected: FAIL (`"bernardo" not in ROLES`; `visible_groups("bernardo") == ()`).

- [ ] **Step 3: Implementar**

Em `app/services/auth_service.py`, trocar `ROLES`:

```python
ROLES = ("admin", "estoque", "logistica", "bernardo")
```

E substituir `visible_groups` inteira por:

```python
def visible_groups(role: str) -> Tuple[str, ...]:
    """Quais dropdowns do rail o role enxerga (id usado em RAIL_GROUPS no JS)."""
    if role == "admin":
        return ("comercial", "bernardo", "estoque", "logistica",
                "enquetes", "financeiro", "clientes")
    if role == "estoque":
        return ("estoque", "enquetes", "clientes")
    if role == "logistica":
        return ("logistica", "enquetes", "clientes")
    if role == "bernardo":
        return ("bernardo",)
    return ()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_auth_service.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Rodar a suíte de auth existente pra garantir não-regressão**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_main_auth_health.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/auth_service.py tests/unit/test_auth_service.py
git commit -m "feat(bernardo): papel bernardo + visible_groups como fonte única"
```

---

### Task 2: Guard de autorização do router Bernardo

**Files:**
- Modify: `app/routers/bernardo.py` (imports + construção do `router` + nova função)
- Test: `tests/unit/test_bernardo_api.py` (adicionar testes do guard)

**Interfaces:**
- Consumes: `request.state.role` (setado pelo middleware em `main.py`).
- Produces: `require_bernardo_access(request) -> str` (403 se role ∉ {admin, bernardo}); router passa a exigir esse dependency em todas as rotas.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/unit/test_bernardo_api.py`:

```python
import pytest
from types import SimpleNamespace
from fastapi import HTTPException
from app.routers.bernardo import require_bernardo_access


def _req(role):
    return SimpleNamespace(state=SimpleNamespace(role=role))


def test_guard_allows_admin():
    assert require_bernardo_access(_req("admin")) == "admin"


def test_guard_allows_bernardo():
    assert require_bernardo_access(_req("bernardo")) == "bernardo"


def test_guard_blocks_estoque():
    with pytest.raises(HTTPException) as exc:
        require_bernardo_access(_req("estoque"))
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_bernardo_api.py -k guard -v`
Expected: FAIL (`ImportError: cannot import name 'require_bernardo_access'`).

- [ ] **Step 3: Implementar o guard**

Em `app/routers/bernardo.py`, garantir os imports (`Depends`, `HTTPException`, `Request` do `fastapi`) e adicionar, antes da criação do `router`:

```python
_BERNARDO_ROLES = {"admin", "bernardo"}


def require_bernardo_access(request: Request) -> str:
    """403 a menos que o role seja admin ou bernardo (defesa em profundidade)."""
    role = getattr(request.state, "role", None)
    if role not in _BERNARDO_ROLES:
        raise HTTPException(status_code=403, detail="forbidden")
    return role
```

Anexar o dependency na construção do router (achar a linha `router = APIRouter(...)` e acrescentar `dependencies=[Depends(require_bernardo_access)]`):

```python
router = APIRouter(dependencies=[Depends(require_bernardo_access)])
```

(Se o `APIRouter(...)` já tiver outros kwargs, só adicionar `dependencies=[...]` à lista deles.)

- [ ] **Step 4: Rodar e ver passar**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_bernardo_api.py -v`
Expected: PASS (6 testes antigos — admin via auth-disabled — + 3 novos).

- [ ] **Step 5: Commit**

```bash
git add app/routers/bernardo.py tests/unit/test_bernardo_api.py
git commit -m "feat(bernardo): restringe /api/bernardo e /bernardo a admin+bernardo"
```

---

### Task 3: Extrair render compartilhado `bernardo_cards.js` + refatorar a página standalone

DRY: a lógica de render dos cards (fetch + cards + "Fechar pacote") vira fonte única em `window.BernardoCards.render`, consumida pela página `/bernardo` (já em prod) e, na Task 4, pela view do dashboard. Validação no **browser** (`/bernardo` deve continuar idêntica).

**Files:**
- Create: `static/js/bernardo_cards.js`
- Modify: `static/js/bernardo_page.js` (passa a chamar `BernardoCards.render`)
- Modify: `templates/bernardo.html` (carregar `bernardo_cards.js` antes do `bernardo_page.js`)

**Interfaces:**
- Produces: `window.BernardoCards.render(containerEl, session)` — `async`, faz `GET /api/bernardo/sessions/${session}`, renderiza os cards em `containerEl`; cada botão "Fechar pacote" faz `POST .../close` e, em `status==="ok"`, re-chama `render(containerEl, session)` (auto-refresh). Strings de usuário escapadas antes do DOM.

- [ ] **Step 1: Criar `static/js/bernardo_cards.js`**

Extrair a lógica atual de `bernardo_page.js` (escapeHtml, STATUS_MSG, load) parametrizando `containerEl` e `session`:

```javascript
// Render compartilhado dos cards de acúmulo Bernardo.
// Usado pela página standalone /bernardo e pela view #section-bernardo do dashboard.
// Strings de usuário escapadas antes do DOM.
(function () {
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const STATUS_MSG = {
    no_votes: "Sem votos pra fechar.",
    not_session: "Enquete não pertence à sessão.",
    not_found: "Enquete não encontrada.",
    no_product: "Enquete sem produto associado.",
    rpc_error: "Falha ao fechar o pacote (tente de novo).",
  };

  async function render(containerEl, session) {
    if (!containerEl) return;
    containerEl.className = "bn-empty";
    containerEl.textContent = "Carregando…";
    let data;
    try {
      const res = await fetch(`/api/bernardo/sessions/${session}`, { credentials: "same-origin" });
      data = await res.json();
    } catch (e) {
      containerEl.textContent = "Erro ao carregar.";
      return;
    }
    if (!data.enquetes || !data.enquetes.length) {
      containerEl.textContent = "Nenhuma enquete Bernardo ativa.";
      return;
    }
    containerEl.className = "";
    containerEl.innerHTML = "";
    for (const enq of data.enquetes) {
      const parts = (enq.participants || [])
        .map(p => `${escapeHtml(p.nome)}: ${escapeHtml(String(p.qty))}`)
        .join(" · ") || "—";
      const card = document.createElement("div");
      card.className = "bn-card";
      card.innerHTML =
        `<div class="bn-card-title">${escapeHtml(enq.titulo)}</div>` +
        `<div class="bn-card-meta">Acúmulo: <b>${escapeHtml(String(enq.total_qty))}</b> peças · ` +
          `${escapeHtml(String(enq.participants_count))} cliente(s)</div>` +
        `<div class="bn-card-meta">${parts}</div>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bn-btn";
      btn.textContent = "Fechar pacote";
      btn.disabled = (enq.total_qty || 0) <= 0;
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const r = await fetch(`/api/bernardo/sessions/${session}/close`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ enquete_id: enq.enquete_id }),
          });
          const out = await r.json();
          if (out.status === "ok") {
            render(containerEl, session);
          } else {
            alert(STATUS_MSG[out.status] || ("Não foi possível fechar: " + (out.status || "erro")));
            btn.disabled = false;
          }
        } catch (e) {
          alert("Erro: " + e.message);
          btn.disabled = false;
        }
      };
      card.appendChild(btn);
      containerEl.appendChild(card);
    }
  }

  window.BernardoCards = { render };
})();
```

- [ ] **Step 2: Refatorar `static/js/bernardo_page.js` pra usar o módulo**

Substituir TODO o conteúdo de `static/js/bernardo_page.js` por:

```javascript
// Página standalone /bernardo — usa o render compartilhado (BernardoCards).
(function () {
  document.addEventListener("DOMContentLoaded", () => {
    window.BernardoCards.render(document.getElementById("bernardo-cards"), "Bernardo");
  });
})();
```

- [ ] **Step 3: Carregar `bernardo_cards.js` antes do `bernardo_page.js` no template**

Em `templates/bernardo.html`, achar o `<script src="/static/js/bernardo_page.js"></script>` e inserir, **imediatamente antes** dele:

```html
  <script src="/static/js/bernardo_cards.js"></script>
```

- [ ] **Step 4: Validar `/bernardo` no browser (sem regressão)**

```bash
export RAYLOOK_USER_ADMIN_HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'admin123',bcrypt.gensalt()).decode())")
export RAYLOOK_USER_ESTOQUE_HASH="$RAYLOOK_USER_ADMIN_HASH" RAYLOOK_USER_LOGISTICA_HASH="$RAYLOOK_USER_ADMIN_HASH"
export SESSION_SECRET=devsecret PORTAL_SECURE_COOKIES=false
export DATA_DIR=/tmp/claude-0/-root-rodrigo-raylook/01d0bfe4-55ac-4f98-ba5b-9a9c94bd27c3/scratchpad
PYTHONPATH=.venv/lib/python3.12/site-packages:. nohup python3 -m uvicorn main:app --host 127.0.0.1 --port 8023 >/tmp/bn8023.log 2>&1 &
```

Validar (Playwright MCP): logar como `admin`, abrir `/bernardo` → cards renderizam igual a antes (ou "Nenhuma enquete Bernardo ativa." sem dados). Sem erro de console (`BernardoCards is not defined` etc.).

- [ ] **Step 5: Derrubar scratch e commitar**

```bash
pkill -f "uvicorn main:app --host 127.0.0.1 --port 8023" || true
git add static/js/bernardo_cards.js static/js/bernardo_page.js templates/bernardo.html
git commit -m "refactor(bernardo): render dos cards em módulo compartilhado BernardoCards"
```

---

### Task 4: Sessão Bernardo no `/` (rail + section + gating + conteúdo)

Frontend sem suíte JS — validação é no **browser** (porta 8023 + SQLite scratch). Entrega: Bernardo entre Comercial e Estoque, visível por papel, abrindo a view que renderiza os cards via `BernardoCards`.

**Files:**
- Modify: `templates/dashboard_v2.html` (CSS de `#section-*`; estilos `.bn-*`; novo `#section-bernardo`; `<script>` de `bernardo_cards.js` + `bernardo_section.js`)
- Modify: `static/js/dashboard_v2.js` (gating dos blocos estáticos; entrada `bernardo` em `RAIL_GROUPS`; ramo `panel` no `renderRail`; wiring de clique; fechar Bernardo nos handlers de estado)
- Create: `static/js/bernardo_section.js` (open/close + `refresh()` chama `BernardoCards.render`)
- Modify: `static/js/enquetes.js`, `static/js/finance-toggle.js`, `static/js/clientes.js` (fechar Bernardo ao abrir cada uma)

**Interfaces:**
- Consumes: `window.BernardoCards.render` (Task 3); `visibleGroups` (Set de `/api/me`); `#section-*`, `#packages-area`, grupos `*-group`.
- Produces (globais usados pelas outras views): `window._bernardoOpen` (bool), `window._bernardoClose()`, `window._bernardoToggle()`.

- [ ] **Step 1: HTML — `#section-bernardo`, estilos e scripts**

Em `templates/dashboard_v2.html`:

(a) Adicionar `#section-bernardo` aos seletores de CSS que hoje listam `#section-clientes, #section-enquetes` (três blocos: ~L393-394, ~L418-419, ~L427-428). Em cada um, incluir `#section-bernardo,` na lista. Ex. no bloco `.active`:

```css
    #section-clientes.active,
    #section-enquetes.active,
    #section-bernardo.active {
```

(b) Adicionar os estilos `.bn-*` (escopados na seção) dentro do `<style>` do head:

```css
    #section-bernardo h2 { font-size: 18px; margin: 0 0 16px; }
    #section-bernardo .bn-card { border: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
               padding: 16px 18px; margin-bottom: 12px; background: rgba(255,255,255,0.02); }
    #section-bernardo .bn-card-title { font-weight: 600; font-size: 15px; }
    #section-bernardo .bn-card-meta { color: var(--text-muted, #999); font-size: 13px; margin-top: 6px;
                    font-variant-numeric: tabular-nums; }
    #section-bernardo .bn-card-meta b { color: var(--text-primary, #eee); }
    #section-bernardo .bn-btn { margin-top: 14px; padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer;
              background: var(--accent, #d4a017); color: var(--bg-main, #111); font-weight: 600;
              font-family: inherit; font-size: 13px; }
    #section-bernardo .bn-btn[disabled] { opacity: .45; cursor: not-allowed; }
    #section-bernardo .bn-empty { color: var(--text-muted, #999); padding: 24px 0; }
```

(c) Adicionar a seção no `content-area`, logo após o fechamento de `section-clientes` (`</div><!-- /section-clientes -->`, ~L1279):

```html
        <div id="section-bernardo">
            <h2>Bernardo — acúmulo</h2>
            <div id="bernardo-cards" class="bn-empty">Carregando…</div>
        </div><!-- /section-bernardo -->
```

(d) Incluir os scripts ao lado dos outros (após `enquetes.js`, ~L1378), nesta ordem:

```html
<script src="/static/js/bernardo_cards.js"></script>
<script src="/static/js/bernardo_section.js"></script>
```

- [ ] **Step 2: Criar `static/js/bernardo_section.js`**

```javascript
// Sessão Bernardo integrada ao dashboard /. View-toggling espelha enquetes.js.
// Render delegado ao módulo compartilhado BernardoCards.
(function () {
  const SESSION = "Bernardo";
  const state = { open: false };

  function openBernardo() {
    state.open = true;
    window._bernardoOpen = true;
    document.getElementById("packages-area")?.classList.add("retracted");
    document.getElementById("section-bernardo")?.classList.add("active");
    document.getElementById("section-enquetes")?.classList.remove("active");
    document.getElementById("section-finance")?.classList.remove("active");
    document.getElementById("section-clientes")?.classList.remove("active");
    document.getElementById("enquetes-group")?.classList.remove("open");
    document.getElementById("fin-group")?.classList.remove("open");
    document.getElementById("clientes-group")?.classList.remove("open");
    window._enquetesOpen = false;
    window._financeOpen = false;
    window._clientesOpen = false;
    window._railCollapseGroups?.();
    document.querySelector('[data-group="bernardo"]')?.classList.add("open");
    window.BernardoCards?.render(document.getElementById("bernardo-cards"), SESSION);
  }

  function closeBernardo() {
    state.open = false;
    window._bernardoOpen = false;
    document.getElementById("section-bernardo")?.classList.remove("active");
    document.querySelector('[data-group="bernardo"]')?.classList.remove("open");
    if (!window._financeOpen && !window._clientesOpen && !window._enquetesOpen) {
      document.getElementById("packages-area")?.classList.remove("retracted");
    }
  }

  function toggleBernardo() {
    if (state.open) closeBernardo(); else openBernardo();
  }

  window._bernardoClose = closeBernardo;
  window._bernardoToggle = toggleBernardo;
})();
```

- [ ] **Step 3: `dashboard_v2.js` — gating dos blocos estáticos**

Logo após o gate do `fin-group` (~L30-32), acrescentar:

```javascript
    if (!visibleGroups.has("enquetes")) {
        document.getElementById("enquetes-group")?.style.setProperty("display", "none");
    }
    if (!visibleGroups.has("clientes")) {
        document.getElementById("clientes-group")?.style.setProperty("display", "none");
    }
```

- [ ] **Step 4: `dashboard_v2.js` — entrada Bernardo no `RAIL_GROUPS`**

Inserir, **logo após** o objeto `comercial` no array `RAIL_GROUPS` (~L323):

```javascript
        { id: "bernardo", label: "Bernardo", panel: true },
```

- [ ] **Step 5: `dashboard_v2.js` — ramo `panel` no `renderRail`**

No `.map` de `renderRail` (`RAIL_GROUPS.filter(...).map(g => {`), inserir no começo do callback, antes de `const open = groupOpen[g.id];`:

```javascript
            if (g.panel) {
                const isOpen = !!window._bernardoOpen;
                return `
                <div class="rail-group ${isOpen ? "open" : ""}" data-group="${g.id}">
                    <div class="rail-group-header" data-panel="${g.id}">
                        <span class="rail-group-label">${g.label}</span>
                        <span class="rail-group-total"></span>
                        <i class="fas fa-chevron-down rail-group-chevron"></i>
                    </div>
                </div>`;
            }
```

E logo após `rail.innerHTML = groupsHtml;`, registrar o clique do header-panel:

```javascript
        rail.querySelectorAll('.rail-group-header[data-panel]').forEach(h =>
            h.addEventListener("click", () => {
                if (h.dataset.panel === "bernardo") window._bernardoToggle?.();
            })
        );
```

- [ ] **Step 6: `dashboard_v2.js` — fechar Bernardo ao navegar por estado**

No handler de clique do `.rail-group-header` (dentro do `if (willOpen) {...}`) e no início do callback do `.rail-step`, acrescentar — junto das chamadas `window._enquetesClose?.()` já existentes:

```javascript
                if (window._bernardoOpen) window._bernardoClose?.();
```

- [ ] **Step 7: Fechar Bernardo ao abrir Enquetes / Financeiro / Clientes**

Em cada função de abertura, junto de onde já removem `.active` das outras sections, acrescentar `window._bernardoClose?.();`:

- `static/js/enquetes.js` (em `openEnquetes`, perto de L118)
- `static/js/finance-toggle.js` (na função que abre o financeiro, onde seta `_financeOpen = true`)
- `static/js/clientes.js` (em `openClientes`, perto de L45-50)

- [ ] **Step 8: Subir servidor scratch e validar no browser**

```bash
export RAYLOOK_USER_ADMIN_HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'admin123',bcrypt.gensalt()).decode())")
export RAYLOOK_USER_BERNARDO_HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'Bernard0',bcrypt.gensalt()).decode())")
export RAYLOOK_USER_ESTOQUE_HASH="$RAYLOOK_USER_ADMIN_HASH" RAYLOOK_USER_LOGISTICA_HASH="$RAYLOOK_USER_ADMIN_HASH"
export SESSION_SECRET=devsecret PORTAL_SECURE_COOKIES=false
export DATA_DIR=/tmp/claude-0/-root-rodrigo-raylook/01d0bfe4-55ac-4f98-ba5b-9a9c94bd27c3/scratchpad
PYTHONPATH=.venv/lib/python3.12/site-packages:. nohup python3 -m uvicorn main:app --host 127.0.0.1 --port 8023 >/tmp/bn8023.log 2>&1 &
```

Validar (Playwright MCP), **sem `DASHBOARD_AUTH_DISABLED`**:
1. `/login` como `admin` → rail mostra **Bernardo entre Comercial e Estoque**; clicar abre `#section-bernardo` com os cards (ou "Nenhuma enquete Bernardo ativa."); clicar em Comercial/Enquetes fecha Bernardo.
2. `/login` como `bernardo`/`Bernard0` → sidebar mostra **só Bernardo** (sem rail de estados, sem Enquetes/Financeiro/Clientes); a view abre e os cards renderizam.
3. `/logout` entre os dois. Sem erro de console.

- [ ] **Step 9: Derrubar o scratch e commitar**

```bash
pkill -f "uvicorn main:app --host 127.0.0.1 --port 8023" || true
git add templates/dashboard_v2.html static/js/dashboard_v2.js static/js/bernardo_section.js \
        static/js/enquetes.js static/js/finance-toggle.js static/js/clientes.js
git commit -m "feat(bernardo): sessão no rail do / (entre Comercial e Estoque) + gating por papel"
```

---

### Task 5: Provisionar usuário `bernardo` (secret + deploy)

**Files:**
- Modify: `deploy/docker-stack.yml` (env do service dashboard, ~L93-96)
- Modify: `deploy/.env` (host, gitignored — **NÃO commitar**)

**Interfaces:**
- Consumes: `RAYLOOK_USER_BERNARDO_HASH` (lido por `auth_service._hash_for("bernardo")`).
- Produces: usuário `bernardo` logável em prod após deploy.

- [ ] **Step 1: Adicionar a env var no `docker-stack.yml`**

Após a linha `RAYLOOK_USER_LOGISTICA_HASH: ${RAYLOOK_USER_LOGISTICA_HASH:?obrigatório}`:

```yaml
      RAYLOOK_USER_BERNARDO_HASH: ${RAYLOOK_USER_BERNARDO_HASH:?obrigatório}
```

- [ ] **Step 2: Gerar o hash e adicionar ao `deploy/.env` do host (CONFIRMAR antes)**

> ⚠️ `deploy/.env` é secret no host. **Pedir confirmação explícita ao usuário** antes de editar. Não imprimir o hash em resposta/log.

```bash
HASH=$(PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 -c "import bcrypt;print(bcrypt.hashpw(b'Bernard0',bcrypt.gensalt()).decode())")
printf 'RAYLOOK_USER_BERNARDO_HASH=%s\n' "$HASH" >> deploy/.env
grep -c '^RAYLOOK_USER_BERNARDO_HASH=' deploy/.env   # deve imprimir 1
```

- [ ] **Step 3: Sanidade — `.env` não está staged**

```bash
git status --porcelain deploy/.env   # deve sair VAZIO (gitignored)
```

Expected: sem saída (arquivo ignorado).

- [ ] **Step 4: Commit do stack (sem o secret)**

```bash
git add deploy/docker-stack.yml
git commit -m "chore(deploy): exige RAYLOOK_USER_BERNARDO_HASH no service dashboard"
```

---

### Fechamento

- [ ] Rodar suíte unitária completa: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v` → tudo verde.
- [ ] `git diff main...HEAD --stat` revisado.
- [ ] Abrir PR (push **só com aprovação do usuário**). O deploy depende de `deploy/.env` do host ter `RAYLOOK_USER_BERNARDO_HASH` **antes** do `docker stack deploy` (senão `:?obrigatório` falha o deploy).

## Self-review (cobertura do spec)

- Papel `bernardo` + ROLES + verify_credentials → Task 1. ✓
- `visible_groups` fonte única (4 papéis) → Task 1. ✓
- Render dos cards sem duplicação (módulo compartilhado) → Task 3. ✓
- `/bernardo` standalone segue funcionando → Task 3 (refatorado p/ usar o módulo, validado no browser). ✓
- Bernardo entre Comercial e Estoque → Task 4 (Steps 4-5). ✓
- Gating de Enquetes/Financeiro/Clientes p/ bernardo → Task 4 (Step 3) + Task 1. ✓
- View reusando `/api/bernardo/*` via `BernardoCards` → Task 4 (Step 2). ✓
- Guard admin+bernardo na API/página → Task 2. ✓
- Hash em docker-stack + deploy/.env → Task 5. ✓
- Testes de auth + guard + browser → Tasks 1,2,3,4. ✓
- Sem migration → respeitado (nenhuma task altera schema). ✓
