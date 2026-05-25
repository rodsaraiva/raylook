# Separado e Enviado com granularidade de cliente

**Data**: 2026-05-25
**Branch alvo**: a definir (provavelmente `feat/separado-enviado-por-cliente`)
**Estado**: brainstorm aprovado, aguardando plano de implementação

## Problema

Hoje os 7 estados do fluxo (`aberto → fechado → confirmado → pago → pendente → separado → enviado`) operam todos com granularidade de **pacote**. Mas na realidade operacional, um pacote tem N clientes e:

- A logística despacha cada cliente em momentos diferentes (Correios, retirada, motoboy).
- Hoje, "marcar enviado" é uma ação que afeta o pacote inteiro, escondendo o fato de que clientes individuais podem estar em estados diferentes dentro do mesmo pacote.
- O portal do cliente A já mostra "em separação" mesmo após receber o pedido, porque depende de `pkg.shipped_at` que só seta quando o admin clica no botão único.

A infraestrutura por-cliente já existe (`pacote_clientes.pdf_sent_at`, `pacote_clientes.shipped_at`), e há um endpoint `advance_client` parcialmente usado pelo dashboard. O que falta é a UI principal refletir essa granularidade e o backend ser coerente.

## Objetivo

Nas seções **Separado** e **Enviado** do dashboard, cada linha da lista representa um par (pacote, cliente) — não mais um pacote agregado. Logística marca cada cliente individualmente como enviado. O pacote-pai é considerado "enviado" só quando o último cliente sai.

Os outros 5 estados (`aberto`, `fechado`, `confirmado`, `pago`, `pendente`) **continuam por pacote**.

## Decisões de design

| Decisão | Escolha | Por quê |
|---|---|---|
| Granularidade nas seções Separado/Enviado | 1 linha por (pacote, cliente) | Pedido direto do usuário; reflete operação real. |
| Transição `pendente → separado` | Segue agregada (estoque gera etiqueta única) | PDF é uma folha só com todos os clientes — separar individualmente não tem sentido operacional. |
| Transição `separado → enviado` | Por cliente | Logística despacha cada cliente em momentos diferentes. |
| Pacote em "parcialmente enviado" | Pode aparecer em ambas as seções simultaneamente | Linhas dos clientes não-enviados ficam em Separado, linhas dos enviados em Enviado. `pkg.shipped_at` só seta no último cliente. |
| Ações | Só por cliente, sem batch ou multi-select | Mantém UI simples; usuário descartou checkbox e botão agregado. |
| Painel de detalhe | Foco no cliente, com contexto do pacote como subtitle | Botão "Marcar enviado" atua só naquele cliente. Drilldown completo continua disponível pelo botão "Ver detalhes". |
| Backend reformata buckets | Sim — `/api/dashboard/packages` devolve cliente-rows em `separado`/`enviado` | Centraliza a derivação; JS fica burro. |
| Backfill | Migration + fallback runtime (defensivo) | Migration corrige histórico (pacotes hoje enviados sem `pc.shipped_at`); fallback protege caso algum código antigo grave só em `pkg`. |
| Métricas/finance | Sem impacto | Nenhum consumidor lê `shipped_at` em `metrics/` ou `finance/`. |

## Arquitetura

```
GET /api/dashboard/packages
        │
        ▼
[_derive_state(pkg)]
   ├─ estado agregado é separado/enviado?
   │     │
   │     ▼
   │  expande pacote_clientes em cliente-rows
   │     ├─ pc.pdf_sent_at setado, pc.shipped_at NULL  → linha em "separado"
   │     └─ pc.shipped_at setado                       → linha em "enviado"
   │
   └─ outros estados → linha agregada de pacote (sem mudança)

POST /api/dashboard/packages/{pkg}/clients/{cli}/advance?to=enviado
        │
        ▼
[advance_client]
   ├─ seta pacote_clientes.shipped_at = now
   ├─ se foi o último cliente → seta pkg.shipped_at = now também
   └─ retorna {previous, new_state, cliente_id}
```

## Mudanças por arquivo

### Backend

**`app/routers/dashboard.py`**

- `_derive_state` ganha estado intermediário "parcialmente enviado": pacote `approved` + `pkg.pdf_sent_at` + algum `pc.shipped_at` mas não todos → continua retornando `"separado"` no nível agregado (porque há clientes esperando). Quando *todos* os `pc.shipped_at` estão setados, vira `"enviado"` mesmo que `pkg.shipped_at` ainda não tenha sido propagado.
- `list_packages_by_state`: após classificar cada pacote, se o estado é `separado` ou `enviado`, expande seus `pacote_clientes` em cliente-rows:
  - `separado`: emite uma linha por cliente onde `pc.shipped_at IS NULL`.
  - `enviado`: emite uma linha por cliente onde `pc.shipped_at IS NOT NULL`.
  - Cliente-row tem shape: `{type: "client_row", pacote_id, cliente_id, cliente_nome, qty, pacote_friendly_id, produto_name, image, pdf_sent_at, shipped_at, state_since}`.
