# Filtro por Fornecedor na Lista Central — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um dropdown à esquerda do campo de busca da lista central que filtra os pacotes por fornecedor.

**Architecture:** O dropdown é populado com a lista cadastrada (`/api/enquetes/fornecedores` via `L.fetchFornecedores()`) mais "Todos" e "Sem fornecedor". A filtragem acontece no client-side dentro de `renderList()`, combinada em E com a busca de texto. O backend ganha o campo `fornecedor` nas linhas-por-cliente (`client_row`) pra o filtro funcionar em Separado/Enviado.

**Tech Stack:** FastAPI (backend), Jinja2 + JS vanilla (frontend), pytest (testes backend). Sem test runner JS — frontend validado no browser.

---

### Task 1: Backend — `fornecedor` no `client_row`

Os estados Separado/Enviado devolvem `client_row` que hoje não trazem `fornecedor`. Adicionar o campo, espelhando o item de pacote (`dashboard.py:476`).

**Files:**
- Modify: `app/routers/dashboard.py` (dict do `client_row`, após linha 453)
- Test: `tests/unit/test_dashboard_client_rows.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/unit/test_dashboard_client_rows.py`:

```python
def test_client_row_includes_fornecedor(fake_client):
    """client_row carrega o fornecedor do pacote pro filtro do dashboard."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)
    fake.tables["pacotes"][0]["fornecedor"] = "Acme Têxtil"

    body = client.get("/api/dashboard/packages").json()
    sep = body["packages_by_state"]["separado"]
    assert sep, "esperava client_rows em separado"
    for row in sep:
        assert row["fornecedor"] == "Acme Têxtil"


def test_client_row_fornecedor_defaults_empty(fake_client):
    """Pacote sem fornecedor → client_row vem com string vazia, não ausente."""
    client, fake = fake_client
    _setup_approved_pkg_with_two_clients(fake)

    body = client.get("/api/dashboard/packages").json()
    for row in body["packages_by_state"]["separado"]:
        assert row["fornecedor"] == ""
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_dashboard_client_rows.py -k fornecedor -v`
Expected: FAIL com `KeyError: 'fornecedor'` nos dois testes novos.

- [ ] **Step 3: Implementar — adicionar o campo ao `client_row`**

Em `app/routers/dashboard.py`, no dict do `client_row` (logo após `"created_at": pkg.get("created_at"),` na linha 453), adicionar a linha:

```python
                    "created_at": pkg.get("created_at"),
                    "fornecedor": pkg.get("fornecedor") or "",
                })
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_dashboard_client_rows.py -v`
Expected: PASS (todos, incluindo os 2 novos).

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_dashboard_client_rows.py
git commit -m "feat(dashboard): expor fornecedor nas client_rows pro filtro"
```

---

### Task 2: HTML — elemento `<select>` do filtro

Adicionar o dropdown à esquerda do `#search`, agrupado com ele no lado direito do header da lista. Estilo reaproveita o look do `.search-box`.

**Files:**
- Modify: `templates/dashboard_v2.html` (CSS perto da linha 165 + markup linha 1067-1073)

- [ ] **Step 1: Adicionar o CSS do select**

Logo após a regra `.search-box { ... }` (termina na linha 168), adicionar:

```css
    .filter-select {
        background: rgba(255,255,255,0.05); color: var(--text-primary);
        border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
        padding: 6px 12px; font-size: 12px; font-family: inherit; cursor: pointer;
        max-width: 180px;
    }
    .filter-select:focus { outline: none; border-color: var(--accent-soft); }
    .list-head-controls { display: flex; align-items: center; gap: 8px; }
```

- [ ] **Step 2: Envolver select + search no markup**

Substituir o bloco do header (linhas 1067-1073) por:

```html
                <div class="pkg-list-head">
                    <div>
                        <div class="pkg-list-title" id="list-title">Pacotes</div>
                        <div class="pkg-list-summary" id="list-summary"></div>
                    </div>
                    <div class="list-head-controls">
                        <select class="filter-select" id="fornecedor-filter" title="Filtrar por fornecedor">
                            <option value="">Todos os fornecedores</option>
                        </select>
                        <input class="search-box" placeholder="🔍 Buscar nome, telefone ou código (PAC…)" id="search">
                    </div>
                </div>
```

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard_v2.html
git commit -m "feat(dashboard): markup do dropdown de filtro por fornecedor"
```

---

### Task 3: JS — popular o dropdown, filtrar e ouvir o change

**Files:**
- Modify: `static/js/dashboard_v2.js` (estado ~linha 8; `load()` ~linha 217; `renderList()` ~linha 516; listener perto da linha 880)

- [ ] **Step 1: Adicionar variável de estado**

Após `let search = "";` (linha 8), adicionar:

```javascript
    let search = "";
    let fornecedorFilter = ""; // "" = todos, "__none__" = sem fornecedor, senão nome exato
