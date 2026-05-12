# Aba Financeiro — Gestão de Contas a Receber

**Data:** 2026-05-12
**Status:** Design aprovado

## Contexto

A aba `Financeiro` do dashboard já existe (`templates/index.html:211`) com 3 KPIs (Total Pendente, Pago Hoje, % Recebido), gráfico de Evolução de Vendas e tabela flat de cobranças (1 linha por pagamento) com filtros pendente/pago/cancelado.

O fluxo do Raylook gera muitos pagamentos por cliente — cada pacote confirmado produz uma cobrança individual. A vista flat dificulta enxergar quem deve quanto e há quanto tempo. Não há separação entre "cliente abandonou" e "cliente ainda vai pagar". Não há corte de inadimplência (write-off), então pendentes antigos poluem o total a receber pra sempre.

Esta evolução reformula a aba pra focar em **gestão de contas a receber**: agregação por cliente, aging por idade do débito, ação de write-off por cobrança individual, timeline de tentativas. Mantém o modo flat atual como opção via toggle pra não quebrar o fluxo existente.

## Decisões de escopo

- **Critério de idade do débito:** tempo desde `pagamentos.created_at` (= momento que o pacote foi confirmado). Inclui clientes que nunca clicaram em Pagar.
- **Agrupamento principal:** por cliente, com expand pra cobranças individuais. Toggle preserva modo "por cobrança".
- **Write-off:** cobrança individual (não cliente inteiro).
- **Ações fora de escopo:** envio automático de lembrete (email/WhatsApp), copiar link do portal. Foco da aba é análise/gestão, não automação de cobrança.

## KPIs no topo da aba

Substituem os 3 KPIs atuais.

| Card | Conteúdo | Cálculo |
|---|---|---|
| Total a receber | R$ X — subtítulo `Y cobranças · Z clientes` | `SUM(pagamentos.total_amount)` em `status IN ('created','sent')` (exclui `written_off`, `paid`, `cancelled`) |
| Aging | Mini-stacked-bar com R$ em 4 buckets: `0-7d / 8-15d / 16-30d / 30+d` | Buckets sobre `now() - pagamentos.created_at` |
| Idade média | X dias com seta ↑/↓ vs 7d atrás | Média ponderada por R$. Tendência via comparação com snapshot anterior |
| % pago vs confirmado | XX% — subtítulo `R$ pago / R$ total` | Em janela rolling 30d por `pagamentos.created_at`: `paid / (paid + pending)` |

Cores reusam `kpi-card` existente.

## Tabela

Toggle no canto direito do header: **Por cliente** (default) / **Por cobrança** (modo atual preservado).

Filtros chips: `Todos` · `0-7d` · `8-15d` · `16-30d` · `30+d` · `Perdidos`. Busca por nome/celular igual hoje.

### Modo "Por cliente"

Colunas:
- Nome do cliente
- Celular (últimos 4 dígitos)
- Total devido (R$)
- Cobranças pendentes (count)
- Idade do débito mais antigo (badge colorido por bucket)
- `▶` expand

Expand mostra mini-tabela aninhada com as cobranças do cliente:
- Pacote / Enquete (link pro modal de detalhe do pacote)
- Valor
- Idade (dias)
- Status (`created` / `sent`)
- Ações por linha: `📜 Histórico` · `❌ Marcar como perdido`

Ordenação default: idade do débito mais antigo desc (mais críticos no topo).

### Modo "Por cobrança"

Idêntico ao layout atual da tabela. Garante zero regressão pra quem usa esse modo.

## Backend

### Migration

`deploy/postgres/migrations/F062_pagamento_written_off_status.sql`:

