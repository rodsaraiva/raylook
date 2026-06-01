# Sistema de Créditos por Cancelamento — Design

Data: 2026-05-29

## Problema

Quando um pacote é cancelado (loja sem estoque) e o cliente **já pagou**, hoje o
`package_cancellation_service.cancel_package(force=True)` apenas preserva o
pagamento pago intacto — o dinheiro fica "parado" sem destino claro e o cliente
não tem como reaproveitá-lo. Não há estorno: a regra do negócio é **crédito na
plataforma** para abater compras futuras.

## Objetivo

- Cancelamento de pacote com pagamento pago gera **crédito** para o cliente (100%
  do valor pago).
- Crédito é **abatido automaticamente** na geração do próximo PIX (individual ou
  combinado), com a redução **visível** para o cliente.
- Crédito visível em duas frentes: aba **"Créditos"** no financeiro (extrato
  completo + saldo) e KPI de **saldo** no portal do cliente.

## Decisões (confirmadas no brainstorming)

| Tema | Decisão |
|---|---|
| Aplicação do crédito | Automática na geração do PIX |
| Cancelamento de venda paga | Cancela a venda+pagamento e credita 100% do valor |
| Modelo de dados | Extrato/ledger (lançamentos `credit`/`debit`); saldo = soma |
| Crédito cobre tudo | Quita sem PIX (marca pago, debita, "pago com crédito") |
| Momento do débito (parcial) | Só quando o PIX for confirmado pago |
| Visibilidade na cobrança | Portal mostra "Crédito aplicado: −R$ X" e total a pagar |
| Estorno / refund | **Não existe** — só crédito na plataforma |
| Ajuste manual / expiração | Fora de escopo (YAGNI) |

## Modelo de dados — tabela `creditos` (ledger)

Nova tabela em `deploy/postgres/schema.sql` e `deploy/sqlite/schema.sql`.

```
creditos
  id              text PK (uuid)
  cliente_id      text NOT NULL  -> clientes(id)
  tipo            text NOT NULL  CHECK (tipo IN ('credit','debit'))
  status          text NOT NULL DEFAULT 'confirmed'
                  CHECK (status IN ('pending','confirmed'))
  valor           numeric NOT NULL CHECK (valor > 0)   -- sempre positivo
  pacote_id       text NULL      -> pacotes(id)   -- origem do crédito (cancelamento)
  venda_id        text NULL      -> vendas(id)    -- venda paga que gerou o crédito
  pagamento_id    text NULL      -> pagamentos(id) -- débito de PIX individual
  asaas_payment_id text NULL     -- débito de PIX combinado (mapeia o pago no Asaas)
  descricao       text NULL      -- ex: "Cancelamento pacote #A-123"
  created_by      text NULL
  created_at      timestamptz NOT NULL DEFAULT now()
```

Índices: `(cliente_id)` para saldo; `(pagamento_id)` e `(asaas_payment_id)` para
confirmar débitos pendentes.

**Saldo do cliente** = `SUM(credit confirmed) − SUM(debit confirmed)`.
Lançamentos `pending` **não entram no saldo** (honra "só debita na confirmação"
sem precisar de reserva).

Sem `balance` materializado — saldo é sempre derivado dos lançamentos (fonte
única de verdade; evita drift). Volume baixo, soma é barata.

**Persistência 100% em Postgres** (tabela `creditos`): nada de `payload_json`
nem `app_runtime_state` para guardar crédito. O `runtime_state` do combinado
segue só com o mapeamento asaas→pagamentos que já existe hoje.

## Componentes

### 1. `app/services/credit_service.py` (novo)

Interface única, sem conhecer detalhes de PIX/Asaas:

- `get_balance(cliente_id) -> float` — `SUM(credit confirmed) − SUM(debit confirmed)`.
- `get_ledger(cliente_id) -> list[dict]` — extrato ordenado por `created_at`
  (inclui pendentes, marcados como tal no extrato).
