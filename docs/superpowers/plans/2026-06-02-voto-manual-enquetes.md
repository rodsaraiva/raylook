# Voto Manual em Enquetes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar botão "＋ Adicionar Voto" no painel de detalhe de enquetes, com modal de busca/cadastro de cliente e seletor de qty, criando o voto via novo endpoint backend com `synthetic=1`.

**Architecture:** Novo endpoint `POST /api/dashboard/enquetes/{enquete_id}/votos` em `app/routers/dashboard.py` faz find-or-create de cliente, upsert do voto com `synthetic=1` e chama `rebuild_for_poll`. Frontend em `enquetes.js` adiciona botão no `renderDetail()` e modal vanilla JS. Nenhuma migration de schema necessária (`synthetic` já existe na tabela `votos`).

**Tech Stack:** Python 3.12 / FastAPI, vanilla JS, PostgREST 14, pytest + FakeSupabaseClient (SQLite em testes).

---

## Arquivos modificados / criados

| Arquivo | Tipo |
|---------|------|
| `tests/unit/test_add_voto_manual.py` | Criar |
| `app/routers/dashboard.py` | Modificar (adicionar endpoint ao final) |
| `static/js/enquetes.js` | Modificar (botão + modal) |

---

## Task 1: Backend — endpoint `POST /enquetes/{enquete_id}/votos`

**Files:**
- Create: `tests/unit/test_add_voto_manual.py`
- Modify: `app/routers/dashboard.py` (final do arquivo, após linha 2017)

### Contexto para o implementador

O router de dashboard usa `SupabaseRestClient.from_settings()` para acessar o banco. Em testes, patchamos esse método com `FakeSupabaseClient` (ver `tests/_helpers/fake_supabase.py`).

Funções auxiliares úteis:
- `_normalize_phone(phone: str) -> str` — remove não-dígitos — em `app/services/portal_service.py`
- `_phone_variants(normalized: str) -> List[str]` — gera variações com/sem DDI 55 — em `app/services/portal_service.py`
- `_sanitize_name(name: str) -> str` — limpa espaços e caracteres invisíveis — em `app/services.whatsapp_domain_service`
- `PackageService(client).rebuild_for_poll(enquete_id)` — reconstrói pacotes — em `app/services/whatsapp_domain_service.py`

Valores válidos de qty (restrição do banco): `3, 4, 6, 8, 9, 12, 16, 20, 24`

A tabela `votos` tem unique index em `(enquete_id, cliente_id)` — um cliente vota uma vez por enquete.

Testes rodam com `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_add_voto_manual.py -v`.

---

- [ ] **Step 1: Criar arquivo de teste com imports e helpers**

Crie `tests/unit/test_add_voto_manual.py`:

```python
"""Testes para POST /api/dashboard/enquetes/{enquete_id}/votos."""
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import main as main_module
from tests._helpers.fake_supabase import FakeSupabaseClient, install_fake


def _client_strict():
    return TestClient(main_module.app)


def _client():
    return TestClient(main_module.app, raise_server_exceptions=False)


ENQ_ID = "enq-001"
CLI_ID = "cli-001"

_BASE_ENQUETE = {
    "id": ENQ_ID, "titulo": "Short Saia", "status": "open",
    "produto_id": None, "created_at": "2026-06-02T10:00:00+00:00",
}

_BASE_CLIENTE = {
    "id": CLI_ID, "nome": "Maria Silva", "celular": "62999991234",
}
```

- [ ] **Step 2: Escrever teste — cliente existente encontrado por nome**

Acrescente ao `tests/unit/test_add_voto_manual.py`:

```python
def test_add_voto_manual_cliente_existente_por_nome(monkeypatch):
    """POST /enquetes/{id}/votos com cliente encontrado pelo nome cria voto synthetic=1."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [_BASE_CLIENTE],
        "votos": [],
        "enquete_alternativas": [],
        "pacotes": [],
        "pacote_clientes": [],
        "vendas": [],
        "pagamentos": [],
    })
    install_fake(monkeypatch, fake)

    with patch("app.services.whatsapp_domain_service.PackageService.rebuild_for_poll", return_value={"ok": True}):
        resp = _client_strict().post(
            f"/api/dashboard/enquetes/{ENQ_ID}/votos",
            json={"busca": "maria", "qty": 6},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cliente"]["id"] == CLI_ID

    votos = fake.tables["votos"]
    assert len(votos) == 1
    assert votos[0]["qty"] == 6
    assert votos[0]["status"] == "in"
    assert votos[0]["synthetic"] == 1
    assert votos[0]["enquete_id"] == ENQ_ID
    assert votos[0]["cliente_id"] == CLI_ID
```

