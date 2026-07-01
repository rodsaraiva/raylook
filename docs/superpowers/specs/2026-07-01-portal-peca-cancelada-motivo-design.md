# Spec — Peça paga cancelada visível no portal do cliente com motivo

Data: 2026-07-01
Status: aprovado (design); pendente plano de implementação

## Problema

Quando a admin cancela uma peça **já paga** pelo dashboard (mesmo gatilho que
gera crédito — `POST /api/dashboard/packages/{id}/cancel` →
`package_cancellation_service.cancel_package`), a peça **desaparece** do portal da
cliente em vez de aparecer com uma tag "Cancelado" e a explicação do motivo.

### Causa-raiz (confirmada)

O cancelamento grava, em cascata:

- `vendas.status = 'cancelled'`
- `pagamentos.status = 'cancelled'` (no ramo pago, `paid_at` é **preservado**)
- `pacotes.status = 'cancelled'` (+ `cancelled_at`, `cancelled_by`)

O portal (`app/services/portal_service.py::get_client_orders`) tem **três filtros
`continue`** que descartam exatamente esses estados, então a peça nunca chega ao
template:

- `portal_service.py:434` — pula venda `cancelled`
- `portal_service.py:446` — pula pacote `cancelled`
- `portal_service.py:450` — pula pagamento `cancelled`

O template (`templates/portal_pedidos.html:113`) **já tem** o badge "Cancelado" e
o estado de pagamento cancelado (`:157`), mas os dados são filtrados antes de
renderizar. Além disso, **não existe hoje** nenhum campo de motivo de
cancelamento gravado — só `cancelled_by` (o role de quem cancelou). Os "reason
chips" do template (`:128`) são de pendência logística (`pending_reasons`), não de
cancelamento.

## Decisões de escopo

- **Escopo:** só peças **pagas → canceladas** (que viraram crédito) voltam a
  aparecer. Peças canceladas que a cliente nunca pagou continuam ocultas.
- **Motivo:** texto livre digitado pela admin no momento do cancelamento,
  **obrigatório**, exibido pra cliente exatamente como escrito.
- **Onde guardar:** `pacotes.cancel_reason` (um motivo por cancelamento de
  pacote; o portal já faz join com `pacote`).
- **Fora de escopo:** motivo por-cliente/por-venda; lista pré-definida de
  motivos; notificação proativa (portal é o único canal — ver
  `project_canal_notificacao_cliente`).

## Design

### Detecção de "peça paga que foi cancelada"

O discriminador é **`pagamentos.paid_at` presente**. No `cancel_package`, o ramo
pago faz `PATCH pagamentos {status:'cancelled', updated_at}` (preserva `paid_at`),
enquanto o ramo não-pago faz `{status:'cancelled', paid_at:null, updated_at}`.
Logo: `status == 'cancelled'` **e** `paid_at != null` ⇒ virou crédito ⇒ deve
aparecer. Isso mantém as canceladas-nunca-pagas ocultas sem query extra.

### Componentes e mudanças

1. **Migration — `deploy/postgres/migrations/F067_pacotes_cancel_reason.sql`** (novo)
   - `BEGIN; ALTER TABLE pacotes ADD COLUMN IF NOT EXISTS cancel_reason text; COMMIT;`
   - Idempotente, coluna nullable, sem constraint → seguro em prod.
   - Espelhar em `deploy/postgres/schema.sql` e `deploy/sqlite/schema.sql`
     (`cancel_reason TEXT`) pra manter o schema canônico coerente.

2. **`app/services/package_cancellation_service.py::cancel_package`**
   - Novo parâmetro `reason: Optional[str] = None`.
   - Incluir `"cancel_reason": reason` no `PATCH` final de `pacotes` (junto com
     status/cancelled_at/cancelled_by).
   - Não altera a lógica de crédito.

3. **`app/routers/dashboard.py` — endpoint `cancel_package`**
   - Ler `reason = (body.get("cancel_reason") or "").strip() or None`.
   - Passar `reason=reason` pro serviço. Não bloquear no backend se vazio (a
     obrigatoriedade é validada na UI); backend aceita `None` sem quebrar.