- `list_balances() -> list[dict]` — saldo por cliente (para a aba financeiro);
  só clientes com lançamentos, com nome/celular embedados.
- `add_credit(cliente_id, valor, *, pacote_id, venda_id, descricao, created_by)`
  — insere `credit` `confirmed`. Idempotência: não insere se já existe `credit`
  com o mesmo `venda_id` (cancelamento re-executado não duplica).
- `add_pending_debit(cliente_id, valor, *, pagamento_id=None, asaas_payment_id=None, descricao)`
  — insere `debit` `status='pending'` (não afeta saldo). Idempotência: não
  insere se já há `debit` para o mesmo `pagamento_id`/`asaas_payment_id`.
- `confirm_debit(*, pagamento_id=None, asaas_payment_id=None)`
  — `UPDATE creditos SET status='confirmed' WHERE tipo='debit' AND status='pending'
  AND <chave>`. Idempotente (no-op se já confirmado).
- `add_confirmed_debit(cliente_id, valor, *, pagamento_id, descricao)`
  — atalho para cobertura total (grava `debit` já `confirmed`, sem PIX).

Backend-agnóstico: usa `SupabaseRestClient` (Postgres) e cai no
`sqlite_service` quando `DATA_BACKEND=sqlite`, seguindo o padrão dos services
existentes.

### 2. Geração do crédito — `package_cancellation_service.py`

Em `cancel_package`, o ramo dos pagos muda:

- **Antes:** `if pag_status == PAID_STATUS: continue` (preserva).
- **Depois:** para cada venda paga:
  1. `credit_service.add_credit(cliente_id, total_amount, pacote_id=…,
     venda_id=…, descricao="Cancelamento pacote #<friendly_id>", created_by=…)`.
  2. Marca a **venda** `cancelled` e o **pagamento** `cancelled` (mantém
     `paid_at`? Não — segue o padrão: o pagamento vira `cancelled`. O registro
     do dinheiro recebido vive agora no ledger de créditos).

O retorno passa a incluir `credited_total` e `credited_clients` (para o frontend
mostrar "X clientes creditados em R$ Y" no toast de confirmação).

`preview_cancel` ganha `credit_total` (soma do que será creditado) para o modal
de confirmação avisar o valor que virará crédito.

Idempotência: como `cancel_package` já é idempotente quando o pacote está
`cancelled`, e `add_credit` deduplica por `venda_id`, re-execução é segura.

### 3. Abatimento — `portal_service.py`

Helper compartilhado:

```
_apply_credit(cliente_id, total) -> (saldo_antes, credito_aplicado, cobranca)
    saldo_antes = credit_service.get_balance(cliente_id)
    credito_aplicado = round(min(saldo_antes, total), 2)
    cobranca = round(total - credito_aplicado, 2)
```

**`create_combined_pix`** (fluxo "Pagar todos"):
- Calcula `_apply_credit` sobre o `total`.
- `cobranca == 0`: **não chama Asaas**. Marca cada pagamento pendente `paid`
  (`paid_at=now`), grava 1 `add_confirmed_debit` (valor=`credito_aplicado`,
  descricao="Pago com crédito — N pedidos"), invalida snapshots. Retorna
  `pago_com_credito=True`, `credito_aplicado`, `total`, `cobranca=0`, sem QR.
- `cobranca > 0`: cria PIX no Asaas pelo valor `cobranca`. O `app_runtime_state`
  (`combined_pix_<asaas_id>`) segue só com o mapeamento asaas→pagamentos (como
  hoje). O débito vai pra `creditos` via `add_pending_debit(asaas_payment_id=
  <asaas_id>, valor=credito_aplicado)` — fica `pending` até a confirmação (§4).
- Resposta sempre inclui `saldo_antes`, `credito_aplicado`, `cobranca`.

**`get_or_create_pix`** (PIX individual):
- Mesma lógica sobre `venda.total_amount`.
- `cobranca == 0`: marca o pagamento `paid`, grava `add_confirmed_debit`,
  retorna `pago_com_credito=True`.
