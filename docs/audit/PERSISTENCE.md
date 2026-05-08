# Alana Dashboard — Matriz de Persistência e Rastreabilidade

> Auditoria completa do que é **persistido permanentemente** vs o que é
> **calculado em tempo real** vs o que tem **snapshot histórico imutável**.
>
> Última atualização: 2026-04-09 (F-045 + F-046)
>
> ## Garantia de confiabilidade
>
> A partir de **2026-04-09 04:00 UTC**, as seguintes propriedades estão garantidas
> para todos os meses subsequentes:
>
> 1. **Todos os KPIs agregados do dash** estão snapshottados hora-a-hora em
>    `metrics_hourly_snapshots` (F-045). Edições retroativas em dados brutos
>    NÃO afetam linhas de horas passadas. Cobertura: votos, enquetes, pacotes,
>    financeiro, clientes, fila WhatsApp, webhook inbox.
> 2. **Breakdowns por entidade** (top clientes, top enquetes, votos por hora,
>    by_poll_today, by_customer_today/week) estão dentro de
>    `metrics_hourly_snapshots.raw_stats.metrics_full` (F-045 v2).
> 3. **Saúde do worker** monitorável via `GET /api/metrics/health`. Detecta
>    gaps na janela e retorna `ok`/`degraded`/`critical`.
> 4. **Edições em campos mutáveis** (`clientes.nome`, `enquetes.titulo`,
>    `pacotes.custom_title`, `pacotes.tag`, `pacote_clientes.unit_price`)
>    geram registro automático em `field_history` via triggers Postgres
>    (F-046). É possível reconstruir "qual era o nome do cliente em 2026-03-15"
>    via `field_value_at('clientes', id, 'nome', '2026-03-15')`.
>
> Limitação remanescente: dados brutos `votos`, `pacotes`, `vendas`, `pagamentos`
> permitem edição direta via SQL (não via API). Se alguém com acesso ao banco
> rodar `UPDATE` direto, os snapshots de horas passadas continuam corretos
> (são imutáveis), mas o `field_history` só captura mudanças nos campos com
> trigger. Para auditoria mais ampla, considere `pgaudit` no futuro.

---

## 1. Princípios

1. **Dados brutos (eventos do domínio)** → Postgres, append-only quando possível.
2. **KPIs agregados** → calculados em tempo real a partir dos dados brutos + **gravados em snapshot horário imutável** (F-045).
3. **Qualquer mudança de estado** que queremos preservar pro histórico deve estar em uma tabela com timestamps e, idealmente, em uma tabela de eventos.

---

## 2. Matriz de persistência

### ✅ Persistido permanentemente no Postgres (append-only ou com history via timestamps)

| Dado | Tabela | Campo temporal | Mutável? | Notas |
|---|---|---|---|---|
| Enquete criada | `enquetes` | `created_at_provider`, `created_at`, `updated_at` | status + título (F-026/F-032) | `external_poll_id` é UNIQUE imutável. |
| Opções de voto | `enquete_alternativas` | `created_at` | não após criada | |
| Cliente | `clientes` | `created_at`, `updated_at` | nome (F-036) | Telefone (`celular`) é UNIQUE imutável. |
| Voto | `votos` | `voted_at`, `updated_at` | qty + status (upsert por `enquete_id+cliente_id`) | |
| **Evento de voto** | **`votos_eventos`** | **`occurred_at`** | **APPEND-ONLY** | **Audit trail completo de cada vote/remove/sync**. |
| Pacote | `pacotes` | `created_at`, `opened_at`, `closed_at`, `approved_at`, `cancelled_at`, `updated_at` | status + tag + custom_title | 4 marcadores temporais cobrem todas as transições. |
| Cliente no pacote | `pacote_clientes` | `created_at`, `updated_at` | unit_price + status (F-033) | Linka voto_id origem. |
| Venda | `vendas` | `created_at`, `sold_at`, `updated_at` | status (futuro F-006) | 1:1 com pacote_cliente. |
| Pagamento | `pagamentos` | `created_at`, `paid_at`, `updated_at` | status | 1:1 com venda. `provider_payment_id` linka Asaas. |
| Produto | `produtos` | `created_at`, `updated_at` | nome, valor, drive_file_id | |
| Webhook recebido | `webhook_inbox` | `received_at`, `processed_at` | status + error | APPEND-ONLY (idempotente via `event_key` UNIQUE). |

### 🟡 Estado transitório / runtime (não histórico)

