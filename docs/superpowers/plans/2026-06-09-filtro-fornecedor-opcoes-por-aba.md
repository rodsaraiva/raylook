# Opções do Filtro de Fornecedor por Aba — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o dropdown de fornecedor listar apenas os fornecedores presentes na aba/estado ativo (mais "Todos" e "Sem fornecedor"), atualizando ao trocar de aba.

**Architecture:** Mudança só de frontend. `populateFornecedorFilter()` deixa de buscar a lista cadastrada (`L.fetchFornecedores()`) e passa a derivar as opções de `currentItems()` (itens da aba ativa). É invocada no início de `render()` (em vez de só no `load()`), para refletir a aba atual a cada troca. Sincroniza `fornecedorFilter` quando a opção selecionada some da aba.

**Tech Stack:** JS vanilla (`static/js/dashboard_v2.js`), Jinja2 (`templates/dashboard_v2.html`). Sem test runner JS — validação no browser.

---

### Task 1: Derivar opções da aba ativa e invocar no render

**Files:**
- Modify: `static/js/dashboard_v2.js` (`populateFornecedorFilter` ~linha 205; `load()` ~linha 231-232; `render()` ~linha 917)
- Modify: `templates/dashboard_v2.html` (cache-bust ~linha 1391)

LEIA o arquivo antes de editar pra confirmar as linhas (podem ter mudado).

- [ ] **Step 1: Reescrever `populateFornecedorFilter` (síncrona, a partir de `currentItems()`)**

Substituir a função atual (linhas ~205-214):

```javascript
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

por:

```javascript
    // Opções = fornecedores presentes nos itens da aba ativa (distintos, ordenados),
    // mais "Todos" e "Sem fornecedor". Mantém a seleção se ela ainda existir na aba;
    // senão reseta pra "Todos" e sincroniza fornecedorFilter.
    function populateFornecedorFilter() {
        const sel = document.getElementById("fornecedor-filter");
        if (!sel) return;
        const presentes = [...new Set(currentItems()
            .map(p => (p.fornecedor || "").trim())
            .filter(Boolean))]
            .sort((a, b) => a.localeCompare(b, "pt-BR", { sensitivity: "base" }));
        const atual = sel.value;
        sel.innerHTML = `<option value="">Todos os fornecedores</option>`
            + presentes.map(f => `<option value="${L.escapeHtml(f)}">${L.escapeHtml(f)}</option>`).join("")
            + `<option value="__none__">Sem fornecedor</option>`;
        const disponiveis = new Set(["", "__none__", ...presentes]);
        sel.value = disponiveis.has(atual) ? atual : "";
        fornecedorFilter = sel.value;
    }
```

- [ ] **Step 2: Remover a chamada redundante no `load()`**

No `load()` (linhas ~231-232), remover a linha `populateFornecedorFilter();` que vem depois de `render();`:

```javascript
        render();
        populateFornecedorFilter();
    }
```

vira:

```javascript
        render();
    }
```

- [ ] **Step 3: Invocar `populateFornecedorFilter()` no início de `render()`**

Trocar (linha ~917):

```javascript
    function render() { renderRail(); renderList(); renderDetail(); }
```

por:

```javascript
    function render() { populateFornecedorFilter(); renderRail(); renderList(); renderDetail(); }