- `cobranca > 0`: cria PIX pela diferença e grava `add_pending_debit(
  pagamento_id=<id>, valor=credito_aplicado)`.
- **Re-entrância:** se o pagamento já tem `pix_payload` salvo, lê o `debit`
  pendente já existente em `creditos` (por `pagamento_id`) e retorna o mesmo
  `credito_aplicado` — não recalcula nem reaplica saldo. `add_pending_debit`
  deduplica por `pagamento_id`.

### 4. Débito na confirmação — `asaas_sync_service.py`

Ao marcar um pagamento como `paid`:

- **Caminho 1 (individual):** após marcar `paid`, chama
  `confirm_debit(pagamento_id=<id>)` — flipa o `debit` pendente (se houver) para
  `confirmed`. No-op se não havia crédito aplicado.
- **Caminho 2 (combinado):** ao confirmar o combinado, chama
  `confirm_debit(asaas_payment_id=<asaas_id>)`.

`confirm_debit` é um `UPDATE … WHERE status='pending'`, naturalmente idempotente
— re-runs do polling não duplicam nem reconfirmam.

### 5. Aba "Créditos" no financeiro

- **Backend:** `app/routers/finance.py` → `GET /api/finance/credits` retornando
  `{ balances: [...], ledger: [...] }` via `credit_service`.
- **Frontend:** `static/js/dashboard_v2.js` — nova aba "Créditos" ao lado de
  Recebíveis/Pagos. Lista clientes com saldo > 0 e, ao expandir, o extrato
  (data, tipo, valor, origem/pacote, descrição).

### 6. Saldo no portal

- `app/routers/portal.py` (rota que renderiza `portal_pedidos.html`): injeta
  `credit_balance = credit_service.get_balance(cliente_id)`.
- `templates/portal_pedidos.html`: novo KPI "Crédito disponível: R$ X" (só
  exibe se saldo > 0).
- Tela do PIX (já existente no portal): mostrar `credito_aplicado` e total a
  pagar a partir da resposta de `create_combined_pix`/`get_or_create_pix`;
  caso `pago_com_credito`, mensagem "Pago integralmente com crédito" sem QR.

## Fluxo de dados (resumo)

```
Cancelamento (admin)
  cancel_package(force=True)
    venda paga -> status=cancelled, pagamento=cancelled
    credit_service.add_credit(+valor)          [ledger: +credit]

Nova compra (cliente, portal)
  create_combined_pix / get_or_create_pix
    saldo = get_balance
    credito_aplicado = min(saldo, total)
    cobranca = total - credito_aplicado
    se cobranca == 0:
       pagamentos -> paid; add_confirmed_debit  [ledger: -debit confirmed]
    se cobranca > 0:
       PIX Asaas (cobranca); add_pending_debit  [ledger: -debit pending]
                                              ↓ (polling confirma)
  asaas_sync_service
    pagamento -> paid; confirm_debit            [ledger: debit -> confirmed]
```

## Migration (produção)

`BEGIN; … COMMIT;` criando `creditos` + índices `(cliente_id)`,
`(pagamento_id)`, `(asaas_payment_id)`. Pré-check com
`pg_get_constraintdef`/`to_regclass('public.creditos')` para idempotência.
Testar em `alana_staging`? Não — banco é dedicado (`raylook_*`). Testar em dev
SQLite + staging do próprio Postgres raylook antes do deploy via CI.

## Testes (integração, SQLite real)

1. **Cancelamento credita:** pacote com 1 venda paga → `cancel_package(force=True)`
   → venda/pagamento `cancelled`, 1 lançamento `credit` = valor pago, saldo correto.
2. **Idempotência crédito:** rodar 2x não duplica.
3. **Abatimento parcial:** saldo 50, compra 200 → cobranca 150 + `debit` 50
   `pending`; saldo permanece 50 (pendente não conta). Após `confirm_debit` →
   `debit` `confirmed`, saldo 0.