- [ ] **Step 3: Escrever teste — cliente não encontrado, cria novo**

Acrescente ao `tests/unit/test_add_voto_manual.py`:

```python
def test_add_voto_manual_cria_cliente(monkeypatch):
    """POST /enquetes/{id}/votos com cliente inexistente e nome+celular cria cliente e voto."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [],
        "votos": [],
        "enquete_alternativas": [],
        "pacotes": [],
        "pacote_clientes": [],
        "vendas": [],
        "pagamentos": [],
    })
    install_fake(monkeypatch, fake)

    with patch("app.services.whatsapp_domain_service.PackageService.rebuild_for_poll", return_value={"ok": True}):
        resp = _client_strict().post(
            f"/api/dashboard/enquetes/{ENQ_ID}/votos",
            json={"busca": "Ana Paula", "qty": 6, "nome": "Ana Paula", "celular": "62988887777"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cliente"]["nome"] == "Ana Paula"

    clientes = fake.tables["clientes"]
    assert len(clientes) == 1
    assert clientes[0]["nome"] == "Ana Paula"

    votos = fake.tables["votos"]
    assert len(votos) == 1
    assert votos[0]["synthetic"] == 1
    assert votos[0]["status"] == "in"
```

- [ ] **Step 4: Escrever teste — cliente não encontrado sem nome/celular retorna found=false**

Acrescente ao `tests/unit/test_add_voto_manual.py`:

```python
def test_add_voto_manual_nao_encontrado_sem_dados(monkeypatch):
    """POST /enquetes/{id}/votos sem cliente e sem nome/celular retorna found=False."""
    fake = FakeSupabaseClient({
        "enquetes": [_BASE_ENQUETE],
        "clientes": [],
        "votos": [],
    })
    install_fake(monkeypatch, fake)

    resp = _client_strict().post(
        f"/api/dashboard/enquetes/{ENQ_ID}/votos",
        json={"busca": "nao existe", "qty": 6},
    )

    assert resp.status_code == 200
    assert resp.json() == {"found": False}
    assert fake.tables.get("votos", []) == []


def test_add_voto_manual_qty_invalida(monkeypatch):
    """qty fora dos valores permitidos retorna 400."""
    fake = FakeSupabaseClient({"enquetes": [_BASE_ENQUETE], "clientes": []})
    install_fake(monkeypatch, fake)

    resp = _client().post(
        f"/api/dashboard/enquetes/{ENQ_ID}/votos",
        json={"busca": "maria", "qty": 7},
    )
    assert resp.status_code == 400


def test_add_voto_manual_enquete_nao_encontrada(monkeypatch):
    """enquete_id inexistente retorna 404."""
    fake = FakeSupabaseClient({"enquetes": [], "clientes": []})
    install_fake(monkeypatch, fake)

    resp = _client().post(
        "/api/dashboard/enquetes/nao-existe/votos",
        json={"busca": "maria", "qty": 6},
    )
    assert resp.status_code == 404
```