```sql
BEGIN;

ALTER TABLE pagamentos DROP CONSTRAINT IF EXISTS pagamentos_status_check;
ALTER TABLE pagamentos ADD CONSTRAINT pagamentos_status_check
  CHECK (status IN ('created','sent','paid','failed','cancelled','written_off'));

ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_at TIMESTAMPTZ;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_reason TEXT;

CREATE INDEX IF NOT EXISTS pagamentos_written_off_at_idx
  ON pagamentos (written_off_at)
  WHERE written_off_at IS NOT NULL;

COMMIT;
```

Validar em `alana_staging` (ver `.claude/rules/python.md`) antes de prod.

`deploy/sqlite/schema.sql` ganha os mesmos campos e o CHECK atualizado pra paridade de dev. Postgres é fonte da verdade.

### Endpoints novos em `app/routers/finance.py`

| Endpoint | Função |
|---|---|
| `GET /api/finance/receivables?bucket=&q=` | Lista agregada por cliente devedor |
| `GET /api/finance/aging-summary` | KPIs prontos pro frontend |
| `POST /api/finance/pagamentos/{id}/write-off` | Marca como perdido. Body: `{reason: string}`. Idempotente |
| `GET /api/finance/pagamentos/{id}/history` | Timeline derivada do pagamento |

### Lógica em `app/services/finance_service.py`

Adicionar:

- `build_receivables_by_client() -> List[Dict]` — agrega `pagamentos` em `status IN ('created','sent')` por `vendas.cliente_id`, calcula totals, count, oldest age, bucket.
- `build_aging_summary() -> Dict` — retorna `{total_receivable, count, clients_count, buckets: {b0_7, b8_15, b16_30, b30_plus}, avg_age_days, avg_age_trend, paid_rate_30d}`.
- `build_payment_history(pagamento_id) -> List[Dict]` — timeline derivada dos campos existentes (ver tabela abaixo).
- `mark_payment_written_off(pagamento_id, reason) -> Dict` — update + retorno do estado novo.

Reusa `refresh_charge_snapshot()` pra ler os pagamentos quando o snapshot já estiver fresco; senão consulta direto.

### Timeline de histórico (derivada, sem tabela nova)

| Evento | Fonte |
|---|---|
| Pacote confirmado | `pagamentos.created_at` |
| PIX gerado | `pagamentos.updated_at` quando `status='sent'` e `pix_payload IS NOT NULL` (proxy — único timestamp disponível) |
| Último acesso ao portal | `clientes.session_expires_at - 30 dias` (sessão vigente indica acesso recente) |
| Pago | `pagamentos.paid_at` |
| Marcado como perdido | `pagamentos.written_off_at` |

**Limitação assumida:** se o cliente clicou "Pagar" várias vezes, só vemos o último (não há log de cliques). Documentar no modal com tooltip "última tentativa registrada". Futura iteração pode criar tabela `pagamentos_events` se necessário.

## Frontend

### Arquivos a mexer

- `templates/index.html` — substituir bloco `section-finance` (linhas ~211-292) pelo novo layout. Manter `id="section-finance"` e classes existentes (`kpi-card`, `finance-table`, `pagination-bar`).
- `static/js/dashboard.js` — a lógica da aba Financeiro hoje vive embutida nesse arquivo (~3500 linhas) referenciando `section-finance`, `finance-table-body`, `finance-pending-total`, etc. Extrair pra `static/js/finance.js` novo, importar como módulo no `index.html`. A extração entra como parte do PR 4 (frontend) — não como refactor separado.
- `static/css/dashboard.css` — adicionar classes pra mini-stacked-bar do aging, badges de bucket, mini-tabela aninhada do expand.

### Componentes

- **Mini-stacked-bar de aging:** uma barra horizontal dividida em 4 segmentos coloridos (`0-7d` verde, `8-15d` amarelo, `16-30d` laranja, `30+d` vermelho), com tooltip mostrando R$ e count em cada bucket.
- **Badge de bucket:** chip colorido com o número de dias (ex: `12d` em fundo amarelo pra bucket 8-15d).
- **Modal de histórico:** vertical timeline com ícone + data formatada + label. Reusa estilo de modais existentes.
- **Confirmação de write-off:** modal com `Nome do cliente · R$ valor · Pacote X`, campo `Motivo` obrigatório (min 3 chars), botões `Cancelar` / `Confirmar perda`.