| Dado | Tabela / Arquivo | Propósito | Durabilidade |
|---|---|---|---|
| `payment_queue` (jobs WhatsApp) | `app_runtime_state` (jsonb) | Fila de envio | Até a mensagem ser enviada ou marcada como `error`. Histórico breve dos sent. |
| `finance_charges_rows` | `app_runtime_state` | Cache do snapshot de charges | Invalidado a cada mudança. |
| `finance_dashboard_stats` | `app_runtime_state` | Cache de KPIs | Invalidado a cada mudança. |
| `customer_rows` | `app_runtime_state` | Cache de clientes + qty + debt | Invalidado a cada mudança. |
| `data/payments.json` | Arquivo local | Legacy FinanceManager (baserow path) | Não usado no modo Supabase. |

### ✅ Snapshot histórico imutável (F-045)

| Dado | Tabela | Frequência | Propósito |
|---|---|---|---|
| KPIs agregados (votos, financeiro, pacotes, fila, etc) | **`metrics_hourly_snapshots`** | **1 linha por hora cheia** | **Rastreabilidade histórica imutável**. Mesmo que dados brutos sejam editados, o snapshot da hora passada permanece. |

Colunas do snapshot (30+):
- **Votos**: `votes_today_so_far`, `votes_last_24h`, `votes_hour_delta`
- **Enquetes**: `enquetes_total`, `enquetes_open`, `enquetes_closed`, `enquetes_created_today`
- **Pacotes**: `pacotes_open`, `pacotes_closed`, `pacotes_approved`, `pacotes_cancelled`, `pacotes_approved_today`
- **Financeiro**: `total_pending_brl`, `total_paid_brl`, `total_paid_today_brl`, `total_cancelled_brl`, `pending_count`, `paid_count`, `cancelled_count`, `active_count`, `conversion_rate_pct`
- **Clientes**: `customers_total`, `customers_with_debt`
- **Fila WhatsApp**: `queue_queued`, `queue_sending`, `queue_retry`, `queue_error`, `queue_sent`
- **Webhook inbox (saúde)**: `webhook_received`, `webhook_processed`, `webhook_failed`
- **Meta**: `raw_stats jsonb` (extensível)

---

## 3. Como o worker funciona

**Código**: `app/workers/metrics_snapshot_worker.py`

**Fluxo:**
1. No startup do app, task `metrics_snapshot_loop()` é iniciada via `asyncio.create_task`
2. Captura imediata (`capture_once()`) pra ter ao menos 1 linha logo no startup
3. Loop infinito:
   - Calcula segundos até a próxima hora cheia (ex: agora 14:23 → 37min)
   - `asyncio.sleep(seconds)`
   - `capture_once()` → grava snapshot
   - Repete

**Idempotência**: `UNIQUE (hour_bucket)` + UPSERT `on_conflict=hour_bucket`. Se o worker cair e re-rodar no mesmo minuto, apenas atualiza a linha. Linhas de horas passadas nunca são tocadas por re-run (o bucket muda).

**Reprocessamento manual**:
```bash
# Forçar snapshot da hora atual
curl -X POST https://staging-alana.v4smc.com/api/metrics/snapshot
```

Ou diretamente via Python:
```python
from app.workers.metrics_snapshot_worker import capture_once
import asyncio
asyncio.run(capture_once())
```

---

## 4. Como consultar o histórico

### Via API

```bash
# Últimas 48h (default)
GET /api/metrics/history

# Últimas 7 dias
GET /api/metrics/history?hours=168

# Janela específica
GET /api/metrics/history?from_ts=2026-04-01T00:00:00Z&to_ts=2026-04-09T00:00:00Z
```

Resposta:
```json
{
  "from": "2026-04-07T04:00:00+00:00",
  "to": "2026-04-09T04:00:00+00:00",
  "count": 2,
  "items": [
    {
      "id": "uuid",
      "hour_bucket": "2026-04-09T04:00:00+00:00",
      "votes_today_so_far": 0,
      "total_pending_brl": 813.60,
      "total_paid_brl": 1356.00,
      "pacotes_open": 511,
      "pacotes_approved": 157,
      "customers_total": 552,
      ...
    }
  ]
}
```

### Via SQL direto