```

- [ ] **Step 2: Função que popula o dropdown**

Adicionar a função antes de `async function load()` (linha 202):

```javascript
    // Popula o dropdown de fornecedor com a lista cadastrada + "Sem fornecedor".
    // Preserva a seleção atual ao repopular.
    async function populateFornecedorFilter() {
        const sel = document.getElementById("fornecedor-filter");
        if (!sel) return;
        const fornecedores = await L.fetchFornecedores();
        const atual = sel.value;
        sel.innerHTML = `<option value="">Todos os fornecedores</option>`
            + fornecedores.map(f => `<option value="${L.escapeHtml(f)}">${L.escapeHtml(f)}</option>`).join("")
            + `<option value="__none__">Sem fornecedor</option>`;
        sel.value = atual; // mantém seleção; vira "" se a opção sumiu
    }
```

- [ ] **Step 3: Chamar o populate no `load()`**

Em `load()`, após `render();` e antes do fechamento da função (linha 217), adicionar a chamada (fire-and-forget, não bloqueia o render):

```javascript
        render();
        populateFornecedorFilter();
    }
```

- [ ] **Step 4: Aplicar o filtro em `renderList()`**

Em `renderList()`, logo após o bloco que calcula `const filtered = q ? all.filter(...) : all;` (termina na linha 528), inserir a filtragem por fornecedor antes de `const totalCount = filtered.length;`:

```javascript
        }) : all;
        const byFornecedor = fornecedorFilter
            ? filtered.filter(p => {
                const f = (p.fornecedor || "").trim();
                return fornecedorFilter === "__none__" ? f === "" : f === fornecedorFilter;
            })
            : filtered;
        const totalCount = byFornecedor.length;
```

Em seguida, trocar TODAS as referências a `filtered` daquele ponto em diante por `byFornecedor` (linhas 530-536): `paged`, `totalPieces`, `totalValue`. Resultado:

```javascript
        const totalCount = byFornecedor.length;
        const paged = byFornecedor.slice((listPage - 1) * LIST_PAGE_SIZE, listPage * LIST_PAGE_SIZE);
        const totalPieces = byFornecedor.reduce((a, p) => p.type === "client_row"
            ? a + (p.qty || 0)
            : a + (Math.min(p.total_qty, p.capacidade_total) || 0), 0);
        const totalValue = byFornecedor.reduce((a, p) => p.type === "client_row"
            ? a + (p.total_amount || 0)
            : a + (p.total_value || 0), 0);
```

- [ ] **Step 5: Listener do `change`**

Após o listener de `#search` (linha 880-886), adicionar:

```javascript
    document.getElementById("fornecedor-filter").addEventListener("change", e => {
        fornecedorFilter = e.target.value;
        listPage = 1;
        selectedId = null;
        renderList();
    });
```

- [ ] **Step 6: Bump do cache-bust do script**

O HTML referencia `dashboard_v2.js?v=<hash>` pra forçar refresh no browser (ver commit `b14a9bc`). Localizar e incrementar:

Run: `grep -n "dashboard_v2.js?v=" templates/dashboard_v2.html`

Atualizar o valor `?v=` pra um novo (ex: data de hoje `20260609`).

- [ ] **Step 7: Commit**

```bash
git add static/js/dashboard_v2.js templates/dashboard_v2.html
git commit -m "feat(dashboard): filtrar lista central por fornecedor no dropdown"
```

---

### Task 4: Validação no browser

Sem test runner JS — a UI precisa ser aberta e validada (regra do projeto).

**Files:** nenhum (validação manual)

- [ ] **Step 1: Subir o dev server local**

Run:
```bash
cd /root/rodrigo/raylook
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```
(SQLite + sandbox; nada bate em API externa.)

- [ ] **Step 2: Validar no browser (Playwright MCP ou manual em `http://127.0.0.1:8000`)**

Checklist:
- [ ] O dropdown aparece à esquerda do campo de busca, alinhado.
- [ ] Opções: "Todos os fornecedores" + fornecedores cadastrados + "Sem fornecedor".
- [ ] Selecionar um fornecedor filtra a lista; o resumo (contagem/peças/valor) reflete o filtrado.
- [ ] "Sem fornecedor" lista só pacotes sem fornecedor.
- [ ] Filtro combina com a busca de texto (E lógico).
- [ ] Trocar de aba/estado mantém a seleção do dropdown.
- [ ] Funciona em Separado e Enviado (client_rows).

- [ ] **Step 3: Encerrar o dev server**

Run: `pkill -f "uvicorn main:app"`

---

## Notas

- **Não fazer push** — o usuário pediu pra aguardar aprovação dele.
- Os números de linha são referências do estado atual; confirmar o contexto antes de cada edit (o arquivo pode ter mudado entre tasks).