## Testes

Em `tests/unit/`:

| Arquivo | Cobertura |
|---|---|
| `test_finance_receivables.py` | `build_receivables_by_client`: agregação por cliente correta, filtro `status IN ('created','sent')`, exclui `written_off/paid/cancelled`, ordenação por idade desc |
| `test_finance_aging.py` | `build_aging_summary`: buckets nos boundaries (7d, 8d, 15d, 16d, 30d, 31d), `paid_rate_30d` em janela rolling, `avg_age_trend` retornando seta correta com snapshot anterior |
| `test_finance_writeoff_endpoint.py` | POST muda status, idempotência (200 quando já `written_off`), 404 quando id inexistente, persiste `written_off_reason`, rejeita reason vazio |
| `test_finance_history.py` | Timeline derivada nos campos certos, ordenação cronológica, robusto a `paid_at`/`written_off_at` null |

Sem necessidade de E2E novo — o smoke test existente da aba Financeiro continua valendo.

## Plano de execução

5 PRs pequenos, mergeáveis em sequência:

1. **Migration + schema.** `F062_pagamento_written_off_status.sql` + atualizar `deploy/sqlite/schema.sql` (CHECK + 2 colunas). Validar em `alana_staging` primeiro.
2. **Backend agregador.** `finance_service.build_receivables_by_client()` + `build_aging_summary()` + `build_payment_history()` + `mark_payment_written_off()` com testes unitários.
3. **Endpoints.** 4 rotas novas em `app/routers/finance.py`.
4. **Frontend.** KPI cards novos, tabela agrupada, toggle de modo, modal histórico, ação write-off. Validar no browser (Playwright MCP).
5. **Cleanup.** Remover KPIs antigos do `section-finance`, ajustar `refresh_charge_snapshot` se KPIs antigos não são mais usados em nenhum lugar, atualizar testes existentes que dependiam dos cards antigos.

Migration primeiro garante que prod aceita o novo status antes do código gravar.

## Verificação end-to-end

Após o PR 5 estar em prod:

1. Acessar `https://raylook.v4smc.com` → aba Financeiro.
2. Confirmar 4 KPIs no topo: Total a receber, Aging (mini-stacked-bar), Idade média (com tendência), % pago vs confirmado.
3. Toggle "Por cliente" / "Por cobrança" funciona — modo cobrança preserva layout antigo.
4. Filtros chips 0-7d / 8-15d / 16-30d / 30+d filtram a lista corretamente.
5. Expandir um cliente devedor mostra as cobranças individuais.
6. Clicar "Histórico" abre modal com timeline.
7. Clicar "Marcar como perdido" exige motivo, confirma, e a cobrança some do "Total a receber" mas aparece no filtro `Perdidos`.
8. `SELECT status, COUNT(*) FROM pagamentos GROUP BY status` em `raylook` mostra a coluna `written_off` populada após write-off.
9. Logs do scheduler `asaas_sync_service` não tentam sincronizar pagamentos `written_off` (filtro `status IN ('created','sent')` já garante isso, mas verificar).

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Modo "Por cliente" esconde pagos/cancelados que o operador esperava ver | Toggle "Por cobrança" preserva comportamento atual completo |
| `paid_rate_30d` cai em meses com confirmações tardias | Janela rolling 30d (não calendar month) |
| Write-off acidental | Modal de confirmação com nome+valor + reason obrigatório |
| Histórico impreciso (só último clique visível) | Documentado; tooltip "última tentativa registrada"; iteração futura pode criar `pagamentos_events` |
| Migration quebra em prod | Validar em `alana_staging` antes; usar `IF NOT EXISTS` nos `ALTER TABLE ... ADD COLUMN` |
