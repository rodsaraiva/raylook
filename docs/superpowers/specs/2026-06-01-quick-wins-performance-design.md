# Quick Wins de Performance — Design Spec

**Data:** 2026-06-01  
**Branch:** `perf/otimizacoes`  
**Status:** Aprovado

---

## Objetivo

Três mudanças cirúrgicas independentes que reduzem trabalho desnecessário sem alterar comportamento visível. Cada item vira um commit separado na branch `perf/otimizacoes`.

---

## Item 1 — Filtro de status no rebuild de pacotes

**Arquivo:** `app/services/whatsapp_domain_service.py:458`

**Problema:** `rebuild_for_poll()` busca todos os votos de uma enquete e descarta os `status = "out"` em Python. Numa enquete com muitos votos cancelados, transfere dados desnecessários da DB.

**Solução:** Adicionar `("status", "neq", "out")` nos filtros do SELECT.

```python
# Antes
votes = self.client.select(
    "votos",
    columns="id,cliente_id,alternativa_id,qty,voted_at,status",
    filters=[("enquete_id", "eq", enquete_id)],
)

# Depois
votes = self.client.select(
    "votos",
    columns="id,cliente_id,alternativa_id,qty,voted_at,status",
    filters=[("enquete_id", "eq", enquete_id), ("status", "neq", "out")],
)
```

O filtro Python na linha 468 permanece como defesa dupla.

**Risco:** Zero. `neq` é suportado pelo cliente PostgREST. Comportamento downstream idêntico.

---

## Item 2 — Debounce na busca do dashboard

**Arquivo:** `static/js/dashboard_v2.js:864`

**Problema:** O handler de `input` na busca chama `renderList()` a cada keystroke. Digitando 13 caracteres = 13 re-renders do DOM completo.

**Solução:** Debounce de 300ms com `setTimeout`/`clearTimeout` vanilla JS.

```javascript
// Antes
document.getElementById("search").addEventListener("input", e => {
    search = e.target.value;
    listPage = 1;
    renderList();
});

// Depois
let _searchTimer = null;
document.getElementById("search").addEventListener("input", e => {
    search = e.target.value;
    listPage = 1;
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => renderList(), 300);
});
```

**Risco:** Mínimo. Delay de 300ms imperceptível ao usuário. Nenhuma outra parte do código depende do timing de `renderList`.

---

## Item 3 — Reconcile com COUNT agregado

**Arquivo:** `main.py:771`

**Problema:** O endpoint `GET /api/reconcile/supabase-baserow` faz 5 SELECTs completos (`columns="id"`) em tabelas que podem ter dezenas de milhares de linhas, só para contar quantas existem com `len()` em Python.

**Solução:** Usar `select=count()` do PostgREST (aggregate nativo), que retorna `[{"count": N}]` sem transferir dados.

```python
# Helper local no endpoint
def _pg_count(client, table: str) -> int:
    rows = client.select(table, columns="count()")
    return int((rows or [{}])[0].get("count", 0))

supabase_counts = {
    "enquetes":   _pg_count(sb, "enquetes"),
    "votos":      _pg_count(sb, "votos"),
    "pacotes":    _pg_count(sb, "pacotes"),
    "vendas":     _pg_count(sb, "vendas"),
    "pagamentos": _pg_count(sb, "pagamentos"),
}
```

**Risco:** Baixo. Endpoint admin-only. Output JSON idêntico. Verificar que a versão do PostgREST em uso suporta `count()` como aggregate antes de commitar.

---

## Verificação

| Item | Como verificar |
|------|---------------|
| 1 | Logs do rebuild mostram menos votos retornados quando há votos `"out"` na enquete |
| 2 | No browser: digitar busca rápida e confirmar que a lista só atualiza após parar de digitar |
| 3 | `GET /api/reconcile/supabase-baserow` retorna contagens corretas; tempo de resposta menor |

---

## Ordem de commits

1. `perf(rebuild): filtra votos out no SQL ao invés de descartar em Python`
2. `perf(dashboard): debounce 300ms na busca evita re-renders por keystroke`
3. `perf(reconcile): substitui SELECT completo por count() agregado no PostgREST`