- [ ] **Step 5: Rodar testes — confirmar que falham**

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_add_voto_manual.py -v 2>&1 | tail -20
```

Esperado: todos **FAIL** ou **ERROR** — endpoint não existe ainda.

- [ ] **Step 6: Implementar o endpoint em `app/routers/dashboard.py`**

Adicione ao final de `app/routers/dashboard.py` (após a última linha):

```python
@router.post("/enquetes/{enquete_id}/votos")
def add_voto_manual(enquete_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Adiciona voto manualmente a uma enquete. synthetic=1 para auditoria."""
    VALID_QTY = {3, 4, 6, 8, 9, 12, 16, 20, 24}
    qty = body.get("qty")
    busca = (body.get("busca") or "").strip()
    nome = (body.get("nome") or "").strip()
    celular = (body.get("celular") or "").strip()

    if qty not in VALID_QTY:
        raise HTTPException(400, f"qty deve ser um de: {sorted(VALID_QTY)}")
    if not busca:
        raise HTTPException(400, "busca é obrigatório")

    client = SupabaseRestClient.from_settings()

    enq = client.select("enquetes", filters=[("id", "eq", enquete_id)], single=True)
    if not enq:
        raise HTTPException(404, "Enquete não encontrada")

    from app.services.portal_service import _normalize_phone, _phone_variants
    from app.services.whatsapp_domain_service import _sanitize_name

    all_clientes = client.select("clientes") or []
    busca_lower = busca.lower()
    normalized_busca = _normalize_phone(busca)
    phone_variants = set(_phone_variants(normalized_busca)) if normalized_busca else set()

    cliente = None
    for c in all_clientes:
        c_phone = _normalize_phone(c.get("celular") or "")
        c_nome = (c.get("nome") or "").lower()
        if (phone_variants and c_phone in phone_variants) or busca_lower in c_nome:
            cliente = c
            break

    if not cliente:
        if not nome or not celular:
            return {"found": False}
        new_cli = client.insert("clientes", {
            "nome": _sanitize_name(nome),
            "celular": _normalize_phone(celular),
        })
        cliente = new_cli[0] if isinstance(new_cli, list) else new_cli

    now = client.now_iso()
    existing_voto = client.select(
        "votos",
        filters=[("enquete_id", "eq", enquete_id), ("cliente_id", "eq", cliente["id"])],
        single=True,
    )
    if existing_voto:
        client.update("votos", {
            "qty": qty, "status": "in", "synthetic": 1, "voted_at": now,
        }, filters=[("id", "eq", existing_voto["id"])])
        voto_id = existing_voto["id"]
    else:
        voto_row = client.insert("votos", {
            "enquete_id": enquete_id,
            "cliente_id": cliente["id"],
            "alternativa_id": None,
            "qty": qty,
            "status": "in",
            "synthetic": 1,
            "voted_at": now,
        })
        voto = voto_row[0] if isinstance(voto_row, list) else voto_row
        voto_id = voto["id"]

    package_result = None
    try:
        from app.services.whatsapp_domain_service import PackageService
        package_result = PackageService(client).rebuild_for_poll(enquete_id)
    except Exception:
        logger.exception("rebuild_for_poll falhou após voto manual enquete=%s", enquete_id)

    return {
        "status": "ok",
        "voto_id": voto_id,
        "cliente": {"id": cliente["id"], "nome": cliente.get("nome"), "celular": cliente.get("celular")},
        "package_result": package_result,
    }
```

- [ ] **Step 7: Rodar testes — confirmar que passam**

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_add_voto_manual.py -v 2>&1 | tail -20
```

Esperado: todos **PASS**.

- [ ] **Step 8: Rodar suite completa para checar regressões**

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v --tb=short 2>&1 | tail -20
```

Esperado: apenas as 5 falhas pré-existentes de `test_login_*` — nenhuma nova falha.

- [ ] **Step 9: Commit**

```bash
git add tests/unit/test_add_voto_manual.py app/routers/dashboard.py
git commit -m "$(cat <<'EOF'
feat(enquetes): endpoint POST /enquetes/{id}/votos para voto manual (synthetic=1)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Frontend — botão e modal em `enquetes.js`

**Files:**
- Modify: `static/js/enquetes.js`

### Contexto para o implementador

O arquivo `enquetes.js` é um IIFE vanilla JS. A função `renderDetail()` gera o HTML do painel direito. O trecho relevante está na linha onde `detail.innerHTML` é atribuído — o botão deve ser inserido entre `.enq-stats` e `.enq-pacotes-list`.

O endpoint de busca de clientes já existe: `GET /api/dashboard/clientes?q=<texto>` — retorna `[{id, nome, celular}]`.

O novo endpoint: `POST /api/dashboard/enquetes/{enquete_id}/votos` — body `{busca, qty, nome?, celular?}`.

Fluxo do modal:
1. Campo busca com debounce 300ms → chama `/api/dashboard/clientes?q=...`
2. Encontrou → exibe chip verde com nome+celular
3. Não encontrou → exibe campos extras de nome e celular
4. Chips de qty: `3 4 6 8 9 12 16 20 24` — um deve estar selecionado
5. Submit → POST para o endpoint. Se `found: false`, exibe campos de cadastro. Se `status: ok`, fecha modal e chama `loadDetail(state.selectedId)`

---

- [ ] **Step 1: Adicionar estilos do botão e modal**

No início de `renderDetail()` (ou num `<style>` injetado uma vez), estes estilos serão aplicados via classes CSS inline no HTML gerado. Não há arquivo CSS separado — o projeto usa estilos inline ou classes já definidas em `templates/index.html`. Como `enquetes.js` injeta HTML, adicione um bloco de estilo **uma vez** no `openEnquetes()`:

Localize a função `openEnquetes()` em `enquetes.js` e acrescente ao final, antes do `refresh()`:

```javascript
function openEnquetes() {
    // ... código existente ...

    // injeta estilos do modal de voto manual (idempotente)
    if (!document.getElementById("enq-voto-styles")) {
        const s = document.createElement("style");
        s.id = "enq-voto-styles";
        s.textContent = `
            .btn-add-voto{width:100%;padding:9px 16px;background:var(--surface,#313244);
              border:1px dashed var(--border,#585b70);border-radius:8px;
              color:var(--accent,#89b4fa);font-size:13px;font-weight:600;cursor:pointer;
              display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:12px;}
            .btn-add-voto:hover{border-color:var(--accent,#89b4fa);opacity:.85;}
            #enq-voto-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);
              display:flex;align-items:center;justify-content:center;z-index:9999;}
            .enq-voto-modal{background:var(--bg-card,#1e1e2e);border:1px solid var(--border,#313244);
              border-radius:12px;padding:20px;width:340px;max-width:90vw;}
            .enq-voto-modal h4{margin:0 0 14px;font-size:15px;color:var(--text,#cdd6f4);}
            .enq-voto-lbl{font-size:11px;color:var(--text-muted,#6c7086);
              text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}
            .enq-voto-input{width:100%;padding:8px 10px;background:var(--surface,#313244);
              border:1px solid var(--border,#45475a);border-radius:6px;
              color:var(--text,#cdd6f4);font-size:13px;box-sizing:border-box;outline:none;margin-bottom:10px;}
            .enq-voto-input:focus{border-color:var(--accent,#89b4fa);}
            .enq-voto-found{background:var(--surface,#313244);border-radius:6px;
              padding:8px 10px;font-size:12px;color:#a6e3a1;margin-bottom:10px;display:flex;gap:6px;}
            .enq-voto-new{background:rgba(249,226,175,.05);border:1px dashed #f9e2af;
              border-radius:6px;padding:10px;margin-bottom:10px;}
            .enq-voto-new .warn{font-size:11px;color:#f9e2af;margin-bottom:8px;}
            .enq-voto-qty-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;}
            .enq-voto-qty-chip{padding:4px 10px;border-radius:6px;font-size:12px;cursor:pointer;
              background:var(--surface,#313244);border:1px solid var(--border,#45475a);
              color:var(--text,#cdd6f4);}
            .enq-voto-qty-chip.selected{background:var(--accent,#89b4fa);
              border-color:var(--accent,#89b4fa);color:#1e1e2e;font-weight:700;}
            .enq-voto-footer{display:flex;gap:8px;justify-content:flex-end;}
            .enq-voto-cancel{padding:7px 14px;background:transparent;
              border:1px solid var(--border,#45475a);border-radius:6px;
              color:var(--text-muted,#6c7086);font-size:12px;cursor:pointer;}
            .enq-voto-confirm{padding:7px 14px;background:var(--accent,#89b4fa);
              border:none;border-radius:6px;color:#1e1e2e;font-size:12px;
              font-weight:700;cursor:pointer;}
            .enq-voto-confirm:disabled{opacity:.4;cursor:default;}
            .enq-voto-error{font-size:12px;color:#f87171;margin-bottom:10px;}
        `;
        document.head.appendChild(s);
    }

    refresh();
}
```

- [ ] **Step 2: Adicionar botão ao `renderDetail()`**

Localize em `renderDetail()` a linha que contém `<div class="enq-pacotes-list">` dentro da string de `detail.innerHTML`. Adicione o botão imediatamente antes dessa div:

```javascript
// Antes (dentro da template string de detail.innerHTML):
            <div class="enq-pacotes-list">${pacotesHtml}</div>

// Depois:
            <button type="button" class="btn-add-voto" id="enq-add-voto-btn">＋ Adicionar Voto</button>
            <div class="enq-pacotes-list">${pacotesHtml}</div>
```

Depois, ao final de `renderDetail()`, após a atribuição do `detail.innerHTML`, adicione o listener do botão:

```javascript
    detail.innerHTML = `...`;  // linha existente — não mudar

    // listener do botão de voto manual
    detail.querySelector("#enq-add-voto-btn")?.addEventListener("click", () => openVotoModal());
```

- [ ] **Step 3: Implementar `openVotoModal()` e `_closeVotoModal()`**

Adicione estas funções logo antes do bloco `// ---- handlers ----` em `enquetes.js`:

```javascript
    // ---- modal voto manual ----

    function openVotoModal() {
        if (document.getElementById("enq-voto-modal-overlay")) return;
        let selectedCliente = null;
        let selectedQty = null;
        let searchTimer = null;

        const overlay = document.createElement("div");
        overlay.id = "enq-voto-modal-overlay";
        overlay.innerHTML = `
            <div class="enq-voto-modal">
                <h4>Adicionar Voto</h4>
                <div class="enq-voto-lbl">Buscar cliente (nome ou telefone)</div>
                <input class="enq-voto-input" id="enq-voto-busca" placeholder="Nome ou celular..." autocomplete="off">
                <div id="enq-voto-busca-result"></div>
                <div class="enq-voto-lbl">Quantidade de peças</div>
                <div class="enq-voto-qty-chips" id="enq-voto-qty-chips">
                    ${[3,4,6,8,9,12,16,20,24].map(q =>
                        `<button type="button" class="enq-voto-qty-chip" data-qty="${q}">${q}</button>`
                    ).join("")}
                </div>
                <div id="enq-voto-error" class="enq-voto-error" style="display:none"></div>
                <div class="enq-voto-footer">
                    <button type="button" class="enq-voto-cancel" id="enq-voto-cancel">Cancelar</button>
                    <button type="button" class="enq-voto-confirm" id="enq-voto-confirm" disabled>Confirmar</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        const buscaInput = overlay.querySelector("#enq-voto-busca");
        const resultDiv = overlay.querySelector("#enq-voto-busca-result");
        const confirmBtn = overlay.querySelector("#enq-voto-confirm");
        const errorDiv = overlay.querySelector("#enq-voto-error");

        function updateConfirm() {
            const hasCliente = selectedCliente !== null;
            const hasQty = selectedQty !== null;
            const newClientFields = overlay.querySelector("#enq-voto-new-nome");
            const newNome = newClientFields ? (overlay.querySelector("#enq-voto-new-nome").value || "").trim() : null;
            const newCelular = newClientFields ? (overlay.querySelector("#enq-voto-new-celular").value || "").trim() : null;
            const canSubmit = hasQty && (hasCliente || (newNome && newCelular));
            confirmBtn.disabled = !canSubmit;
            confirmBtn.textContent = (hasCliente || !newClientFields) ? "Confirmar" : "Criar e Votar";
        }

        // qty chips
        overlay.querySelectorAll(".enq-voto-qty-chip").forEach(chip => {
            chip.addEventListener("click", () => {
                overlay.querySelectorAll(".enq-voto-qty-chip").forEach(c => c.classList.remove("selected"));
                chip.classList.add("selected");
                selectedQty = parseInt(chip.dataset.qty, 10);
                updateConfirm();
            });
        });

        // busca com debounce
        buscaInput.addEventListener("input", () => {
            clearTimeout(searchTimer);
            const q = buscaInput.value.trim();
            if (!q) {
                resultDiv.innerHTML = "";
                selectedCliente = null;
                updateConfirm();
                return;
            }
            searchTimer = setTimeout(async () => {
                try {
                    const r = await fetch(`/api/dashboard/clientes?q=${encodeURIComponent(q)}`, { credentials: "same-origin" });
                    const data = await r.json();
                    const found = data[0] || null;
                    if (found) {
                        selectedCliente = found;
                        resultDiv.innerHTML = `<div class="enq-voto-found">✓ ${escape(found.nome || "")} · ${escape(found.celular || "")}</div>`;
                    } else {
                        selectedCliente = null;
                        resultDiv.innerHTML = `
                            <div class="enq-voto-new">
                                <div class="warn">⚠ Cliente não encontrado. Preencha para cadastrar:</div>
                                <div class="enq-voto-lbl">Nome completo</div>
                                <input class="enq-voto-input" id="enq-voto-new-nome" value="${escape(q)}">
                                <div class="enq-voto-lbl">Celular</div>
                                <input class="enq-voto-input" id="enq-voto-new-celular" placeholder="Ex: 62999991234">
                            </div>`;
                        overlay.querySelector("#enq-voto-new-nome")?.addEventListener("input", updateConfirm);
                        overlay.querySelector("#enq-voto-new-celular")?.addEventListener("input", updateConfirm);
                    }
                } catch (_) {
                    resultDiv.innerHTML = "";
                    selectedCliente = null;
                }
                updateConfirm();
            }, 300);
        });

        // submit
        confirmBtn.addEventListener("click", async () => {
            confirmBtn.disabled = true;
            errorDiv.style.display = "none";
            const q = buscaInput.value.trim();
            const body = { busca: q, qty: selectedQty };
            if (!selectedCliente) {
                body.nome = (overlay.querySelector("#enq-voto-new-nome")?.value || "").trim();
                body.celular = (overlay.querySelector("#enq-voto-new-celular")?.value || "").trim();
            }
            try {
                const r = await fetch(`/api/dashboard/enquetes/${encodeURIComponent(state.selectedId)}/votos`, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                const data = await r.json();
                if (!r.ok) {
                    errorDiv.textContent = data.detail || `Erro ${r.status}`;
                    errorDiv.style.display = "";
                    confirmBtn.disabled = false;
                    return;
                }
                if (data.found === false) {
                    errorDiv.textContent = "Cliente não encontrado. Preencha nome e celular.";
                    errorDiv.style.display = "";
                    confirmBtn.disabled = false;
                    return;
                }
                _closeVotoModal();
                loadDetail(state.selectedId);
            } catch (e) {
                errorDiv.textContent = `Erro: ${e.message}`;
                errorDiv.style.display = "";
                confirmBtn.disabled = false;
            }
        });

        overlay.querySelector("#enq-voto-cancel").addEventListener("click", _closeVotoModal);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) _closeVotoModal(); });
        buscaInput.focus();
    }

    function _closeVotoModal() {
        document.getElementById("enq-voto-modal-overlay")?.remove();
    }
```

- [ ] **Step 4: Verificar que `escape()` está disponível no escopo**

A função `escape()` já está definida no início do IIFE em `enquetes.js` (linha ~20). Não é necessário redeclará-la — o modal usa a mesma função.

- [ ] **Step 5: Iniciar servidor local e testar no browser**

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true PYTHONPATH=. .venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Abrir `http://127.0.0.1:8000`, navegar para a aba Enquetes, selecionar uma enquete.

Verificar:
1. Botão "＋ Adicionar Voto" aparece entre os stats e a lista de pacotes
2. Clicar no botão abre o modal
3. Digitar um nome/telefone existente → aparece chip verde com o nome
4. Digitar algo inexistente → aparecem campos de nome e celular
5. Selecionar qty → botão "Confirmar" habilita
6. Confirmar → modal fecha e painel recarrega

- [ ] **Step 6: Commit**

```bash
git add static/js/enquetes.js
git commit -m "$(cat <<'EOF'
feat(enquetes): botão e modal de voto manual com busca e seletor de qty

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Push e verificação final

- [ ] **Step 1: Rodar suite completa de testes**

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v --tb=short 2>&1 | tail -25
```

Esperado: novos testes passam. Apenas as 5 falhas pré-existentes de `test_login_*` são aceitáveis.

- [ ] **Step 2: Push da branch**

```bash
git push origin HEAD
```

- [ ] **Step 3: Confirmar commits**

```bash
git log --oneline -5
```

Esperado (2 novos commits):
```
<sha> feat(enquetes): botão e modal de voto manual com busca e seletor de qty
<sha> feat(enquetes): endpoint POST /enquetes/{id}/votos para voto manual (synthetic=1)
```
