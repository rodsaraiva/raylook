# Voto Manual em Enquetes — Design Spec

**Data:** 2026-06-02
**Branch:** `feat/voto-manual-enquetes`
**Status:** Aprovado

---

## Objetivo

Permitir que o operador adicione votos manualmente a uma enquete diretamente pelo dashboard, sem precisar de uma mensagem no WhatsApp. O voto é idêntico a um voto real em todos os aspectos, exceto pelo campo `synthetic = 1` para fins de auditoria.

---

## UI — Botão e Modal

### Posição do botão

Em `enquetes.js`, função `renderDetail()`, entre o bloco `.enq-stats` e a div `.enq-pacotes-list`:

```html
<button class="btn-add-voto" id="enq-add-voto-btn">＋ Adicionar Voto</button>
```

### Fluxo do modal

1. Clique no botão abre modal com dois campos: **busca de cliente** e **seletor de qty**
2. Campo busca: texto livre, debounce 300ms → `GET /api/dashboard/clientes?q=...`
   - Resultado encontrado: exibe nome + celular do cliente abaixo do campo
   - Não encontrado: exibe aviso amarelo + campos extras de **nome** e **celular** para cadastro
3. Qty: chips clicáveis com os valores válidos — `3 · 4 · 6 · 8 · 9 · 12 · 16 · 20 · 24`
4. Botão de confirmação:
   - Cliente encontrado: label "Confirmar"
   - Cliente novo: label "Criar e Votar"
5. Submit → chama `POST /api/dashboard/enquetes/{enquete_id}/votos`
6. Sucesso → fecha modal, chama `loadDetail(enquete_id)` para recarregar o painel
7. Erro → exibe mensagem inline no modal (não fecha)

---

## Backend — Novo endpoint

### Rota

```
POST /api/dashboard/enquetes/{enquete_id}/votos
```

Autenticação: mesma do router de dashboard (requer login).

### Body

```json
{
  "busca": "maria silva",
  "qty": 6,
  "nome": "Maria Silva",
  "celular": "62999991234"
}
```

- `busca` — obrigatório, texto livre (nome parcial ou celular)
- `qty` — obrigatório, inteiro; valores permitidos: `3, 4, 6, 8, 9, 12, 16, 20, 24`
- `nome` + `celular` — opcionais; obrigatórios apenas quando cliente não for encontrado

### Lógica

1. Valida `qty` contra os valores permitidos → 400 se inválido
2. Busca enquete por `enquete_id` → 404 se não existir
3. Busca cliente por `busca`:
   - Tenta match por celular usando `_phone_variants` (com e sem DDI 55)
   - Tenta match por nome (ILIKE `%busca%`)
   - Se não encontrado e `nome`+`celular` ausentes → retorna `{"found": false}` (não é erro)
   - Se não encontrado e `nome`+`celular` presentes → cria cliente (`INSERT clientes`)
4. Faz upsert do voto:
   ```python
   {
     "enquete_id": enquete_id,
     "cliente_id": cliente["id"],
     "alternativa_id": None,  # não há alternativa para votos manuais
     "qty": qty,
     "status": "in",
     "synthetic": 1,
     "voted_at": now,
   }
   ```
   on_conflict: `enquete_id, cliente_id` (atualiza qty/status se já existir)
5. Chama `rebuild_for_poll(enquete_id)` — falha no rebuild é logada mas não bloqueia a resposta
6. Retorna `{"status": "ok", "voto_id": "...", "package_result": {...}, "cliente": {...}}`

### Resposta intermediária (cliente não encontrado)

```json
{"found": false}
```

HTTP 200. O frontend exibe os campos de cadastro e o usuário resubmete com `nome` + `celular`.

---

## Schema — sem migration necessária

A tabela `votos` já possui `synthetic smallint NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1))`.
Votos manuais são inseridos com `synthetic = 1`. Nenhuma alteração de schema.

---

## Testes

Dois testes unitários em `tests/unit/test_add_voto_manual.py`:

**`test_add_voto_manual_cliente_existente`**
- Setup: enquete existente + cliente existente no FakeSB
- Chama endpoint com `busca` que encontra o cliente, `qty=6`
- Assert: voto criado com `synthetic=1`, `status="in"`, `qty=6`

**`test_add_voto_manual_cria_cliente`**
- Setup: enquete existente, nenhum cliente no FakeSB
- Chama endpoint com `busca`, `nome="Ana Paula"`, `celular="62999991234"`, `qty=6`
- Assert: cliente criado, voto criado com `synthetic=1`

---

## Arquivos modificados / criados

| Arquivo | Tipo | O quê |
|---------|------|-------|
| `app/routers/dashboard.py` | Modificar | Novo endpoint `POST /enquetes/{id}/votos` |
| `static/js/enquetes.js` | Modificar | Botão + modal + lógica de busca/submit |
| `tests/unit/test_add_voto_manual.py` | Criar | 2 testes unitários |
