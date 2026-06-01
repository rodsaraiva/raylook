# Quick Wins de Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Três correções cirúrgicas que eliminam trabalho desnecessário: filtro SQL em votos, debounce na busca e COUNT agregado no reconcile.

**Architecture:** Cada item é independente — um commit por item na branch `perf/otimizacoes`. Nenhuma mudança de interface ou comportamento visível. Item 1 tem teste unitário; item 2 é verificado no browser; item 3 via curl.

**Tech Stack:** Python 3.12 / FastAPI, vanilla JS, PostgREST 14, pytest, SQLite (testes).

---

## Arquivos modificados

| Arquivo | Item | Tipo |
|---------|------|------|
| `app/services/whatsapp_domain_service.py` | 1 | Modificar |
| `tests/unit/test_whatsapp_domain_services.py` | 1 | Modificar (novo teste) |
| `static/js/dashboard_v2.js` | 2 | Modificar |
| `main.py` | 3 | Modificar |

---

## Task 1: Filtro `status != "out"` no rebuild de votos

**Files:**
- Modify: `app/services/whatsapp_domain_service.py:458`
- Test: `tests/unit/test_whatsapp_domain_services.py`

- [ ] **Step 1: Escrever o teste que verifica que votos "out" não chegam ao rebuild**

Abrir `tests/unit/test_whatsapp_domain_services.py` e adicionar este teste dentro de `TestPackageServiceRebuild`, após o teste `test_sem_votos_retorna_zeros`:

```python
def test_votos_out_sao_ignorados_pelo_select(self):
    """Votos com status='out' não devem entrar no rebuild — filtro no SQL."""
    enquete = _make_enquete()
    enquete_with_join = {**enquete, "produtos": _make_produto()}
    votos = [
        _make_voto(id="v1", qty=6, status="out"),   # cancelado — deve ser ignorado
        _make_voto(id="v2", qty=6),                  # ativo — deve contar
    ]

    sb = FakeSB(_base_tables(
        enquetes=[enquete_with_join],
        produtos=[_make_produto()],
        votos=votos,
    ))
    svc = PackageService(sb)

    result = svc.rebuild_for_poll(_POLL_ID)

    # Só o voto ativo (qty=6) deve ser considerado
    assert result["open_qty"] == 6
```

Nota: `_make_voto` precisa aceitar o parâmetro `status`. Verificar sua assinatura atual no arquivo de testes:

```python
# Linha ~80-100, provavelmente:
def _make_voto(id="v-default", cliente_id="c1", alternativa_id="alt1",
               qty=6, voted_at=None, status="in"):
    ...
```

Se `status` não estiver como parâmetro, adicionar com default `"in"`.

- [ ] **Step 2: Rodar o teste para confirmar que falha**

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_services.py::TestPackageServiceRebuild::test_votos_out_sao_ignorados_pelo_select -v
```

Esperado: **FAIL** — o voto `"out"` ainda está chegando no rebuild porque o filtro não existe.

- [ ] **Step 3: Implementar o filtro**

Em `app/services/whatsapp_domain_service.py`, localizar a chamada de `select` de votos dentro de `rebuild_for_poll` (~linha 458).

Antes:
```python
votes = self.client.select(
    "votos",
    columns="id,cliente_id,alternativa_id,qty,voted_at,status",
    filters=[("enquete_id", "eq", enquete_id)],
)
```

Depois:
```python
votes = self.client.select(
    "votos",
    columns="id,cliente_id,alternativa_id,qty,voted_at,status",
    filters=[("enquete_id", "eq", enquete_id), ("status", "neq", "out")],
)
```

- [ ] **Step 4: Rodar todos os testes de rebuild para confirmar que passam**

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_services.py::TestPackageServiceRebuild -v
```

Esperado: todos **PASS**, incluindo o novo.

- [ ] **Step 5: Commit**

```bash
git add app/services/whatsapp_domain_service.py tests/unit/test_whatsapp_domain_services.py
git commit -m "perf(rebuild): filtra votos out no SQL ao invés de descartar em Python

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Debounce 300ms na busca do dashboard

**Files:**
- Modify: `static/js/dashboard_v2.js:864`

> Não há framework de testes JS neste projeto. Verificação é feita no browser.

- [ ] **Step 1: Localizar o handler de busca**

Em `static/js/dashboard_v2.js`, encontrar o bloco:

```javascript
document.getElementById("search").addEventListener("input", e => {
    search = e.target.value;
    listPage = 1;
    renderList();
});
```

- [ ] **Step 2: Aplicar o debounce**

Substituir o bloco acima por:

```javascript
let _searchTimer = null;
document.getElementById("search").addEventListener("input", e => {
    search = e.target.value;
    listPage = 1;
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => renderList(), 300);
});
```

A variável `_searchTimer` deve ser declarada no mesmo escopo do handler (escopo de módulo ou IIFE, onde já estão as outras variáveis como `search` e `listPage`).

- [ ] **Step 3: Iniciar servidor local e verificar no browser**

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true PYTHONPATH=. .venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Abrir `http://127.0.0.1:8000` no browser, ir até o campo de busca e digitar rapidamente "SHORT SAIA". Confirmar que a lista **não atualiza a cada tecla** — só atualiza ~300ms após parar de digitar.