```sql
-- Últimas 24h de snapshots
SELECT hour_bucket, votes_today_so_far, total_pending_brl, total_paid_brl
FROM metrics_hourly_snapshots
WHERE hour_bucket >= now() - interval '24 hours'
ORDER BY hour_bucket DESC;

-- Comparação hora a hora do mesmo dia da semana
SELECT hour_bucket, votes_today_so_far
FROM metrics_hourly_snapshots
WHERE extract(dow FROM hour_bucket) = extract(dow FROM now())
  AND extract(hour FROM hour_bucket) = extract(hour FROM now())
ORDER BY hour_bucket DESC
LIMIT 10;

-- Evolução do total pendente nos últimos 7 dias
SELECT date_trunc('day', hour_bucket) AS dia,
       max(total_pending_brl) AS peak_pending,
       min(total_pending_brl) AS min_pending,
       avg(total_pending_brl)::numeric(12,2) AS avg_pending
FROM metrics_hourly_snapshots
WHERE hour_bucket >= now() - interval '7 days'
GROUP BY 1
ORDER BY 1 DESC;
```

---

## 5. Rastreabilidade — casos de uso

### Caso 1: "Como estava o total pendente ontem às 14h?"

**Antes do F-045:** impossível sem reprocessar toda a base, e mesmo assim o resultado podia ser diferente se alguém editou dados depois.

**Depois do F-045:**
```sql
SELECT hour_bucket, total_pending_brl, pending_count
FROM metrics_hourly_snapshots
WHERE hour_bucket = date_trunc('hour', now() - interval '1 day' - interval '2 hours')
  + interval '14 hours';
```
Resposta imutável. Mesmo que alguém cancele uma venda hoje, essa linha mostra o que estava pendente naquele instante.

### Caso 2: "Meu dash mostra -26% vs ontem, isso é confiável?"

Sim. O cálculo atual no `processors.py:analyze_votos` lê os votos brutos que são imutáveis (voted_at, qty). A comparação "vs ontem" usa o timestamp do voto, não agregação computada.

A única fonte de desvio são **mudanças retroativas** (ex: voto cancelado depois). Com F-045 você pode cruzar com o snapshot da hora passada pra detectar discrepância:
```sql
SELECT hour_bucket, votes_today_so_far
FROM metrics_hourly_snapshots
WHERE hour_bucket = date_trunc('hour', now() - interval '1 day');
```

### Caso 3: "Quantos pacotes estavam abertos na semana passada às sextas?"

```sql
SELECT hour_bucket, pacotes_open
FROM metrics_hourly_snapshots
WHERE extract(dow FROM hour_bucket) = 5  -- sexta
  AND hour_bucket >= now() - interval '30 days'
ORDER BY hour_bucket DESC;
```

### Caso 4: "A fila de WhatsApp travou?"

```sql
SELECT hour_bucket, queue_queued, queue_sending, queue_retry, queue_error, queue_sent
FROM metrics_hourly_snapshots
ORDER BY hour_bucket DESC
LIMIT 24;
```
Se `queue_queued` ou `queue_retry` cresce sem parar e `queue_sent` estagna, o worker travou.

### Caso 5: "O webhook está processando?"

```sql
SELECT hour_bucket, webhook_received, webhook_processed, webhook_failed
FROM metrics_hourly_snapshots
ORDER BY hour_bucket DESC
LIMIT 12;
```
Se `webhook_received` cresce, mas `webhook_processed` não acompanha, tem backlog. O worker de retry (F-003) deveria estar puxando.

---

## 6. Audit trail de campos mutáveis (F-046 + F-049)

Triggers Postgres registram qualquer UPDATE em campos mutáveis críticos
+ **transições de status** na tabela `field_history`. Append-only.

| Tabela | Campos auditados | Trigger |
|---|---|---|
| `clientes` | `nome` | `clientes_audit_trg` |
| `enquetes` | `titulo`, **`status`** | `enquetes_audit_trg` |
| `pacotes` | `custom_title`, `tag`, **`status`** | `pacotes_audit_trg` |
| `pacote_clientes` | `unit_price` | `pacote_clientes_audit_trg` |
| `vendas` | **`status`** | `vendas_audit_trg` |
| `pagamentos` | **`status`** | `pagamentos_audit_trg` |

Com isso, qualquer transição `open → closed → approved/cancelled` de um
pacote, ou `pending → sent → paid/cancelled` de um pagamento, fica
registrada com timestamp, valor anterior, valor novo, e role que fez a
mudança (`current_user`).

### Exemplo: rastrear o ciclo de vida de um pacote

