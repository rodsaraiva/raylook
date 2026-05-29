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
  id          text PK (uuid)
  cliente_id  text NOT NULL  -> clientes(id)
  tipo        text NOT NULL  CHECK (tipo IN ('credit','debit'))
  valor       numeric NOT NULL CHECK (valor > 0)   -- sempre positivo
  pacote_id   text NULL      -> pacotes(id)   -- origem do crédito (cancelamento)
  venda_id    text NULL      -> vendas(id)    -- venda paga que gerou o crédito
  pagamento_id text NULL     -> pagamentos(id) -- onde o débito foi aplicado
  descricao   text NULL      -- ex: "Cancelamento pacote #A-123"
  created_by  text NULL
  created_at  timestamptz NOT NULL DEFAULT now()
```

Índice: `(cliente_id)` para cálculo de saldo.

**Saldo do cliente** = `SUM(valor WHERE tipo='credit') − SUM(valor WHERE tipo='debit')`.

Sem `balance` materializado — saldo é sempre derivado dos lançamentos (fonte
única de verdade; evita drift). Volume baixo, soma é barata.

## Componentes

### 1. `app/services/credit_service.py` (novo)

Interface única, sem conhecer detalhes de PIX/Asaas:

- `get_balance(cliente_id) -> float` — soma dos lançamentos.
- `get_ledger(cliente_id) -> list[dict]` — extrato ordenado por `created_at`.
- `list_balances() -> list[dict]` — saldo por cliente (para a aba financeiro);
  só clientes com lançamentos, com nome/celular embedados.
- `add_credit(cliente_id, valor, *, pacote_id, venda_id, descricao, created_by)`
  — insere lançamento `credit`. Idempotência: não insere se já existe `credit`
  com o mesmo `venda_id` (cancelamento re-executado não duplica).
- `debit_credit(cliente_id, valor, *, pagamento_id, descricao, created_by)`
  — insere lançamento `debit`. Idempotência por `pagamento_id`.

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
  (`paid_at=now`), grava 1 `debit` (valor=`credito_aplicado`, descricao="Pago
  com crédito — N pedidos"), invalida snapshots. Retorna `pago_com_credito=True`,
  `credito_aplicado`, `total`, `cobranca=0`, sem QR.
- `cobranca > 0`: cria PIX no Asaas pelo valor `cobranca`. Salva no
  `app_runtime_state` (`combined_pix_<asaas_id>`) os campos atuais **+
  `credito_aplicado`**. O débito é gravado **só na confirmação** (ver §4).
- Resposta sempre inclui `saldo_antes`, `credito_aplicado`, `cobranca`.

**`get_or_create_pix`** (PIX individual):
- Mesma lógica sobre `venda.total_amount`.
- `cobranca == 0`: marca o pagamento `paid`, grava `debit`, retorna
  `pago_com_credito=True`.
- `cobranca > 0`: cria PIX pela diferença. Persiste `credito_aplicado` no
  `payload_json` do pagamento (chave `credito_aplicado`) para a confirmação ler.
- **Re-entrância:** se o pagamento já tem `pix_payload` salvo, retorna o crédito
  já registrado no `payload_json` (não recalcula nem reaplica saldo).

### 4. Débito na confirmação — `asaas_sync_service.py`

Ao marcar um pagamento como `paid`:

- **Caminho 1 (individual):** se `payload_json.credito_aplicado > 0` e ainda não
  há `debit` para esse `pagamento_id`, chama `debit_credit`.
- **Caminho 2 (combinado):** ao confirmar o combinado, se o state tem
  `credito_aplicado > 0`, grava **um** `debit` (idempotente por `asaas_id`/
  primeiro `pagamento_id`) com a descrição do combinado.

`debit_credit` é idempotente (por `pagamento_id`), então re-runs do polling não
duplicam.

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
       pagamentos -> paid; debit_credit(-credito) [ledger: -debit]
    se cobranca > 0:
       PIX Asaas (cobranca); guarda credito_aplicado
                                              ↓ (polling confirma)
  asaas_sync_service
    pagamento -> paid; debit_credit(-credito)   [ledger: -debit]
```

## Migration (produção)

`BEGIN; … COMMIT;` criando `creditos` + índice. Pré-check com
`pg_get_constraintdef`/`to_regclass('public.creditos')` para idempotência.
Testar em `alana_staging`? Não — banco é dedicado (`raylook_*`). Testar em dev
SQLite + staging do próprio Postgres raylook antes do deploy via CI.

## Testes (integração, SQLite real)

1. **Cancelamento credita:** pacote com 1 venda paga → `cancel_package(force=True)`
   → venda/pagamento `cancelled`, 1 lançamento `credit` = valor pago, saldo correto.
2. **Idempotência crédito:** rodar 2x não duplica.
3. **Abatimento parcial:** saldo 50, compra 200 → cobranca 150, sem débito até
   confirmar; após confirmar → 1 `debit` 50, saldo 0.
4. **Cobertura total:** saldo 300, compra 200 → cobranca 0, pagamentos `paid`,
   `debit` 200, saldo 100, sem chamada Asaas.
5. **Idempotência débito:** polling repetido não duplica `debit`.
6. **Saldo:** `get_balance` = SUM(credit) − SUM(debit).

## Trade-offs aceitos

- **Sem reserva de saldo:** débito só na confirmação. Se o cliente gerar 2 PIX
  antes de pagar, o saldo aparece disponível nos dois. Aceitável no volume atual.
- **Saldo derivado (não materializado):** soma a cada leitura. Volume baixo.

## Fora de escopo

- Estorno/refund (regra: só crédito na plataforma).
- Ajuste/lançamento manual de crédito pela admin.
- Expiração de crédito.