- [ ] **Step 4: Commit**

```bash
git add static/js/dashboard_v2.js
git commit -m "perf(dashboard): debounce 300ms na busca evita re-renders por keystroke

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Reconcile com COUNT agregado no PostgREST

**Files:**
- Modify: `main.py:771`

- [ ] **Step 1: Verificar que PostgREST suporta `count()` na versão em uso**

```bash
docker exec $(docker ps --filter name=raylook_dashboard -q) \
  curl -s "http://postgrest:3000/enquetes?select=count()" \
  -H "Authorization: Bearer $(grep SUPABASE_SERVICE_KEY /proc/1/environ -a | tr '\0' '\n' | grep SUPABASE_SERVICE_KEY | cut -d= -f2)" \
  -H "Content-Type: application/json" | head -c 200
```

Esperado: `[{"count":"N"}]` onde N é o número de enquetes. Se retornar erro 400, a versão do PostgREST não suporta aggregate — neste caso **não implementar este item** e registrar o bloqueio.

- [ ] **Step 2: Implementar o helper e substituir os selects**

Em `main.py`, localizar o endpoint `reconcile_supabase_baserow` (~linha 771) e substituir o corpo:

Antes:
```python
@app.get("/api/reconcile/supabase-baserow")
async def reconcile_supabase_baserow():
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase domain disabled.")
    try:
        sb = SupabaseRestClient.from_settings()
        enquetes_sb = sb.select("enquetes", columns="id")
        votos_sb = sb.select("votos", columns="id")
        pacotes_sb = sb.select("pacotes", columns="id")
        vendas_sb = sb.select("vendas", columns="id")
        pagamentos_sb = sb.select("pagamentos", columns="id")
    except Exception as exc:
        logger.exception("reconcile failed")
        raise HTTPException(status_code=502, detail=f"Reconcile failed: {exc}")

    supabase_counts = {
        "enquetes": len(enquetes_sb) if isinstance(enquetes_sb, list) else 0,
        "votos": len(votos_sb) if isinstance(votos_sb, list) else 0,
        "pacotes": len(pacotes_sb) if isinstance(pacotes_sb, list) else 0,
        "vendas": len(vendas_sb) if isinstance(vendas_sb, list) else 0,
        "pagamentos": len(pagamentos_sb) if isinstance(pagamentos_sb, list) else 0,
    }
    return {"status": "ok", "supabase": supabase_counts, "baserow_comparison": "disabled_in_staging"}
```

Depois:
```python
@app.get("/api/reconcile/supabase-baserow")
async def reconcile_supabase_baserow():
    if not supabase_domain_enabled():
        raise HTTPException(status_code=503, detail="Supabase domain disabled.")

    def _pg_count(client: SupabaseRestClient, table: str) -> int:
        rows = client.select(table, columns="count()")
        return int((rows or [{}])[0].get("count", 0))

    try:
        sb = SupabaseRestClient.from_settings()
        supabase_counts = {
            "enquetes":   _pg_count(sb, "enquetes"),
            "votos":      _pg_count(sb, "votos"),
            "pacotes":    _pg_count(sb, "pacotes"),
            "vendas":     _pg_count(sb, "vendas"),
            "pagamentos": _pg_count(sb, "pagamentos"),
        }
    except Exception as exc:
        logger.exception("reconcile failed")
        raise HTTPException(status_code=502, detail=f"Reconcile failed: {exc}")

    return {"status": "ok", "supabase": supabase_counts, "baserow_comparison": "disabled_in_staging"}
```

- [ ] **Step 3: Verificar localmente que o endpoint retorna JSON correto**

```bash
curl -s http://127.0.0.1:8000/api/reconcile/supabase-baserow | python3 -m json.tool
```

Esperado (estrutura):
```json
{
    "status": "ok",
    "supabase": {
        "enquetes": 42,
        "votos": 1200,
        "pacotes": 380,
        "vendas": 95,
        "pagamentos": 110
    },
    "baserow_comparison": "disabled_in_staging"
}
```

Se o ambiente local usa SQLite (`DATA_BACKEND=sqlite`), o PostgREST não estará disponível e o endpoint retornará 503 — isso é esperado. Neste caso, a verificação real só é possível em staging/prod.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "perf(reconcile): substitui SELECT completo por count() agregado no PostgREST

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Push e verificação final

- [ ] **Step 1: Rodar suite completa de testes**

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v --tb=short 2>&1 | tail -20
```

Esperado: todos os testes passam.

- [ ] **Step 2: Push da branch**

```bash
git push origin perf/otimizacoes
```

- [ ] **Step 3: Confirmar commits na branch**

```bash
git log main..perf/otimizacoes --oneline
```

Esperado (4 commits):
```
<sha> perf(reconcile): substitui SELECT completo por count() agregado no PostgREST
<sha> perf(dashboard): debounce 300ms na busca evita re-renders por keystroke
<sha> perf(rebuild): filtra votos out no SQL ao invés de descartar em Python
<sha> docs: spec dos 3 quick wins de performance
581f550 perf(rebuild): busca pacote_clientes de aprovados com IN ao invés de loop
```