- Filtros por data: em `enviado`, range aplica em `pc.shipped_at` (não `pkg.shipped_at`). Em `separado` aplica em `pc.pdf_sent_at` (ou `pkg.pdf_sent_at` como fallback).
- `advance_package` na transição `separado → enviado`: marca **todos** os `pacote_clientes.shipped_at` que ainda eram NULL + `pkg.shipped_at`. Atalho admin que pula a granularidade.
- `advance_client` quando `to=enviado` ou avanço unitário pra enviado: ao detectar que foi o último cliente sem `shipped_at`, seta também `pkg.shipped_at = now` e `pkg.shipped_by` do role.
- `regress_package` em `enviado`: zera `pkg.shipped_at` E `pacote_clientes.shipped_at` de todos os clientes do pacote.
- `shipped_by` deixa de ser `"simulated@dev"` chumbado — pega do role/sessão. (Correção de tech debt aproveitando a mudança.)

**`app/services/portal_service.py`**

- `_delivery_status` passa a receber também o `pacote_cliente` do cliente que está olhando o portal. Critério "enviado" vira `pc.shipped_at OR pkg.shipped_at` (fallback defensivo).
- `get_client_orders` adiciona `pacote_clientes` ao select (filtrado pelo `cliente_id`) e passa pro `_delivery_status`.

### Frontend

**`static/js/dashboard_v2.js`**

- `renderList`: detecta se o item tem `type === "client_row"`. Se sim, renderiza linha com nome do cliente, qty, e `friendly_id` do pacote como subtitle. Senão, mantém renderização atual de pacote.
- `renderDetail`: quando item é cliente-row, painel mostra:
  - Header: nome do cliente, qty, valor.
  - Subtitle: `friendly_id` do pacote + produto.
  - Botão "Marcar enviado" → `POST /api/dashboard/packages/{pkg}/clients/{cli}/advance?to=enviado`.
  - Botão "Ver detalhes" (drilldown) → abre modal do pacote inteiro (sem mudança no modal).
- `canDoAdvance("separado", "enviado")` já passa pra `logistica` — sem mudança em permissões.

**`static/dashboard/lib.js`**

- `primaryActionFor`: separa caso `client_row_separado` → label "Marcar enviado" (já é igual a "separado" hoje, mas separa pra clareza).

### Schema / migração

**`deploy/postgres/schema.sql`**: sem mudança (campos já existem).

**Migration SQL** (one-shot, `BEGIN;...COMMIT;` com pré-check):

```sql
BEGIN;
UPDATE pacote_clientes pc
   SET shipped_at = p.shipped_at
  FROM pacotes p
 WHERE pc.pacote_id = p.id
   AND p.shipped_at IS NOT NULL
   AND pc.shipped_at IS NULL;
COMMIT;
```

Roda manualmente em prod após deploy (não automatizada no CI — segue padrão dos outros deploys SQL).

### Testes

- `tests/unit/test_dashboard_packages_endpoint.py`: novos casos:
  - Pacote em `separado` expande em N cliente-rows.
  - Pacote em `enviado` (todos `pc.shipped_at`) expande em N cliente-rows na seção `enviado`.
  - Pacote parcialmente enviado: aparece em ambas as seções (linhas diferentes).
  - Filtro de data em `enviado` usa `pc.shipped_at`.
- `tests/unit/test_dashboard_advance.py`: novos casos:
  - `advance_client` no último cliente sem `shipped_at` seta `pkg.shipped_at`.
  - `advance_package` direto pra `enviado` marca todos os `pc.shipped_at`.
  - `regress_package` em `enviado` zera `pkg.shipped_at` E todos os `pc.shipped_at`.
- Portal service: cliente vê "enviado" no portal mesmo quando outros clientes do pacote ainda não saíram.

## Pontos abertos

Nenhum no momento (métricas e backfill resolvidos no brainstorm).

## Compatibilidade

- Modal de drilldown do pacote (timeline, lista completa de clientes) **não muda**.
- Endpoint `/etiqueta.pdf` continua acessível enquanto `pkg.pdf_sent_at IS NOT NULL` (não muda).
- Outros consumidores de `pkg.shipped_at` (portal cliente) ganham fallback `pc.shipped_at` — sem regressão.
- Buckets `aberto`/`fechado`/`confirmado`/`pago`/`pendente` continuam com shape de pacote (sem breaking change pro JS).

## Rollout

1. Deploy backend + frontend juntos (CI normal).
2. Migration SQL roda manualmente em prod após o deploy verificar verde.
3. Sem feature flag — mudança visual coerente, baixo risco operacional. Se necessário, regredir = revert do commit (a migration é idempotente; rodar de novo não estraga nada).