4. **Cobertura total:** saldo 300, compra 200 → cobranca 0, pagamentos `paid`,
   `debit` 200 `confirmed`, saldo 100, sem chamada Asaas.
5. **Idempotência débito:** `confirm_debit` repetido não altera saldo;
   `add_pending_debit` repetido (mesmo pagamento/asaas_id) não duplica.
6. **Saldo:** `get_balance` = SUM(credit confirmed) − SUM(debit confirmed),
   ignorando `pending`.

## Trade-offs aceitos

- **Sem reserva de saldo:** débito pendente não conta no saldo. Se o cliente
  gerar 2 PIX antes de pagar, o saldo aparece disponível nos dois (gera 2 débitos
  pendentes; só o pago é confirmado, o outro fica pendente órfão e inócuo).
  Aceitável no volume atual.
- **Saldo derivado (não materializado):** soma a cada leitura. Volume baixo.

## Fora de escopo

- Estorno/refund (regra: só crédito na plataforma).
- Ajuste/lançamento manual de crédito pela admin.
- Expiração de crédito.

---

## Addendum (2026-05-29) — Fluxo de pagamento serializado (anti double-spend)

Decisão do usuário após a revisão de integração: o débito-só-na-confirmação, sem
reserva, permitia um double-spend se o cliente pagasse dois PIX sobrepostos
(combinado + individual) — o saldo podia ficar negativo. Em vez de reserva no
saldo (que prenderia crédito em PIX abandonado, contra a decisão do Q5), adota-se
um **fluxo serializado**:

**Geração do pagamento no portal (individual ou "Pagar todos"):**

1. **Crédito ≥ valor (cobre tudo):** o portal exibe um **modal de confirmação**
   ("vai usar R$X de crédito e quitar — confirmar?"). Ao confirmar, o servidor
   **debita o crédito na hora** (débito `confirmed`), marca o(s) pagamento(s)
   como `paid` e **não chama o Asaas**.
2. **Crédito < valor:** gera cobrança Asaas só da **diferença**; o crédito vira
   débito `pending`, confirmado quando o PIX é pago.
3. **Serialização (garante saldo nunca-negativo):** no momento em que o cliente
   inicia um pagamento **que envolve crédito** (caso 1 ou 2), todas as **outras
   cobranças em aberto** do cliente são canceladas:
   - cobranças Asaas individuais com `provider_payment_id` (status
     `created`/`sent`, não pagas) → `AsaasClient.cancel_payment` + o `pagamento`
     volta a um estado recarregável (limpa `provider_payment_id`/`payment_link`/
     `pix_payload`/`due_date`, status `created`) + remove o débito `pending` dele;
   - PIX combinados do cliente em `app_runtime_state` (prefixo `combined_pix_`)
     → `cancel_payment` do `asaas_id` + remove débito `pending`
     (`asaas_payment_id`) + `delete_runtime_state`.
   A cobrança que está sendo criada agora é preservada (`keep`).

Como as outras cobranças com crédito provisório são canceladas (e seus débitos
`pending` removidos), no instante da geração só existe **uma** aplicação de
crédito viva. O `get_balance` (confirmados) já reflete o disponível correto — não
é preciso reserva nem clamp. Quando esse único PIX é pago, seu débito confirma;
nenhum outro débito de crédito pode confirmar (foram cancelados). Logo o saldo
**nunca fica negativo**. Para pagar outro pedido depois, o portal gera uma
**cobrança nova**, que recalcula o crédito já atualizado.

Cancelar a cobrança **inclui cancelar no Asaas** (DELETE /payments/{id}) — para
o QR antigo não poder mais ser pago.

Isto substitui a aplicação de crédito introduzida nas Tasks 4/5 (que passa a
chamar o cancelamento das outras cobranças antes de aplicar o crédito) e adiciona
o modal de confirmação na cobertura total.