```sql
SELECT changed_at, field_name, old_value, new_value, changed_by
FROM field_history
WHERE table_name = 'pacotes' AND record_id = '<pacote_id>'
ORDER BY changed_at ASC;
-- Retorna linha do tempo completa: quando abriu, fechou, confirmou, cancelou.
```

### Exemplo: todos os pacotes cancelados nas últimas 24h

```sql
SELECT record_id, old_value AS era, changed_at, changed_by
FROM field_history
WHERE table_name='pacotes' AND field_name='status' AND new_value='cancelled'
  AND changed_at >= now() - interval '24 hours'
ORDER BY changed_at DESC;
```

### Consultar o valor que um campo tinha numa data passada

```sql
-- Qual era o nome do cliente <id> em 2026-03-15?
SELECT public.field_value_at('clientes', '<id>', 'nome', '2026-03-15'::timestamptz);
-- Retorna NULL se nunca mudou desde então (use o valor atual).

-- Histórico completo de mudanças num pacote:
SELECT changed_at, field_name, old_value, new_value, changed_by
FROM field_history
WHERE table_name = 'pacotes' AND record_id = '<pacote_id>'
ORDER BY changed_at DESC;
```

### Débito remanescente (não-crítico)

| Dado | Por que ainda não | Como tratar no futuro |
|---|---|---|
| Edição direta SQL bypass dos triggers | Triggers só pegam UPDATEs no banco; quem usa `psql` pode contornar com `ALTER TABLE DISABLE TRIGGER` | Considerar `pgaudit` em prod |
| Por-cliente histórico de débito | Derivável cruzando snapshots horários com `pacote_clientes` | Adicionar coluna `customers_with_debt_detail jsonb` no snapshot se necessário |
| Rolling 7 dias de votos calculado em tempo real | OK enquanto `votos_eventos` for append-only | Se `analyze_votos` ficar lento, agregar via snapshots |

---

## 7. Comandos operacionais

### Verificar o worker está vivo
```bash
docker service logs --since 5m alana-staging_alana-dashboard 2>&1 | grep metrics_snapshot
```
Deveria aparecer "metrics_snapshot_loop iniciado" no startup e "dormindo Xs até próxima hora cheia" periodicamente.

### Forçar snapshot agora
```bash
curl -X POST https://staging-alana.v4smc.com/api/metrics/snapshot
```

### Contar snapshots no banco
```sql
SELECT count(*), min(hour_bucket), max(hour_bucket)
FROM metrics_hourly_snapshots;
```

### Ver a saúde do worker (últimas 6 horas, gaps)
```sql
SELECT hour_bucket,
       lag(hour_bucket) OVER (ORDER BY hour_bucket) AS previous,
       hour_bucket - lag(hour_bucket) OVER (ORDER BY hour_bucket) AS gap
FROM metrics_hourly_snapshots
WHERE hour_bucket >= now() - interval '6 hours'
ORDER BY hour_bucket DESC;
```
Todos os `gap` deveriam ser `01:00:00` (uma hora). Gaps maiores = worker ficou fora do ar naquele período.

### Backup manual do snapshot (exportar)
```bash
docker exec $PG pg_dump -U postgres -d alana_staging -t metrics_hourly_snapshots \
  --data-only --column-inserts > snapshots_backup_$(date +%F).sql
```

---

## 8. Healthcheck do worker

```bash
curl https://staging-alana.v4smc.com/api/metrics/health?window_hours=24
```

Retorna:
```json
{
  "status": "ok",
  "reason": "sem gaps, worker saudável",
  "window_hours": 24,
  "snapshot_count": 24,
  "expected_count": 24,
  "gaps": [],
  "last_snapshot_at": "2026-04-09T04:00:00+00:00",
  "minutes_since_last": 12.5
}
```

Status:
- `ok` → snapshot recente (<2h) e zero gaps
- `degraded` → snapshot recente mas tem gaps (worker falhou pontualmente)
- `critical` → snapshot mais recente é antigo (>2h) ou sem snapshots

Recomendado: scrape via Uptime Kuma / cronjob, alertar quando `status != "ok"`.

---

## 9. Referências

- Migration F-045: `deploy/postgres/migration_F045_metrics_snapshots.sql`
- Migration F-046: `deploy/postgres/migration_F046_field_history.sql`
- Worker: `app/workers/metrics_snapshot_worker.py`
- Endpoints: `main.py` → `GET /api/metrics/history`, `POST /api/metrics/snapshot`, `GET /api/metrics/health`
- Findings: `docs/audit/FINDINGS.md#F-045`, `#F-046`