```

(Deve vir ANTES de `renderList()` porque sincroniza `fornecedorFilter`, que o `renderList` consome.)

- [ ] **Step 4: Bump do cache-bust**

Em `templates/dashboard_v2.html` (linha ~1391), trocar `?v=20260609` por `?v=20260609b`:

Run: `grep -n "dashboard_v2.js?v=" templates/dashboard_v2.html`

- [ ] **Step 5: Verificação de sintaxe**

Run: `node --check static/js/dashboard_v2.js`
Expected: sem saída (OK). Se `node` não existir, pule.

Confirme também que `L.fetchFornecedores` não é mais referenciado em `populateFornecedorFilter` (mas continua sendo usado pelo modal `promptFornecedor` — não remover o helper):

Run: `grep -n "fetchFornecedores" static/js/dashboard_v2.js`
Expected: só a ocorrência dentro de `promptFornecedor` (~linha 123), nenhuma em `populateFornecedorFilter`.

- [ ] **Step 6: Commit**

```bash
cd /root/rodrigo/raylook
git add static/js/dashboard_v2.js templates/dashboard_v2.html
git commit -m "feat(dashboard): filtro de fornecedor lista só opções da aba ativa"
```

Não fazer push (aguardar aprovação do usuário).

---

### Task 2: Validação no browser

Sem test runner JS — UI validada no browser (regra do projeto). Setup cirúrgico: porta livre 8123, bind 127.0.0.1, matar SÓ pelo PID exato (nunca `pkill -f` genérico — porta 8000 é do portainer; outros projetos rodam `uvicorn main:app`).

**Files:** nenhum (validação manual)

- [ ] **Step 1: Backup + seed do SQLite local**

```bash
cd /root/rodrigo/raylook
cp data/raylook.db /tmp/raylook.db.bak-opcoes
.venv/bin/python3 -c "
import sqlite3
c=sqlite3.connect('data/raylook.db')
c.execute(\"UPDATE pacotes SET fornecedor='Acme Têxtil' WHERE id IN ('66c3c44f-276e-46e2-b72a-6be8b358dd1e','c42cdf3f-2d3c-4b0f-add9-32ec3128e930')\")
c.execute(\"UPDATE enquetes SET fornecedor='Acme Têxtil' WHERE id='1bdcc571-366c-406f-8ab6-66db2c377b8c'\")
c.execute(\"UPDATE pacotes SET fornecedor='Boa Malha' WHERE id='3b4832d5-21e8-4373-9fb3-82d177d180c9'\")
c.execute(\"UPDATE enquetes SET fornecedor='Boa Malha' WHERE id='70a7eefa-ea47-4264-9867-8e54c5f99dd4'\")
c.commit(); print('seeded')
"
```

Seed: `Acme Têxtil` em 2 pacotes **approved** (aparecem em Separado/Enviado), `Boa Malha` em 1 pacote **open** (Aberto).

- [ ] **Step 2: Subir o dev server na 8123 (PID rastreado)**

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true DATA_BACKEND=sqlite RAYLOOK_SANDBOX=true nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8123 > /tmp/raylook_dev8123.log 2>&1 &
echo $! > /tmp/raylook_dev8123.pid
sleep 5
curl -s -o /dev/null -w "/ HTTP %{http_code}\n" http://127.0.0.1:8123/
```

- [ ] **Step 3: Validar no browser (Playwright MCP em `http://127.0.0.1:8123/`)**

Trocar o filtro de data pra "Todos" (`.filter-pill[data-filter="all"]`) pra carregar os dados antigos. Checklist:
- [ ] Aba **Aberto**: dropdown lista só `Boa Malha` (+ Todos + Sem fornecedor) — NÃO mostra `Acme Têxtil`.
- [ ] Trocar pra **Enviado**: dropdown atualiza e lista só `Acme Têxtil` (+ Todos + Sem fornecedor) — NÃO mostra `Boa Malha`.
- [ ] Filtrar por um fornecedor reduz a lista corretamente; `Sem fornecedor` mostra os sem fornecedor; combina em E com a busca de texto.
- [ ] Selecionar `Acme Têxtil` em Enviado e trocar pra **Aberto** → dropdown reseta pra `Todos os fornecedores` (Acme não existe em Aberto), e a lista de Aberto aparece completa.

- [ ] **Step 4: Cleanup (PID exato + restore do DB + artefatos)**

```bash
cd /root/rodrigo/raylook
MYPID=$(cat /tmp/raylook_dev8123.pid 2>/dev/null)
[ -n "$MYPID" ] && kill "$MYPID" 2>/dev/null; sleep 1; [ -n "$MYPID" ] && kill -9 "$MYPID" 2>/dev/null
cp /tmp/raylook.db.bak-opcoes data/raylook.db
rm -rf .playwright-mcp *.png
```

---

## Notas

- **Não fazer push** sem aprovação do usuário (branch já está no remote; o push final é decisão dele).
- **Nunca** usar `pkill -f "uvicorn"` — derruba outros projetos e a prod do raylook (incidente já ocorrido nesta sessão). Sempre matar pelo PID exato salvo em `/tmp/raylook_dev8123.pid`.
- Números de linha são do estado atual; confirmar contexto antes de cada edit.