4. **`app/services/portal_service.py::get_client_orders`**
   - Adicionar `cancel_reason` às colunas do embed `pacote:pacote_id(...)`.
   - Reestruturar os 3 filtros: uma peça `cancelled` com `paid_at` presente
     **não** é descartada; open/closed e canceladas-nunca-pagas continuam
     descartadas.
   - Adicionar `cancel_reason` (string, `""` se nulo) ao dict do pedido. `status`
     e `delivery_status` já resultam em `"cancelled"` pra esse caso.

5. **`templates/portal_pedidos.html`**
   - Sob o badge "Cancelado" (`ds == 'cancelled'`), renderizar a linha do motivo
     quando `order.cancel_reason` existir (reaproveitar estilo `reason-obs`).

6. **Frontend admin — `static/js/dashboard_v2.js` + `templates/dashboard_v2.html`**
   - Novo modal `cancel-reason-modal` (HTML no `dashboard_v2.html`, no padrão do
     `pending-reasons-modal`) com **textarea** de motivo.
   - Nova função `promptCancelReason()` (padrão do `promptPendingReasons`):
     resolve `{ cancel_reason }` ou `null`; **bloqueia** OK com textarea vazia.
   - Ponto de cancelamento do painel de detalhe (`dashboard_v2.js:876`): chamar
     `promptCancelReason()` antes; setar `opts.body = { cancel_reason }` e
     `delete opts.confirmText`.
   - Expor a função globalmente (ex.: `window.RaylookCancelReason`) pra reuso.

7. **`static/dashboard/lib.js::doAction`**
   - No handler de 409 (`blocked_paid`), o force POST passa a enviar
     `{ ...body, force: true }` (em vez de só `{ force: true }`) pra não perder o
     `cancel_reason` capturado no primeiro passo.

8. **`static/dashboard/modal.js` — cancel do drill-down (`:245–280`)**
   - Chamar `window.RaylookCancelReason()` antes do fetch e mandar `cancel_reason`
     no body (inicial e no force do 409).

## Data flow

```
[Dashboard] admin clica "Cancelar pacote"
   -> promptCancelReason() (textarea obrigatória) -> { cancel_reason }
   -> POST /api/dashboard/packages/{id}/cancel  body={cancel_reason}
        -> 409 blocked_paid? -> confirma -> POST body={cancel_reason, force:true}
   -> cancel_package(reason): grava pacotes.cancel_reason + cascade + crédito
[Portal] get_client_orders
   -> peça cancelled com paid_at != null NÃO é filtrada
   -> order.cancel_reason = pacote.cancel_reason
   -> template: badge "Cancelado" + linha do motivo
```

## Error handling / edge cases

- **Motivo vazio no backend:** aceito como `None`; peça aparece só com a tag
  (fallback). A UI é quem obriga o preenchimento.
- **Cancelamento sem pagos (force não exigido):** primeiro POST já leva o
  `cancel_reason`; grava normalmente.
- **Idempotência:** re-cancelar pacote já `cancelled` retorna cedo
  (`already_cancelled`) sem sobrescrever o motivo — comportamento atual mantido.
- **Peça cancelada nunca paga:** `paid_at` nulo → continua oculta (escopo).

## Testing

- **Unit (`tests/unit/test_portal_service.py`):** peça paga→cancelada aparece com
  `status='cancelled'`, `delivery_status='cancelled'` e `cancel_reason`; sem
  motivo aparece com `cancel_reason=''`; cancelada-nunca-paga segue oculta. (2
  testes já escritos, falhando — TDD.)
- **Unit (`tests/unit/test_package_cancellation_service.py`):** `cancel_package`
  com `reason` grava `pacotes.cancel_reason`.
- **UI (Playwright):** cancelar peça paga no dashboard com motivo → abrir portal
  da cliente (ou `/portal/preview/{id}`) e ver tag + motivo.
- Integração > mock, especialmente DB (regra herdada).

## Deploy

- Migration roda em prod **só com aprovação** (raylook tem Postgres dedicado; não
  usa `alana_staging`). Local valida com SQLite.
- Sem push sem aprovação do usuário (pedido explícito).
