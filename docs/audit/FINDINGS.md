# Alana Dashboard — Findings (Auditoria)

> Achados da auditoria por severidade. Cada achado é **incremental** — ver regras no `README.md`.
> **Versão:** v1 (2026-04-08) | **Ambiente:** `alana_staging`

**Legenda:**
- 🔴 **SEV-1 Crítico** — perda/corrupção de dados, segurança grave, bug ativo causando inconsistência financeira visível
- 🟠 **SEV-2 Alto** — inconsistência visível mas contornável, bug recorrente
- 🟡 **SEV-3 Médio** — risco lógico/code smell, race condition teórica
- 🟢 **SEV-4 Baixo** — higiene, dívida técnica

**Status de cada finding:** `aberto`, `em-análise`, `fix-proposto`, `fix-aplicado`, `verificado-fechado`, `não-aplicável`

---

## 🔴 SEV-1 — Crítico

### F-001 — 25 pacotes `closed` sem `pacote_clientes` (pacotes fantasma)

**Status:** ✅ fix-aplicado + cleanup (2026-04-08) — root cause em whatsapp_domain_service.py:509-552. Substituído por RPC `close_package` transacional com `pg_advisory_xact_lock`. Validado end-to-end (4 votos → pacote criado, 4 pacote_clientes inseridos, 4 votos atualizados pra in, tudo numa transação) + teste de concorrência (2 chamadas paralelas geraram seq=2 e seq=3 sem conflito). Migration aplicada: deploy/postgres/migration_F001_close_package_rpc.sql. Deploy: alana-dashboard:staging-audit-3389395.

**Root cause (confirmada 2026-04-08 no código convergido):**

Em `app/services/whatsapp_domain_service.py:509-552`, o fluxo de fechamento de pacote é:

```python
# linha 509: INSERT pacote (commit 1)
pacote = self.client.insert("pacotes", {...status="closed"...})[0]

# linha 523-552: loop N vezes (N = len(subset)) — cada iteração é 1 round-trip PostgREST
for vote in subset:
    self.client.insert("pacote_clientes", {...})  # commit 2..N+1
    self.client.update("votos", {"status": "in"}, ...)  # commit N+2..2N+1
```

Cada chamada `.insert()` / `.update()` é um **HTTP request separado** ao PostgREST, cada um em sua própria transação. Se o processo crashar/timeoutar/perder conexão entre os commits, o pacote fica **`status='closed'` mas sem pacote_clientes** — exatamente o que vi nos 25 casos.

**Fix sugerido atualizado:**
Mover o fluxo inteiro pra uma RPC Postgres:

```sql
CREATE OR REPLACE FUNCTION close_package(p_enquete_id uuid, p_votes jsonb, ...)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
    -- tudo em uma transação
    PERFORM pg_advisory_xact_lock(hashtext(p_enquete_id::text));
    INSERT INTO pacotes (...) RETURNING * INTO v_pacote;
    -- inserir todos pacote_clientes de uma vez
    INSERT INTO pacote_clientes SELECT ... FROM jsonb_array_elements(p_votes);
    -- atualizar todos votos de uma vez
    UPDATE votos SET status='in' WHERE id = ANY(...);
    RETURN jsonb_build_object('pacote_id', v_pacote.id);
END $$;
```

E chamar via `client.rpc("close_package", {...})`.
**Descoberto em:** 2026-04-08
**Impacto:** Pacotes aparecem no dashboard como "fechados prontos pra confirmar", mas ao confirmar **não geram vendas nem PDF** (porque não há clientes associados). Inconsistência visual + operacional.

**Evidência:**

```sql
SELECT date_trunc('day', created_at)::date AS dia, count(*)
FROM pacotes p
WHERE p.status='closed'
  AND NOT EXISTS (SELECT 1 FROM pacote_clientes pc WHERE pc.pacote_id=p.id)
GROUP BY 1 ORDER BY 1 DESC;
--    dia     | count
-- 2026-04-07 |    14
-- 2026-04-06 |    10
-- 2026-04-04 |     1
```

São **recentes** (últimos 3 dias) — coincide com o ciclo de 60+ imagens `staging-*fix*` recém-buildadas.

**Hipótese:** Race condition entre `PackageService.rebuild_for_poll()` e um delete concorrente. Corroborado por F-004 (erros `23503` no `webhook_inbox`: "Key (pacote_id)=... is not present in table pacotes" e "still referenced from pacote_clientes").

**Fix sugerido:**
1. Transacionar `INSERT pacote + INSERT pacote_clientes` via RPC Postgres.
2. Script one-shot para: (a) deletar os 25 pacotes órfãos OU (b) recalcular a partir de `votos` ativos.
3. Adicionar `CHECK` no banco: após commit, `status='closed' ⇒ EXISTS(pacote_clientes)` (via função + constraint trigger).

---

### F-002 — 1140 vendas `approved` em pacotes `cancelled` com 1034 pagamentos `paid`

**Status:** ✅ verificado-fechado (2026-04-08) — **decisão de negócio**: pacote cancelado preserva vendas/pagamentos. O operador pode alterar manualmente o status de pagamento (pendente ↔ pago) pelo dash clicando no badge de status. Toggle **já implementado**: frontend `static/js/dashboard.js:482-511` chama `PATCH /api/finance/charges/{id}/status` com body `{status: paid|pending}`; backend `main.py:1871` seta `status` e atualiza `paid_at`. Funcional tanto para marcar pago quanto reverter pra pendente.
**Impacto:** **Inconsistência financeira visível**. Há dinheiro recebido (PIX pago) registrado contra pacotes cancelados. Qualquer relatório de receita mente.

**Evidência:**

```sql
SELECT p.status::text, count(v.*) FROM vendas v JOIN pacotes p ON p.id=v.pacote_id GROUP BY p.status;
--   approved  |     2
--   cancelled |  1140

SELECT pg.status::text, count(*)
FROM pagamentos pg JOIN vendas v ON v.id=pg.venda_id JOIN pacotes p ON p.id=v.pacote_id
WHERE p.status='cancelled' GROUP BY pg.status;
--  sent   |   106
--  paid   |  1034
```

**Hipótese:** Relacionado a F-005 — os 245 pacotes `cancelled` são todos do mesmo dia (2026-03-30) com `cancelled_at/cancelled_by/approved_at = NULL`, ou seja, **bulk UPDATE direto no banco** sem passar pelo fluxo de `/reject`. Provavelmente um script de migração/correção marcou como `cancelled` mas não cascateou vendas/pagamentos.

**Fix sugerido:**
1. Decidir regra de negócio: pacote cancelado **mantém** ou **cancela** vendas? Hoje `venda_status` só tem `approved` — máquina de estados incompleta (ver F-006).
2. Adicionar `cancelled`/`refunded` no enum `venda_status` e `pagamento_status`.
3. Migração: para cada `pacote.status='cancelled'`, mover vendas associadas para estado correspondente baseado no estado do pagamento (paid → manter como caso especial; unpaid → cancelar).

---

### F-003 — 9 votos parados em `webhook_inbox status='received'` há até 25 dias

**Status:** **fix-aplicado** (2026-04-08, commit `f1711d0`)

**Fix aplicado:** Novo worker async `app/workers/webhook_retry.py` iniciado no startup do FastAPI. Varre a cada 60s `webhook_inbox WHERE status='received' AND received_at < now() - '5 minutes'`, reprocessa via `normalize_webhook_events` + `poll_service`/`vote_service`, marca como `processed` ou `failed`. Batch de 20 por ciclo.

**Validação pós-deploy (imagem `staging-audit-f1711d0`):**
- Antes: 9 registros em `status='received'` parados há 11-25 dias
- Depois: **0 registros em received**, 9 marcados como `failed` com erro `retry_failed_normalize` (payloads antigos de formato que o parser atual não reconhece mais — eventos irrelevantes a essa altura)

Problema estruturalmente resolvido. Novos zumbis (caso ocorram) serão processados corretamente pelo parser atual.
**Impacto:** Votos reais de clientes **perdidos** — entraram no webhook mas nunca foram processados. `received` deveria transicionar para `processed` ou `failed` em segundos.

**Evidência:**

```sql
SELECT provider, event_kind, age(now(), received_at) AS age
FROM webhook_inbox WHERE status='received' ORDER BY received_at DESC LIMIT 10;
--  whapi | vote_updated | 11 days 02:12
--  whapi | vote_updated | 23 days 23:56
--  whapi | vote_updated | 24 days 23:01  (x6)
--  whapi | vote_updated | 25 days 20:27
```

**Hipótese:** O processamento é **síncrono dentro do request HTTP** (`main.py:155` → `VoteService.process_vote`). Se o request travou/crashou no meio depois de inserir em `webhook_inbox` mas antes de atualizar o status, o registro fica órfão. **Não há worker de retry**.

**Fix sugerido:**
1. Criar worker periódico que varre `webhook_inbox WHERE status='received' AND received_at < now() - interval '5 minutes'` e re-processa.
2. Tornar o processamento realmente assíncrono: 202 imediato, `asyncio.create_task` pra processar.
3. Limite de retries com backoff; após N falhas marca `failed`.

---

### F-004 — 44 `webhook_inbox` failed revelam race conditions reais

**Status:** 🟡 fix-parcial-aplicado (2026-04-08) — race de sequence_no duplicado eliminada pelo F-001 (advisory lock). Race de FK pacote_id ainda pode acontecer em deletes paralelos (improvável após F-001).
**Impacto:** Padrão de falhas expõe que as race conditions suspeitas (mapeamento agent §7) **estão acontecendo em produção**, não são teóricas.

**Evidência (agrupadas por tipo de erro):**

| Tipo de erro | Código Postgres | Ocorrências | O que significa |
|---|---|---|---|
| `Cannot coerce result to single JSON object` | PGRST116 | 14 | SELECT `single=True` esperava 1 linha, recebeu 0. SELECT-seguido-de-INSERT sem lock. |
| Duplicate `(enquete_id, sequence_no)` | 23505 | ~10 | Duas threads geraram o mesmo `sequence_no` e tentaram INSERT. `next_pacote_sequence` não é chamado dentro da mesma transação. |
| `pacote_id not present in pacotes` | 23503 | 6 | INSERT em `pacote_clientes` apontando para pacote já deletado. Race delete↔insert. |
| `still referenced from pacote_clientes` | 23503 | 6 | DELETE de pacote enquanto havia `pacote_clientes` filhos. FK sem CASCADE. |
| CHECK constraint violation | 23514 | 3 | INSERT de pacote violando `capacidade_total>0` ou similar. |
| SSL handshake timeout | — | 2 | Conexão PostgREST instável. |
| HTTP 500 genérico PostgREST | — | 1 | Provável crash/timeout do PostgREST. |

**Fix sugerido:**
1. **Advisory lock por `enquete_id`** no início de `rebuild_for_poll` (evita duas threads mexerem nos pacotes da mesma enquete em paralelo).
2. Transacionar todo `rebuild_for_poll` via RPC Postgres.
3. Adicionar `ON DELETE CASCADE` em `pacote_clientes.pacote_id` OU bloquear delete via regra de negócio.
4. Investigar porquê deletes de pacote estão acontecendo (não há endpoint de `DELETE /pacote`; alguém tá fazendo SQL direto?).

---

### F-005 — 245 pacotes `cancelled` com metadados NULL (bulk UPDATE forense)

**Status:** ✅ verificado-fechado (2026-04-08) — usuário confirmou que é pra manter o registro como está. Ver F-002. Origem do bulk update de 2026-03-30 não foi identificada mas é aceitável pro negócio. Nenhuma ação tomada.
**Impacto:** Histórico operacional corrompido — não dá pra saber quem/quando/porquê esses pacotes foram cancelados. Relatórios de auditoria mentem.

**Evidência:**

```sql
SELECT count(*) FILTER (WHERE cancelled_at IS NULL) AS sem_data,
       count(*) FILTER (WHERE approved_at IS NULL) AS nunca_aprovado,
       count(*) FILTER (WHERE cancelled_by IS NULL) AS sem_autor
FROM pacotes WHERE status='cancelled';
-- sem_data=245, nunca_aprovado=245, sem_autor=245

SELECT date_trunc('day', created_at)::date, count(*)
FROM pacotes WHERE status='cancelled' GROUP BY 1;
-- 2026-03-30 | 245
```

**Hipótese:** Script de migração/correção em 2026-03-30 fez `UPDATE pacotes SET status='cancelled' WHERE ...` sem preencher `cancelled_at/cancelled_by`. Relacionado a F-002.

**Fix sugerido:**
1. Identificar o script que rodou (procurar em `/root/alana-staging-*/scripts/`, git log se disponível, logs do VPS).
2. Backfill: `UPDATE pacotes SET cancelled_at='2026-03-30 12:00+00', cancelled_by='migration-script-20260330' WHERE status='cancelled' AND cancelled_at IS NULL;`
3. Adicionar `CHECK (status='cancelled' => cancelled_at IS NOT NULL)` no banco.

---

## 🟠 SEV-2 — Alto

### F-006 — Máquina de estados de `vendas` incompleta (só `approved`)

**Status:** aberto
**Impacto:** Impossível cancelar/estornar uma venda no modelo. Por isso F-002 existe — quando o pacote cancela, a venda não tem pra onde ir.

**Evidência:**
```sql
SELECT DISTINCT status FROM vendas;
-- approved   (único valor)
```

**Fix sugerido:** Adicionar `cancelled`, `refunded` ao enum `venda_status` e implementar transições em `SalesService`.

---

### ~~F-007 — `closed_at < created_at` em 1181 pacotes~~ → REBAIXADO PARA SEV-4 / OBSERVAÇÃO

**Status:** verificado-fechado (não é bug)
**Resolução em 2026-04-08:** Usuário confirmou que `closed_at` deve representar **o horário em que o membro que fechou o pacote votou** (timestamp original do WhatsApp), não o horário de inserção no banco. Portanto `closed_at < created_at` é o **comportamento esperado** para dados que foram inseridos via batch/replay/migração — `created_at` é "quando a linha entrou no Postgres", `closed_at` é "quando o último voto que completou os 24 foi enviado no WhatsApp".

**Implicação para outros findings:**
- `created_at` **não** é confiável pra ordenação cronológica do negócio. Sempre usar `closed_at` (ou `opened_at`) em relatórios temporais.
- Documentado em `SYSTEM.md §2.3` como regra explícita.
- Não adicionar `CHECK (closed_at >= created_at)` — quebraria a semântica.

---

### F-008 — 29 pacotes `closed` com contadores divergentes

**Status:** ✅ fix-aplicado (2026-04-08). UPDATE recalculou `participants_count=(SELECT count(*) FROM pacote_clientes)` e `total_qty=(SELECT sum(qty))` para os 29 casos. 0 divergências restantes. Descoberta: 25 desses 29 eram os órfãos do F-001 (closed sem pacote_clientes) — esses foram marcados como `cancelled` com `cancelled_by='f001_orphan_cleanup_20260408'` (reversível).
**Impacto:** Dashboard mostra `participants_count`/`total_qty` diferentes do número real de clientes no pacote. Operador toma decisões erradas.

**Evidência:**

```sql
SELECT p.status::text, count(*) FILTER (WHERE p.participants_count <> (SELECT count(*) FROM pacote_clientes pc WHERE pc.pacote_id=p.id)) AS divergentes, count(*) AS total
FROM pacotes p GROUP BY p.status;
--   open     |  936 / 936  (by-design: open não materializa pacote_clientes)
--   closed   |   29 / 781
--   cancelled|  245 / 245
--   approved |    0 / 154
```

Amostra:
```
bfbb7610... seq=7 | participants_count=8 | real=2 | total_qty=24 | real_qty=6
ca95608b... seq=1 | participants_count=5 | real=3 | total_qty=24 | real_qty=12
```

**Hipótese:** Clientes saíram do `pacote_clientes` (delete externo ou efeito colateral de reingestão) mas os contadores denormalizados não foram atualizados.

**Fix sugerido:**
1. Recalcular: `UPDATE pacotes SET participants_count=(SELECT count(*) ...), total_qty=(SELECT sum(qty) ...) WHERE status='closed'`.
2. Eliminar contadores denormalizados (usar VIEW), OU
3. Trigger que mantém sincronizado em cada INSERT/DELETE de `pacote_clientes`.

---

### F-009 — Endpoints administrativos sem autenticação

**Status:** ✅ fix-aplicado (2026-04-08, opt-in) — novo middleware HTTP Basic Auth em `main.py`. Ativação via env vars `DASHBOARD_AUTH_ENABLED=true`, `DASHBOARD_AUTH_USER`, `DASHBOARD_AUTH_PASS`. Rotas sempre públicas: `/health`, `/api/supabase/health`, `/webhook/whatsapp`, `/webhook/asaas`, `/static/*`, `/metrics`. Comparação constant-time via hmac.compare_digest. Default desligado pra backward compat — ativar quando quiser com `docker service update --env-add DASHBOARD_AUTH_ENABLED=true --env-add DASHBOARD_AUTH_USER=admin --env-add DASHBOARD_AUTH_PASS=... alana-staging_alana-dashboard`.
**Impacto:** Qualquer cliente HTTP que alcance `staging-alana.v4smc.com` pode confirmar/rejeitar/reverter pacotes sem credenciais.

**Evidência:** `SYSTEM.md §4.3`. Endpoints listados:
- `POST /api/packages/{id}/confirm` (main.py:449)
- `POST /api/packages/{id}/reject` (main.py:740)
- `POST /api/packages/{id}/revert` (main.py:757)
- `POST /api/packages/{id}/retry_payments` (main.py:774)
- `POST /api/packages/backfill-routing` (main.py:792)
- `POST /api/refresh` (main.py:252)

**Fix sugerido:** Middleware FastAPI validando bearer token (mesmo secret simples já ajudaria). Prod deveria ter auth real (OAuth/OIDC).

---

### F-010 — `WHATSAPP_WEBHOOK_SECRET` opcional (se vazio, webhook aceita qualquer payload)

**Status:** 🟡 fix-parcial (2026-04-08). main.py agora loga WARNING em cada request quando secret vazio (força visibilidade) e suporta novo env toggle `WHATSAPP_WEBHOOK_SECRET_REQUIRED=true` para fail-closed. Não é obrigatório no código porque exigiria configurar o secret na WHAPI (externa) ao mesmo tempo. Passo final depende do usuário: (1) configurar secret na WHAPI + env, (2) ligar `WHATSAPP_WEBHOOK_SECRET_REQUIRED=true`.
**Impacto:** Se a env var não for configurada, qualquer um injeta votos falsos / cria pacotes fraudulentos.

**Evidência:** Lógica em `main.py:155-187` tem `if settings.webhook_secret: check...`. Sem `else: raise`.

**Fix sugerido:** Tornar obrigatório (falhar na inicialização se vazio).

---

### F-011 — 7 FKs sem índice (performance)

**Status:** ✅ fix-aplicado (2026-04-08). Migration `deploy/postgres/migration_F011_missing_fk_indexes.sql` criou os 7 índices: enquetes.produto_id (56KB), votos.alternativa_id (128KB), votos_eventos.alternativa_id (160KB), pacote_clientes.produto_id (72KB), pacote_clientes.voto_id (208KB), vendas.pacote_cliente_id (56KB), vendas.produto_id (32KB). Total <1MB. `CREATE INDEX IF NOT EXISTS` (idempotente).
**Impacto:** JOINs e filtros por essas colunas fazem **seq scan** em tabelas grandes. Dash fica lento conforme cresce.

**Evidência:**
```sql
SELECT c.conrelid::regclass AS tabela, a.attname AS coluna
FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
WHERE c.contype='f' AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=c.conrelid AND a.attnum=ANY(i.indkey));
```
| Tabela | Coluna |
|---|---|
| `enquetes` | `produto_id` |
| `votos` | `alternativa_id` |
| `votos_eventos` | `alternativa_id` |
| `pacote_clientes` | `produto_id` |
| `pacote_clientes` | `voto_id` |
| `vendas` | `pacote_cliente_id` |
| `vendas` | `produto_id` |

**Fix sugerido:** `CREATE INDEX CONCURRENTLY` para cada. Impacto baixo — índices pequenos.

---

## 🟡 SEV-3 — Médio

### F-012 — Zero transações explícitas em operações multi-tabela

**Status:** aberto
**Impacto:** Qualquer falha no meio de uma operação (rede, timeout, crash) deixa dados inconsistentes. Todos os outros findings desta seção derivam disso.

**Evidência:** Todo INSERT/UPDATE passa por PostgREST, que **não suporta transações explícitas** via REST. O código do `PackageService.rebuild_for_poll` (`whatsapp_domain_service.py:341-453`) faz 3+ mutations em sequência sem atomicidade.

**Fix sugerido:** Migrar operações críticas pra funções Postgres (`CREATE FUNCTION ... LANGUAGE plpgsql`), chamadas via PostgREST RPC. A função roda tudo em 1 transação.

---

### F-013 — Nenhum trigger/constraint de domínio no banco

**Status:** aberto
**Impacto:** Todas as invariantes (`SYSTEM.md §2.3`) dependem 100% do código Python. Qualquer SQL manual ou script esquecido viola — e não há rede de proteção.

**Fix sugerido:** Adicionar ao menos:
- `CHECK (voto_status='in' AND qty>0) OR (voto_status='out' AND qty=0)` em `votos`
- `CHECK ((status IN ('closed','approved')) = (closed_at IS NOT NULL))` em `pacotes`
- `CHECK ((status='approved') = (approved_at IS NOT NULL))` em `pacotes`
- `CHECK (cancelled_at >= created_at)` em `pacotes`
- Etc.

---

### F-014 — `uvicorn main:app` vs `app/main.py` (entry point duplo)

**Status:** aberto
**Impacto:** Confusão pra novos devs. Risco de código legado no `main.py` raiz vs novo em `app/`.

**Evidência:** `main.py` tem 885 linhas com toda a lógica. `app/main.py` é um wrapper de 10 linhas. Dockerfile copia ambos. `uvicorn main:app` roda o legado.

**Fix sugerido:** Consolidar em `app/main.py` (migrar rotas pra `app/routers/*` — diretório já existe mas está vazio).

---

### F-015 — `next_pacote_sequence` não é transacional com INSERT

**Status:** ✅ fix-aplicado (2026-04-08) — close_package agora calcula sequence_no dentro do mesmo advisory lock + transação. next_pacote_sequence ainda existe mas só é usado pelo manual_package_service.
**Impacto:** Gera os 10 erros `23505` (duplicate sequence_no) vistos em F-004.

**Fix sugerido:** Incorporar `next_pacote_sequence` dentro da RPC que cria o pacote, com `SELECT ... FOR UPDATE` na enquete ou advisory lock.

---

### F-016 — Pacotes `open` materializam contadores sem validação contra `votos`

**Status:** aberto
**Impacto:** O pacote `open` é um "slot virtual" — `participants_count` e `total_qty` refletem sobras do último `rebuild_for_poll`, mas não há garantia de que batem com `votos` reais.

**Evidência:** Todos os 936 pacotes `open` têm `participants_count>0` e `total_qty>0` mas `pacote_clientes=0` (comportamento esperado). Porém não há checagem de que `total_qty = sum(votos.qty WHERE enquete_id=X AND status='in' AND NOT IN(closed_packages))`.

**Fix sugerido:** VIEW que calcula o slot `open` dinamicamente ao invés de armazenar.

---

### F-017 — Workers em background via `asyncio.create_task` não resilientes

**Status:** aberto
**Impacto:** Se o container restartar durante um `/confirm`, o PDF não é gerado e os PIX não são criados. Não há retry.

**Fix sugerido:** Fila externa (Redis + RQ/Dramatiq, ou Postgres `queue_jobs` table polada por worker separado).

---

### F-028 — 🟠 SEV-2 — Endpoints sem paginação retornam histórico inteiro (720KB-1MB por request)

**Status:** **parcialmente resolvido** (2026-04-08, commit `f1711d0`) — gzip aplicado; paginação ainda pendente
**Descoberto em:** 2026-04-08 (Fase 5 do audit)

**Impacto:** O dashboard puxa o histórico completo a cada refresh. Com o volume atual (staging) já pesa 1.8MB de JSON combinado por page load. Em produção (que tem ~60% mais dados) será pior, e continua crescendo linearmente. Impacto em banda, tempo de parse no browser, memória, bateria mobile.

**Evidência:** medições em `staging-alana.v4smc.com`:

| Endpoint | Tempo médio | Payload | Conteúdo |
|---|---:|---:|---|
| `/health` | 55ms | 15 B | ok |
| `/` (dash HTML) | 80ms | 26 KB | OK |
| `/api/metrics` | 430ms | **786 KB** | `generated_at`, `enquetes` (9 keys), `votos` (21 keys), `customers_map` (551 entradas) |
| `/api/finance/charges` | 300ms | **1.08 MB** | **lista de 1142 vendas** (todo o histórico, 23 campos cada) |
| `/api/finance/stats` | 53ms | 429 B | OK |

**Causas:**
1. `/api/finance/charges` retorna `SELECT * FROM vendas JOIN clientes JOIN pacotes JOIN pagamentos` sem `LIMIT`/`OFFSET` nem filtros (só client-side).
2. `/api/metrics.customers_map` inclui todos os 551 clientes mesmo que a página atual só mostre poucos.
3. Sem ETag nem cache condicional — cada refresh baixa o payload inteiro de novo.

**Fix sugerido:**
1. **Paginação server-side** em `/api/finance/charges`: aceitar `?page=1&size=50&status=paid&date_from=...`, retornar `{rows, count, next}`. ⏳ pendente
2. **Slim `/api/metrics`**: remover `customers_map` do payload inicial. Endpoint separado `/api/customers/{id}` chamado sob demanda (hover/click). ⏳ pendente
3. **Cache HTTP**: `ETag` + `Cache-Control: private, max-age=30` nos endpoints de leitura. ⏳ pendente
4. **Compressão gzip/br**: ✅ **APLICADO** via `GZipMiddleware` no FastAPI (commit `f1711d0`).
5. **Resposta streaming**: FastAPI `StreamingResponse` com JSONL se paginação não couber na UI atual. ⏳ pendente

**Medições pós-gzip (2026-04-08 pós-deploy):**

| Endpoint | Antes | Depois (gzip) | Redução |
|---|---:|---:|---:|
| `/api/metrics` | 720 KB / 430ms | **120 KB / 245ms** | **-84% bytes, -43% tempo** |
| `/api/finance/charges` | 1080 KB / 300ms | **108 KB / 380ms** | **-90% bytes** |

O tempo de `/api/finance/charges` subiu ligeiramente (300→380ms) porque o CPU gasta mais comprimindo, mas o ganho de banda (−900KB) vale muito mais pro cliente, especialmente mobile. Paginação (item 1) ainda vai dar ganho complementar.

---

## 🟢 SEV-4 — Baixo / Higiene

### F-018 — 60+ imagens `alana-dashboard:staging-*` acumuladas no VPS

**Status:** ✅ fix-aplicado (2026-04-08). Limpeza preservando 4 tags pra rollback: staging-audit-3389395 (atual), staging-audit-f1711d0 (anterior), rollback-20260408, staging-edit-columns-fix (last known good).
**Impacto:** Disco + confusão. Nenhum problema funcional.

**Evidência:** `docker image ls | grep alana-dashboard | wc -l` ≈ 60. Nomes como `staging-fix-v1` ... `staging-fix-v19`, `staging-edit-fix`, `staging-queue-fix`, `staging-polls-open-fix` sugerem ciclo de build-and-pray.

**Fix sugerido:** `docker image prune -a --filter "reference=alana-dashboard:staging-*" --filter "until=168h"` (mantém últimas 7 dias).

---

### F-019 — 69 `clientes` sem nenhum voto

**Status:** aberto
**Impacto:** Zero. Provavelmente cadastros de teste antigos.

---

### F-020 — `~20` diretórios `/root/alana-staging-*` (snapshots de deploys antigos)

**Status:** aberto
**Impacto:** Zero funcional. Confusão sobre qual é o código ativo.

**Fix sugerido:** Arquivar em `/root/_archive/` após confirmar qual é o ativo.

---

### F-021 — Prints debug em `integrations/notifications/*`, `integrations/asaas/*`, `scripts/*`

**Status:** aberto
**Impacto:** Risco baixo de vazar dados sensíveis em logs. Use logger estruturado.

---

## Achados adicionados em 2026-04-08 (segunda rodada)

### ~~F-022 — "Editar Pacote Confirmado" quebrado~~ → **FALSO POSITIVO / INVALIDADO**

**Status:** verificado-fechado (falso positivo)
**Resolução em 2026-04-08:**

O finding original foi gerado auditando `/root/alana-staging-supabase/`, que é um **zip antigo extraído** (provavelmente do commit `staging-main-sync-20260330` ou similar) — **não reflete o código deployado hoje** na imagem `alana-dashboard:staging-edit-columns-fix`.

**Verificação no container em execução (`docker exec` no serviço `alana-staging_alana-dashboard`):**

```python
# /app/main.py:1359 dentro do container
@app.patch("/api/packages/{pkg_id}/edit")
...
# /app/main.py:1548
@app.get("/api/packages/{pkg_id}/edit-data")
async def ...
    from app.services.confirmed_package_edit_service import build_edit_columns
    available_votes, selected_votes = build_edit_columns(pkg, active_votes, confirmed_packages)
```

E o JS deployado tem **15 matches** de `dragstart|dragover|drop|dataTransfer|editColumns`. O feature está implementado e rodando.

**Lição aprendida (incorporada no workflow do audit):** **NUNCA auditar a partir de `/root/alana-staging-*` dirs — são zips defasados.** Sempre usar:
1. O código do **repo clonado** em `/root/projeto_alana/` (branch correspondente à imagem), OU
2. `docker exec` + extração via `docker cp` do container em execução.

Ver `FINDINGS.md#F-025` pra uma nova investigação que surgiu desse caso — divergência repo ↔ imagem ↔ dirs locais.

---

### F-025 — 🟠 SEV-2 — Descoordenação entre repo GitHub, dirs locais e imagem deployada

**Status:** aberto
**Descoberto em:** 2026-04-08
**Impacto:** Operar sobre código desatualizado, medo de commitar "regressões", ciclo de build-and-pray. A raiz de vários dos achados deste audit.

**Evidência:**

1. **Repo ativo:** `https://github.com/V4MarcosPaulo/projeto_alana`, branch deployada: `fix/staging-hotfixes-20260407` (fingerprint MD5 do `main.py` bate com o container).
2. **Último commit da branch:** `590da97 fix: votos disponíveis na edição de pacote filtra apenas mesma enquete`
3. **Delta container ↔ branch `fix/staging-hotfixes-20260407`:** ~68 arquivos. Alguns arquivos existem no container mas não na branch HEAD:
   - `app/services/supabase_service.py`
   - `app/services/whatsapp_domain_service.py`
   - `app/services/routing_service.py`
   - `app/services/runtime_state_service.py`
   - `app/services/finance_service.py`
   - `app/services/drive_image_service.py`
   - `app/services/group_context_service.py`
   - `app/services/domain_lookup.py`
   - `app/services/staging_dry_run_service.py`
   - `app/services/recent_image_cache.py`
   - `app/services/services/` (pasta aninhada ⚠️)
   - `app/workers/background_tasks.py`
   - `integrations/{asaas,google_drive,whapi,integrations}/` (pastas aninhadas)
4. Vários desses arquivos **existem em outras branches** (ex: `staging/test-env-20260331-c46887e`) — sugere que o Dockerfile ou build manual puxou arquivos de múltiplas branches/zips.
5. Há **14 branches remotas** (incluindo `main`, hotfix, backups, migrations, staging-sync variadas) sem clareza hierárquica de qual é o "tronco".
6. Há **~20 dirs `/root/alana-staging-*`** no VPS (zips extraídos), todos desatualizados em relação ao container.
7. Há **60+ imagens** `alana-dashboard:staging-*` locais — ciclo caótico de build-and-pray visível.

**Risco imediato:**
- Qualquer fix que eu commitar na branch `fix/staging-hotfixes-20260407` **rebuildado** como imagem **perderá** os arquivos "only in container" listados acima — se eles estiverem realmente em uso pelo código.
- Alternativa: commitar os arquivos "only in container" junto com os fixes. Mas sem saber de qual branch vieram, isso pode estar trazendo código órfão / experimental.

**Fix sugerido (estratégia de convergência — a alinhar com o usuário):**

1. **Convergir tudo na branch `fix/staging-hotfixes-20260407`:**
   a. Extrair o código real do container (feito: `/root/alana-audit/container-snapshot/app/`).
   b. Diff cirúrgico: arquivo a arquivo, decidir se a versão do container é mais nova/correta ou se é órfão.
   c. Commit em múltiplos commits atômicos (não "big bang"): `chore: sync <arquivo> from deployed container`.
   d. Push pra `fix/staging-hotfixes-20260407`.
   e. Rebuild da imagem A PARTIR do commit, tag com SHA, deploy, smoke test.
2. **Limpar as 20 pastas `/root/alana-staging-*` e as 60+ imagens** após confirmar que tudo está no git.
3. **Convenção de branches:** estabelecer que `main` recebe merges de `fix/*` e `feature/*`, nunca mais trabalho direto em `main` pra staging.
4. **CI build:** habilitar GitHub Actions que builda e publica no `ghcr.io/v4marcospaulo/alana-dashboard-staging:sha-<sha>` automaticamente, pra eliminar o build manual no VPS.

**Pergunta bloqueante para o usuário:** antes de commitar qualquer coisa, preciso saber:
- Os arquivos "only in container" são mudanças válidas que devem ir pra branch, ou são órfãos pra descartar?
- Posso criar uma branch `audit/2026-04-08-convergence` a partir do HEAD de `fix/staging-hotfixes-20260407` e commitar o delta lá primeiro (como safety net) antes de mexer na branch ativa?

---

### F-023 — 🟢 SEV-4 — `baileys-poll-listener` é dead code

**Status:** aberto
**Descoberto em:** 2026-04-08
**Impacto:** Zero. Apenas confusão.

**Evidência:**
- `/root/baileys-poll-listener/` contém um serviço Node.js standalone (Baileys 7.0.0-rc.4) que captura votos do WhatsApp e posta num webhook.
- **Não está rodando**: sem processo Node, sem container Docker, sem serviço systemd.
- O tráfego real de votos no `webhook_inbox` vem do provider `whapi` (10055 eventos), nunca de `baileys`.
- O `auth_info/` (sessão WhatsApp do Baileys) existe mas pode estar expirado.

**Decisão (escopo do audit):** **Fora de escopo.** Não auditar, não atualizar.

**Fix sugerido (housekeeping):**
1. Confirmar com o usuário se o projeto pode ser arquivado.
2. Se sim, mover `/root/baileys-poll-listener/` → `/root/_archive/baileys-poll-listener/`.

---

### F-024 — 🟡 SEV-3 — `created_at` semanticamente confuso (≠ ordenação do negócio)

**Status:** aberto (decorrência da resolução do F-007)
**Impacto:** Qualquer query que use `ORDER BY created_at` para mostrar "histórico do negócio" mente. O dashboard, relatórios financeiros, exports — todos podem estar usando o campo errado.

**Evidência:** Resolução de F-007 estabeleceu que `closed_at` é o ground truth temporal. Mas `created_at` é o que tem `DEFAULT now()` no schema, então é o que naturalmente seria usado.

**Fix sugerido:**
1. Auditar todas as queries do código que usam `created_at` em `pacotes` — substituir por `closed_at`/`opened_at` onde fizer sentido.
2. Renomear `created_at` → `inserted_at` no banco (migração) pra deixar a semântica explícita. (Custo alto, talvez wontfix.)
3. Adicionar coluna gerada `event_at` que escolhe o melhor timestamp por status.

---

### F-026 — 🟠 SEV-2 — Bug de extração de título: 89 enquetes com `titulo = external_poll_id`

**Status:** ✅ fix-aplicado (2026-04-08). Backfill: 89 enquetes atualizadas via webhook_inbox.payload_json#>>'{after_update,poll,title}' (UPDATE em transação, 0 restantes). Preventivo: VoteService.process_vote em whatsapp_domain_service.py agora extrai o título do payload ao invés de fallback silencioso para external_poll_id; loga ERROR se não conseguir.
**Descoberto em:** 2026-04-08 (durante reconciliação Baserow ↔ Postgres)

**Impacto:** Dashboard exibe enquetes com título sendo o ID bruto (ex: `Kj2LFYQCe_RpWw-go4Bq53HliOHgA`), sem preço nem descrição. Operador não consegue identificar o produto. **117 pacotes e 615 votos** estão associados a enquetes com esse problema.

**Evidência:**

```sql
SELECT count(*) FROM enquetes WHERE titulo = external_poll_id;
-- 89

-- distribuição temporal (recente → antigo):
-- 2026-04-06: 1
-- 2026-04-05: 1
-- 2026-03-28: 2
-- 2026-03-27: 1
-- 2026-03-24: 18  ← pico
-- 2026-03-23: 13
-- 2026-03-14: 14  ← pico
```

**Hipótese:** Ao criar a enquete, a normalização do webhook falha em extrair o título da mensagem do WhatsApp (função `_extract_poll_info` em `app/services/whatsapp_domain_service.py`) e faz fallback silencioso para `external_poll_id`. Acontece quando o payload vem em formato inesperado (bot editou mensagem, schema WhatsApp mudou, etc).

**Tendência:** Bug diminuiu drasticamente — só 2 casos em abril vs dezenas em março. Provavelmente foi **parcialmente corrigido** em algum commit recente, mas ainda resta um caminho residual.

**Fix sugerido:**
1. **Backfill imediato:** Script que lê `webhook_inbox.payload_json` dos eventos `poll_created` correspondentes e re-extrai o título, atualizando as 89 linhas.
2. **Preventivo:** No parser, se extração falhar NÃO fazer fallback silencioso — logar erro `ERROR` level + marcar a enquete com `needs_title_recovery=true` (coluna a adicionar). Operador vê alerta no dashboard.
3. **Constraint de warning:** CHECK constraint ou VIEW que destaca `titulo = external_poll_id` como problema.

---

### F-027 — ℹ️ Informativo — Reconciliação Baserow ↔ Postgres para período 2026-04-06+

**Status:** verificado-fechado (não é bug)
**Descoberto em:** 2026-04-08

**Reconciliação executada:** comparação de `external_poll_id` entre:
- **Baserow (prod)** tabela 18, filtrado por `field_171 >= 1775433600` (2026-04-06 00:00 UTC)
- **Postgres (staging)** `enquetes` filtrado por `created_at >= '2026-04-06'`

| Conjunto | Quantidade |
|---|---:|
| Baserow (prod) enquetes >= 2026-04-06 | 140 |
| Postgres (staging) criadas >= 2026-04-06 | 139 |
| Interseção (mesmo external_poll_id) | 137 |
| **Gap A**: no Baserow mas não no Postgres | 3 |
| **Gap B**: no Postgres mas não no Baserow | 2 |

**Gap A analisado:** todas as 3 são enquetes `[teste] ...` — **resíduos da minha própria limpeza durante o audit** (quando o usuário pediu pra deletar enquetes com "teste" no título na Fase 1). Não é bug do sistema.

**Gap B analisado:**
1. Uma cai no F-026 (titulo=poll_id bug).
2. Outra (`KmEpCo7E293nlg-gpkBq53Hli`, título "Conjunto lindo lindo PMG na alfaiataria", `chat_id=120363295598413696@g.us`) é do **grupo de teste** — staging recebe do grupo de teste, prod não grava no Baserow (comportamento esperado, confirmado pelo usuário).

**Conclusão:** staging está **bem alinhado com prod** no período solicitado (**97.8% de interseção**, restante é ruído conhecido e esperado). **Nenhuma sincronização massiva é necessária.**

---

### F-030 — 🟡 SEV-3 — 734 pacotes `open` zumbis (7-30 dias parados)

**Status:** ✅ fix-aplicado (2026-04-08). Frontend: filtro `opened_at >= now() - 7 days` no endpoint. Limpeza: 428 pacotes open cancelados via `UPDATE ... SET status='cancelled', cancelled_by='zombie_cleanup_20260408'` (critério: nenhum voto nos últimos 14 dias). Pacotes open caíram de 939 → 511. Reversível via `WHERE cancelled_by='zombie_cleanup_20260408'`.

**Descoberto em:** 2026-04-08 (investigando por que o modal "Criar Pacote" mostrava 964 enquetes em "72 horas")

**Impacto:** O modal "Criar Pacote" estava inflando a lista porque incluía **todas as enquetes com `pacotes.status='open'`, sem filtro de idade**. 748 enquetes com pacote open criado entre 7-30 dias atrás apareciam ao lado das 155 realmente dentro de 72h, tornando a seleção ruim.

**Evidência (snapshot 2026-04-08, via `deploy/postgres/diagnose_F030_zombie_open_packages.sql`):**

```
Distribuição de idade dos pacotes status='open' (por opened_at):
  até 72h:        142 pacotes   1386 peças   417 clientes
  3-7 dias:        63 pacotes    591 peças   178 clientes
  7-30 dias:      734 pacotes   7233 peças  2165 clientes  ← zumbis

Última atividade de voto nas enquetes com pacote open:
  até 72h:         171 enquetes
  3-7 dias:         76 enquetes
  7-30 dias:       692 enquetes  ← votação claramente parada
```

Mais antigos: pacotes de 2026-03-13 (26 dias atrás), com `participants_count` baixo (1-7) e `total_qty` entre 3-21 (nunca chegaram aos 24 peças necessários).

**Fix aplicado (2026-04-08):**
- `main.py:/api/polls/recent`: filtro adicional `opened_at >= now() - interval '7 days'` na query de pacotes open.
- `templates/index.html`: label do modal atualizada para *"Enquetes ativas (últimas 72h ou com pacote aberto recente)"*.
- Resultado: modal agora mostra ~205 enquetes (vs 964 antes).
- **Os 734 pacotes zumbis continuam no banco** — nada foi deletado, só ficaram invisíveis no modal.

**Próximos passos (aguardando decisão humana):**
1. Revisar `deploy/postgres/diagnose_F030_zombie_open_packages.sql` (read-only) pra ver o estado completo.
2. Decidir política de limpeza (se houver):
   - **Conservador** (30 dias): `UPDATE pacotes SET status='cancelled', cancelled_at=now(), cancelled_by='zombie_cleanup' WHERE status='open' AND opened_at < now() - interval '30 days'` → muito poucos.
   - **Médio** (14 dias): mesmo filtro com `interval '14 days'` → maioria dos 734.
3. Longo prazo: worker periódico (daily) que marca como cancelled os pacotes open com última atividade > X dias.

**Causa raiz:** O fluxo de voto em `rebuild_for_poll` sempre cria/atualiza um pacote `open` com as sobras (`sequence_no=0`). Quando a enquete para de receber votos, o pacote open persiste indefinidamente — não há expiração automática.

---

### F-031 — 🔴 SEV-1 — Criação de pacote manual quebra quando cliente não tem voto prévio

**Status:** ✅ fix-aplicado (2026-04-08)

**Descoberto em:** 2026-04-08 (usuário tentou criar pacote manual e recebeu erro 400)

**Impacto:** Ao criar pacote manual via modal "Criar Pacote" e escolher um cliente (por telefone) que **não tinha voto registrado na enquete**, o backend quebrava com erro:

```
Supabase REST error 400 for /rest/v1/pacote_clientes:
  code 23502 — null value in column "voto_id" of relation "pacote_clientes"
  violates not-null constraint
```

A transação do Postgres fazia rollback automático (nenhum dado corrompido), mas o operador via erro confuso e o pacote não era salvo. **Blocker total do fluxo de criação manual** quando há clientes novos/não votantes.

**Evidência:** `app/services/manual_package_service.py:200` (pré-fix):

```python
voto_id = existing_voto[0]["id"] if isinstance(existing_voto, list) and existing_voto else None
# ...
client.insert("pacote_clientes", {"voto_id": voto_id, ...})  # voto_id=None → viola NOT NULL
```

**Fix aplicado:** quando `existing_voto` é vazio, criar voto sintético em `votos` antes do INSERT em `pacote_clientes`:

```python
if not voto_id:
    synthetic_voto = {
        "enquete_id": enquete_id,
        "cliente_id": customer["id"],
        "alternativa_id": alternativas_by_qty.get(qty),
        "qty": qty,
        "status": "in",
        "voted_at": now.isoformat(),
    }
    created = client.upsert_one("votos", synthetic_voto, on_conflict="enquete_id,cliente_id")
    voto_id = str(created["id"])
```

O voto sintético:
- Tem `alternativa_id` correspondente à `qty` (lookup pré-feito em `enquete_alternativas` uma vez por chamada).
- `status='in'`, `voted_at=now()`.
- `upsert on_conflict="enquete_id,cliente_id"` (idempotente).

Representa operacionalmente: *"o operador registrou esse voto em nome do cliente"* — mesmo conceito do fluxo automático `rebuild_for_poll`.

**Deploy:** `alana-dashboard:staging-audit-0f9aaf4` + follow-up `staging-audit-e7d97c8`.

---

### F-032 — 🔴 SEV-1 — Endpoint `PATCH /api/packages/{id}/edit` não existia, títulos editados viravam 404

**Status:** ✅ fix-aplicado (2026-04-08)

**Descoberto em:** 2026-04-08 (usuário editou título de pacote, confirmou, título voltou pro original)

**Impacto:** Ao editar o título de um pacote pelo dashboard (aba de Pacotes Confirmados ou antes de confirmar), o frontend chamava `PATCH /api/packages/{id}/edit` mas o endpoint **não existia** — FastAPI respondia 404, o frontend tratava o erro silenciosamente (só logava no console), a UI mostrava o novo título otimisticamente mas nada era persistido. Depois do refresh ou da confirmação do pacote, o título voltava pro original.

**Causa raiz:** o endpoint existia como router em `app/routers/packages.py:87` (`@router.patch("/{pkg_id}/edit")`), mas `main.py` **nunca chamava `app.include_router(packages_router.router)`** — só incluía `customers_router`. Então o endpoint do router ficava órfão. A versão inline em `main.py` existia na imagem anterior ao audit (`staging-edit-columns-fix`) mas foi perdida durante a Operação Convergência (F-025), porque o dir fonte local de build (`/root/alana-staging-deploy-manual-qty-fix/`) não tinha o endpoint inline — só o router órfão.

**Verificação:**
```bash
curl -X PATCH https://staging-alana.v4smc.com/api/packages/00000000-0000-0000-0000-000000000000/edit \
  -H 'Content-Type: application/json' -d '{"poll_title":"teste"}'
# Antes do fix: {"detail":"Not Found"} HTTP 404
```

**Fix aplicado:** endpoint `PATCH /api/packages/{pkg_id}/edit` adicionado inline em `main.py` (ao lado do `/update-confirmed`). Fluxo:

1. Valida `poll_title` no body
2. Chama `update_package_state(pkg_id, {"custom_title": new_title})` → grava em `pacotes.custom_title` no Postgres
3. Se o pacote está em `confirmed_packages` store local, atualiza o snapshot imediato
4. Regenera métricas via `generate_and_persist_metrics()`
5. Retorna `{status, package_id, poll_title, persisted, data}`

**Leitura já estava correta** — `metrics/supabase_clients.py:482` já fazia:
```python
"poll_title": row.get("custom_title") or enquete.get("titulo") or poll_id
```

**Validação end-to-end:**
```
pkg d81ecde9... antes: custom_title=NULL
PATCH /api/packages/d81ecde9.../edit → HTTP 200, persisted=true
banco depois: custom_title="[audit-smoke] Titulo Editado F-032"
```

**Lição:** o router `packages.py` ainda está órfão — ele tem `confirm`, `reject`, `revert`, `tag`, `edit` duplicados com main.py. Não foi incluído pra evitar conflito. Follow-up: consolidar todos em um lugar só (ou manter tudo inline e deletar o router, ou migrar tudo pro router e remover inline).

**Deploy:** `alana-dashboard:staging-audit-e7d97c8`.

---

### F-033 — 🟠 SEV-2 — Edição de título não recalcula preço no `pacote_clientes`

**Status:** ✅ fix-aplicado (2026-04-08)

**Impacto:** Ao editar o título de um pacote e mudar o preço embutido no formato `$X,XX` (ex: `Calça PMG $45,00` → `Calça PMG $10,00`), o título persistia via F-032 mas o `unit_price`, `subtotal`, `commission_amount` e `total_amount` no `pacote_clientes` continuavam com o preço original. Resultado: cobrança PIX criada com valor errado no Asaas.

**Fix:** `PATCH /api/packages/{id}/edit` agora:
1. Extrai preço do título via `finance.utils.extract_price` (regex `R?\$\s*\d+(?:[.,]\d{1,2})?`).
2. Se o novo preço for diferente do `unit_price` atual (delta > 0.01), faz `UPDATE` em cada linha de `pacote_clientes` desse pacote recalculando `unit_price`, `subtotal = new_price × qty`, `commission_amount = subtotal × 0.13`, `total_amount = subtotal + commission`.
3. Só afeta o pacote editado — não modifica `produtos.valor_unitario` nem outros pacotes da mesma enquete.
4. Resposta inclui `price_updated: bool`.

**Validação end-to-end:**
```
pkg cc2d4622: unit_price=60.0 subtotal=180.0 total=203.4
PATCH .../edit {"poll_title":"...PMG $12,34"}
→ {status: success, price_updated: true}
pkg cc2d4622: unit_price=12.34 subtotal=37.02 total=41.83
```

**Débito técnico:** a edição ainda não aceita um campo `price` explícito no body (precisa embutir no título). Frontend usa `prompt()` simples. Follow-up: modal de edit com campos separados para título e preço.

---

### F-034 — 🔴 SEV-1 — `_find_package_in_metrics` retorna pacote errado por colisão de legacy id

**Status:** ✅ fix-aplicado (2026-04-08)

**Impacto crítico:** Ao confirmar um pacote manual, o `pdf_worker` e `payments_worker` recebiam o snapshot **errado** — com os votos da enquete inteira ao invés do único cliente adicionado manualmente. Consequências:
- PDF de etiqueta enviado ao estoque mostrava votantes da enquete original (ex: 4 pessoas) em vez do cliente manual.
- Mensagens de cobrança PIX criadas erradas (4 cobranças de clientes que nem estavam no pacote).
- A cobrança correta do cliente manual **não era criada**.

**Causa raiz:** `metrics/supabase_clients.py:_legacy_package_id()` faz `max(sequence_no - 1, 0)`:
- `seq=0` (pacote slot open) → id `{poll}_0`
- `seq=1` (primeiro pacote closed) → id `{poll}_0` ← **COLISÃO**

O `_find_package_in_metrics` em `main.py` iterava por `(open, closed_today, closed_week, confirmed_today)` nessa ordem e retornava o primeiro match. Ao buscar por `{poll}_0`, **pegava o open primeiro**, cujo `votes` é o `open_votes_by_poll` (votos correntes da enquete, não `clients_by_package`).

**Evidência:** snapshot do banco logo após fix F-031 mostrava dois pacotes com mesmo id legacy:
```
section=open            id={poll}_0 source=4c6ca95d... votes count=4 (Irene, Nores, Seaan, Luzia)
section=confirmed_today id={poll}_0 source=fc872722... votes count=1 (Marcos Paulo)
```

**Fix:**
1. **Nova ordem de iteração** no `_find_package_in_metrics`: `(confirmed_today, closed_today, closed_week, open)` — open por último, porque o open slot é sempre o "fallback" e nunca deve ser preferido sobre pacotes reais.
2. **Primeira passada por `source_package_id`** (UUID, sem colisão). Só se não achar por UUID é que cai pro fallback por legacy id.
3. Garante que mesmo com ids colidindo, o resultado é sempre o pacote "real" (não o slot open).

**Débito técnico não abordado:** `_legacy_package_id` ainda tem o bug de gerar ids colidentes. Reescrita (ex: seq=0 → `_open`, seq>=1 → `_{seq-1}`) deixada como follow-up porque mudar o formato pode quebrar clientes/frontend que persistem ids. A reordenação da busca cobre 100% dos casos conhecidos.

---

### F-035 — 🔴 SEV-1 — Worker da fila de pagamentos/WhatsApp nunca foi iniciado

**Status:** ✅ fix-aplicado (2026-04-08)

**Impacto crítico:** Todas as mensagens de cobrança (PIX + imagem do produto + copia-e-cola) enfileiradas pelo `payments_worker` após confirmação de pacote ficavam **paradas na fila pra sempre** e nunca eram enviadas pelo WhatsApp. Ao mesmo tempo, `payment_sync_service` (que faz polling no Asaas pra marcar pagamentos como `paid`) também nunca rodava.

**Causa raiz:** mesmo padrão do F-025 (Operação Convergência). Existia um `app/startup.py` com um lifespan handler que iniciava:
1. `start_payment_queue_worker()` — worker consumidor da fila
2. `start_payment_sync_scheduler(interval_minutes=15)` — sync Asaas
3. `app.state.refresh_lock` e `app.state.packages_lock`

Mas o `init_app(app)` **nunca era chamado no `main.py`**. O `main.py` tinha seu próprio `@app.on_event("startup")` que não fazia nenhuma dessas chamadas. Durante a convergência, o lifespan ficou órfão.

**Evidência:** dos 5 jobs enfileirados em testes anteriores, nenhum havia sido processado — todos ficaram em estado `queued` na fila. Depois do fix, o worker subiu e processou todos os 5 em 20 segundos (todos retornaram `status=sent`).

**Fix:** adicionado no `@app.on_event("startup")` do `main.py`:

```python
# F-035: iniciar worker da fila de pagamentos
from app.services.payment_queue_service import start_payment_queue_worker
await start_payment_queue_worker()

# Sync scheduler (polling Asaas a cada 15 min)
from app.services.payment_sync_service import start_payment_sync_scheduler
asyncio.create_task(start_payment_sync_scheduler(interval_minutes=15))

# Locks no app.state (usados por routers em app/routers/packages.py)
app.state.refresh_lock = refresh_lock
app.state.packages_lock = packages_lock
```

Não foi feita migração completa para lifespan (do `app/startup.py`) porque isso conflitaria com o `@app.on_event("startup")` existente, mais risco. O fix cirúrgico resolve o problema imediato.

**Validação em produção:**
```
20:23:44 payment_sync: Sincronização concluída
20:23:47 payment_queue: job=23d0899d... processado sent
20:23:50 payment_queue: job=45795ec2... processado sent
20:23:54 payment_queue: job=4a413959... processado sent
20:23:57 payment_queue: job=91c13fb2... processado sent
20:24:01 payment_queue: job=eeb4c1a4... processado sent
```

**Deploy:** `alana-dashboard:staging-audit-7c4eee6`.

---

### F-036 — 🟠 SEV-2 — Aba Clientes mostra qty incorreta, sem coluna "Em Débito" e sem cancelar cobrança

**Status:** ✅ fix-aplicado (2026-04-08)

**Relatado pelo usuário:**
- Aba Clientes mostra qty de peças errada (só reflete votos atuais, deveria mostrar histórico vitalício em todos os pacotes do cliente — incluindo pacotes manuais).
- Faltava coluna "Em Débito" (valor de cobranças não-pagas).
- Faltava botão/ação "Cancelar cobrança" no financeiro.

**Causa raiz:**
- `build_customer_rows()` em `staging_dry_run_service.py` somava `qty` e `total_paid` a partir da lista de **charges** (vendas aprovadas + pagamentos). Cliente com pacotes manuais criados mas pagamentos ainda `created` não aparecia na conta. Nunca havia campo de débito.
- O enum `pagamento_status` tinha apenas `(created, sent, paid, failed)`. Não havia forma de cancelar uma cobrança sem deletar a linha.

**Fix aplicado:**

**1. Migration SQL** — `deploy/postgres/migration_F036_pagamento_cancelled_status.sql`:
```sql
ALTER TYPE pagamento_status ADD VALUE IF NOT EXISTS 'cancelled';
```

**2. Migration SQL** — `deploy/postgres/migration_F036_customer_stats_rpc.sql`:
Nova RPC `get_customer_stats()` que retorna uma linha por cliente com:
- `qty`: soma de `pacote_clientes.qty` JOIN `pacotes` WHERE `status IN ('closed', 'approved')` — exclui cancelled.
- `total_debt`: soma de `vendas.total_amount` JOIN `pagamentos` WHERE `status IN ('created', 'sent')` — não-pago nem cancelado.
- `total_paid`: soma de `vendas.total_amount` JOIN `pagamentos` WHERE `status = 'paid'`.

Uma única query PostgreSQL vs N+1 round-trips via PostgREST.

**3. Backend** — `app/services/customer_service.py`:
Nova função `_build_customer_rows_supabase()` que chama a RPC. `refresh_customer_rows_snapshot()` agora usa ela quando `supabase_domain_enabled()`. Estrutura retornada: `{phone, name, qty, total_debt, total_paid}`.

**4. Backend** — `main.py:/api/finance/charges/{id}/status`:
- Aceita novos valores: `cancelled`, `canceled`, `cancelado` além de `paid`/`pending`/`pago`/`pendente`.
- Mapeia pra valores do enum Postgres (`paid`, `created`, `cancelled`).
- Após gravar, chama `refresh_customer_rows_snapshot()` automaticamente pra manter `total_debt`/`total_paid` sincronizados sem F5 manual.

**5. Frontend** — `templates/index.html` e `static/js/dashboard.js`:
- Nova coluna "Em Débito" na tabela de clientes (formatada em BRL, vermelho quando > 0).
- Cabeçalho "Peças Votadas" renomeado para "Peças Pedidas".
- Badge "Cancelado" cinza (reversível — clicar volta para Pendente).
- Novo botão "Cancelar cobrança" (ícone `fa-ban`, cinza) ao lado de Reenviar e Excluir no financeiro. Mantém histórico (grava `status=cancelled`), não deleta a linha.
- Botão "Excluir" continua disponível pra limpeza real.

**Cache invalidation:**
Depois de aplicar as migrations e fazer o deploy, o snapshot antigo em `app_runtime_state.customer_rows` ainda servia dados velhos. Fix operacional: `DELETE FROM app_runtime_state WHERE key='customer_rows'` força rebuild pelo próximo request.

**Bug operacional descoberto:** PostgREST precisa de `NOTIFY pgrst, 'reload schema'` ou restart pra reconhecer novas RPCs. Migrations aplicadas no banco não aparecem automaticamente no schema cache do PostgREST. Documentado como lição operacional.

**Validação end-to-end (pós-deploy e refresh do cache):**

```
Marcos Paulo 556293353390: qty=108 debt=R$ 3.390,00 paid=R$ 0,00   ← 3 pacotes manuais
Marcos Paulo 556294908837: qty= 24 debt=R$   135,60 paid=R$ 0,00   ← 1 pacote
Gil Viana    558892093535: qty=333 debt=R$     0,00 paid=R$ 4.407  ← tudo pago
Vanny moda   559491125481: qty=402 debt=R$   189,84 paid=R$ 3.800  ← parcial

Total: 551 clientes
```

Testes end-to-end OK:
- POST `/api/customers/` cria cliente
- PATCH `/api/customers/{phone}` edita nome (propaga em todas as abas via JOIN na próxima leitura de metrics)
- PATCH `/api/finance/charges/{id}/status` com `{"status":"cancelled"}` grava corretamente

**Débito técnico não abordado:**
- Modal de edição de cliente só permite mudar **nome**, não telefone. Mudar telefone exigiria migrar todas as FKs (`votos.cliente_id` etc) — escopo grande. Por enquanto, se precisar trocar telefone, cadastrar novo cliente.

**Deploy:** `alana-dashboard:staging-audit-fc7dedb`.

---

### F-037 — 🟠 SEV-2 — Toggle de status no Financeiro não atualiza o dash

**Status:** ✅ fix-aplicado (2026-04-08)

**Relatado pelo usuário:** "cliquei no botão de status e alterei o status de um cliente de Pendente para Pago, porém o dash não atualizou e o status não foi alterado".

**Causa raiz:** o endpoint `PATCH /api/finance/charges/{id}/status` gravava corretamente no banco (`pagamentos.status`), invalidava o snapshot de **clientes** (F-036), mas **não invalidava o snapshot de charges** (`app_runtime_state.finance_charges_rows`). Resultado: operador clicava "Pago", backend confirmava sucesso, mas o próximo `GET /api/finance/charges` lia do snapshot stale e devolvia `status=created`, fazendo parecer que o clique não teve efeito.

**Reprodução confirmada:**
```
PATCH /api/finance/charges/a51286b4.../status {"status":"paid"}
  → {"status":"success","new_status":"paid"}
Banco pós-PATCH: status='paid', paid_at='2026-04-09...'
GET /api/finance/charges?search=556293353390
  → [{id:a51286b4..., status:"created", paid_at:null}]  ← stale
```

**Fix:** `main.py:update_charge_status` agora chama `refresh_charge_snapshot()` logo após o PATCH no banco, **antes** do refresh de customers:

```python
# F-037: refresh do snapshot de charges pra que o próximo GET retorne
# o novo status sem servir dados velhos do runtime_state.
from app.services.finance_service import refresh_charge_snapshot
await asyncio.to_thread(refresh_charge_snapshot)

# F-036: refresh do snapshot de customers pra atualizar total_debt/total_paid
from app.services.customer_service import refresh_customer_rows_snapshot
await asyncio.to_thread(refresh_customer_rows_snapshot)
```

**Validação end-to-end:**
```
Marcos Paulo antes: qty=108 debt=3390.00 paid=0.00
PATCH status=paid em 1 charge de R$ 813.60
  → banco: pagamentos.status='paid'
  → GET charges: charge agora retorna status='paid' ← F-037 OK
  → GET customers: debt=2576.40 paid=813.60 ← F-036 OK
Rollback PATCH status=pending
  → GET customers: debt=3390.00 paid=0.00 ← consistente
```

**Deploy:** `alana-dashboard:staging-audit-5af2b0f`.

---

### F-038 — ℹ️ Informativo — Throttling de WhatsApp está DESLIGADO no staging

**Status:** verificado-fechado (by-design, `TEST_MODE=true`)

**Pergunta do usuário:** "veja se está funcionando o sistema de throttling que evita bloqueio do número por mandar muitas mensagens".

**Resposta:** o throttling **existe e é sofisticado** em `integrations/notifications/whatsapp.py`:

- **Lock global** (`_client_send_lock`): serializa envios, nunca dois em paralelo.
- **Delay entre clientes** (env `WHATSAPP_CLIENT_DELAY_MIN_SECONDS`/`MAX`): espera entre 1-4 minutos (default) entre mensagens pra clientes diferentes.
- **Pausa para café** (env `WHATSAPP_BREAK_EVERY_MIN/MAX=5/10`): depois de enviar N mensagens (aleatório 5-10), pausa por `WHATSAPP_BREAK_DURATION_MIN/MAX=300/600` segundos (5-10 min).
- **Pausa grande** (env `WHATSAPP_BIG_BREAK_INTERVAL=4`): a cada 4 pausas curtas, pausa adicional de 5-10 min.
- **Delay entre mensagens do mesmo envio**: entre a foto do produto e o PIX copia-e-cola (`WHATSAPP_INTER_MESSAGE_DELAY_*`).

**MAS**: todo esse throttling é **desligado** quando:
```python
_test_mode_enabled = os.getenv("TEST_MODE", "").strip().lower() in ("1","true","yes")
WHATSAPP_DISABLE_CLIENT_THROTTLE = env_flag or _test_mode_enabled
```

O staging tem `TEST_MODE=true`, então `WHATSAPP_DISABLE_CLIENT_THROTTLE=true` por efeito cascata. Por isso os 5 jobs que estavam travados na fila (F-035) chegaram todos no Marcos Paulo em rajada de ~20 segundos.

**Avaliação:** isso é **intencional** — em staging você quer feedback rápido, não esperar 10 minutos por mensagem. Em produção (`alana.v4smc.com`) com `TEST_MODE=false`, o throttling fica ativo e protege o número real.

**Recomendação (opcional, se o usuário quiser validar o throttling no staging):**

Desabilitar só o `TEST_MODE` do staging mantém demais comportamentos de teste (ex: `TEST_PHONE_NUMBER` override continua) se você quiser rodar com throttling real. Ou pode setar diretamente valores menores pra testar rápido:

```bash
docker service update \
  --env-add WHATSAPP_DISABLE_CLIENT_THROTTLE=false \
  --env-add WHATSAPP_CLIENT_DELAY_MIN_SECONDS=5 \
  --env-add WHATSAPP_CLIENT_DELAY_MAX_SECONDS=15 \
  --env-add WHATSAPP_BREAK_EVERY_MIN=3 \
  --env-add WHATSAPP_BREAK_EVERY_MAX=5 \
  --env-add WHATSAPP_BREAK_DURATION_MIN=10 \
  --env-add WHATSAPP_BREAK_DURATION_MAX=30 \
  alana-staging_alana-dashboard
```

(Valores muito menores que prod mas ainda representativos do algoritmo: pausa a cada 3-5 mensagens, cada pausa de 10-30s.)

---

### F-039 — 🔴 SEV-1 — Financeiro com histórico inconsistente, cálculos de KPI incluindo cancelled, lifecycle de status incompleto

**Status:** ✅ fix-aplicado (2026-04-08) — reset + 5 fixes

**Relato do usuário:** "estou em dúvida sobre a procedência e qualidade desse financeiro, vamos zerar todo o histórico atual e validar o funcionamento dos campos".

**Escopo do fix (5 partes):**

#### 1. Reset do histórico financeiro

`deploy/postgres/migration_F039_reset_financeiro.sql` (idempotente, transacional):

- `DELETE pagamentos` (1145 linhas)
- `DELETE vendas` (1145 linhas)
- **Recria vendas** a partir de `pacote_clientes` de pacotes `status='approved'` (790 linhas, 1:1 com pacote_clientes)
- **Recria pagamentos** com `status='created'`, 1 por venda (790 linhas)
- **Não passa por `sales.approve_package`** para não disparar pdf_worker e payments_worker de novo (os PDFs e mensagens já foram enviados)
- Limpa snapshots em `runtime_state` (`finance_charges_rows`, `finance_dashboard_stats`, `customer_rows`)
- Limpa `data/payments.json` do FinanceManager legacy

**Estado antes → depois:**
```
vendas:          1145 → 790
pagamentos:      1145 → 790
  created:          4 → 790
  sent:           106 → 0
  paid:          1035 → 0
total_pendente:  ???  → R$ 177.136,32
```

Clientes (551), pacotes e pacote_clientes mantidos intactos.

#### 2. Bug no cálculo de KPIs (`build_dashboard_stats`)

Antes: `total_pending = sum(status != 'paid')`, contando `cancelled` como pendente. Taxa de conversão usava `total_charges` no denominador, também inflado por cancelled.

Agora, conjuntos explícitos:
```python
PENDING_STATUSES = {"created", "sent", "pending", "enviando", "erro no envio"}
PAID_STATUS = "paid"
CANCELLED_STATUSES = {"cancelled", "cancelado"}
```

E novos campos retornados:
```python
{
    "total_pending", "pending_count",        # só PENDING_STATUSES
    "total_paid", "paid_count",              # só PAID_STATUS
    "total_cancelled", "cancelled_count",    # novo: só CANCELLED_STATUSES
    "active_count",                          # = pending + paid (sem cancelled)
    "total_active",                          # = R$ pending + R$ paid
    "paid_today_total", "paid_today_count",  # filtrado por data
    "total_charges",                         # = pending + paid + cancelled
    "timeline",                              # exclui cancelled
}
```

Frontend (`updateFinanceSummary`) agora usa `active_count` no denominador da conversion rate: `(paidCount / activeCount) * 100`.

#### 3. Lifecycle paid ↔ pending ↔ cancelled

Regras confirmadas com o usuário:

| Situação | Ação automática | UI exige confirmação? |
|---|---|---|
| Asaas diz `paid` (webhook) | marca `paid` | — |
| Já era `paid` + Asaas diz `paid` | noop idempotente | — |
| Era `pending` + Asaas diz `paid` | vira `paid` | — |
| Era `cancelled` + Asaas diz `paid` | vira `paid` (Asaas > manual) | — |
| Manual: `pending` → `paid` | grava | prompt simples |
| Manual: `paid` → `pending` | grava | **prompt reforçado** (pode reverter confirmação do Asaas) |
| Manual: qualquer → `cancelled` | grava | prompt simples |
| Manual: `cancelled` → `pending` | grava | prompt simples |

#### 4. Asaas webhook (`/webhook/asaas`)

Mudanças:
- **Validação opcional de secret** via env `ASAAS_WEBHOOK_SECRET` (header `asaas-access-token` ou query `?access_token=...`). Fail-closed se configurado. Default aceita tudo.
- **Idempotência**: se `pagamentos.status` já é `paid`, noop sem tocar no banco.
- **Snapshot refresh**: invalida `charge_snapshot`, `dashboard_stats` e `customer_rows_snapshot` após gravar — dash reflete instantaneamente sem F5.
- Resposta inclui `previous_status` para debugging.

#### 5. Toggle manual no endpoint `/api/finance/charges/{id}/status`

Já tinha `refresh_charge_snapshot` (F-037) + `refresh_customer_rows_snapshot` (F-036). Adicionado `refresh_dashboard_stats` pra completar o trio — agora KPIs do dash também atualizam sem F5.

**Validação end-to-end em produção (imagem staging-audit-e64bb6b):**

```
Cobrança R$ 339.00 de Samara Melo (558499921249)

ANTES  pending=R$177136.32 (790)  paid=R$0 (0)  paid_today=R$0
       customer: debt=R$339  paid=R$0

PATCH status=paid

DEPOIS pending=R$176797.32 (789)  paid=R$339 (1)  paid_today=R$339
       customer: debt=R$0  paid=R$339
```

Todos os números batem na casa decimal. Lifecycle consistente ponta-a-ponta.

**Deploy:** `alana-dashboard:staging-audit-e64bb6b`.

---

### F-040 — 🔴 SEV-1 — Financeiro zerado, migração completa para Asaas, filtros corrigidos

**Status:** ✅ fix-aplicado (2026-04-08) — reset total + migração de provider + 3 bugs

**Pedido do usuário:** *"se zeramos e começamos do 0 o financeiro, então que cobranças são essas? (...) zera tudo, o financeiro vai ser populado a partir de agora com o Assas, verifique se todas as devidas ligações estão feitas corretamente (...) filtros não estão funcionando"*.

#### 1. Reset completo de verdade

Diferente do F-039 (que recriou 790 charges a partir dos pacotes approved históricos), esta rodada **zerou tudo** e mantém apenas clientes:

```sql
DELETE FROM pagamentos;
DELETE FROM vendas;
DELETE FROM app_runtime_state WHERE key IN (
    'finance_charges_rows',
    'finance_dashboard_stats',
    'customer_rows'
);
-- data/payments.json também limpo dentro do container
```

Estado final: `pending=R$0 (0)`, `paid=R$0 (0)`, `total_charges=0`. Clientes (551), pacotes (incluindo os 157 approved) e pacote_clientes mantidos — a aba Clientes continua mostrando `qty` histórico.

A partir de agora, o financeiro **só recebe cobranças novas** criadas quando um pacote é confirmado pelo dash.

#### 2. Migração Mercado Pago → Asaas

**Divergência descoberta:** env configurado com `AS_AASAAS_TOKEN` e `AS_AASAAS_URL=https://api-sandbox.asaas.com/v3/` (Asaas), mas o código do `payments_worker` e `_process_job` instanciava `MercadoPagoClient`. Validei com o sandbox do Asaas:

```python
# Teste direto
asaas = AsaasClient()
c = asaas.create_customer(name='Teste', phone='5562900000000')  # 200 OK
p = asaas.create_payment_pix(c['id'], amount=10.00, due_date=due, description='teste')
# → pay_25v61nrz6e86v88i, status=PENDING, pix_payload 184 chars
```

Mudanças no código:

**`app/workers/background.py:payments_worker`**
- `MercadoPagoClient()` → `AsaasClient()`
- Fluxo: `create_customer(name, phone)` → `create_payment_pix(customer_id, amount, due_date, description)`
- Data format: `yyyy-mm-dd` (Asaas) ao invés de `yyyy-mm-ddTHH:MM:SS.000-03:00` (MP)
- `update_vote_state` grava `asaas_payment_id` em vez de `mercadopago_payment_id`. O `package_state_service` detecta a chave e grava no banco em `pagamentos.provider_payment_id` com `provider='asaas'`.
- Busca `pagamentos.id` via `SELECT provider_payment_id=eq.<asaas_id>` pra usar como `charge_id` na fila.

**`app/services/payment_queue_service.py:_process_job`**
- Substitui `MercadoPagoClient` por `AsaasClient`.
- Pass-through via `mp_client=asaas` (parâmetro legado). O `send_payment_whatsapp` já tem fallback pra campos Asaas (`invoiceUrl`, `paymentLink`, `value`), funciona sem mudança.
- Atualiza `pagamentos.status` via `SupabaseRestClient` direto (em vez de FinanceManager legacy).

**Observação pra produção:** o `AsaasClient.create_customer` hoje faz find-or-create por CPF. Como não passamos CPF real, todos os customers caem no CPF default `24971563792` (que no sandbox pertence a "Rodrigo Saraiva"). Em produção, **cada cliente precisa de CPF real** ou cada customer vai ser criado como novo. Solução: adicionar campo CPF opcional em `clientes` e passar no `create_customer`.

**Pra mudar sandbox → produção:**
```bash
docker service update \
  --env-add AS_AASAAS_URL=https://api.asaas.com/v3/ \
  --env-add AS_AASAAS_TOKEN=<token_producao> \
  alana-staging_alana-dashboard
```
Ou, melhor, no próprio serviço prod sem mexer no staging.

#### 3. Bug de filtros do Financeiro

**Antes:** `_filter_charge_rows` fazia match exato entre `filter.status` (da UI) e `charge.status` (do banco). Mas a UI envia `pending` e o banco tem `created` ou `sent` — filtro nunca batia, sempre 0 resultados.

**Agora:** mapa de aliases em `_filter_charge_rows`:
```python
STATUS_GROUPS = {
    "pending":    {"created", "sent", "pending"},
    "pendente":   {"created", "sent", "pending"},
    "enviando":   {"enviando"},
    "erro no envio": {"erro no envio"},
    "paid":       {"paid"},
    "pago":       {"paid"},
    "cancelled":  {"cancelled", "cancelado"},
    "cancelado":  {"cancelled", "cancelado"},
}
```

Testado: `status=pending` → pega `created`/`sent` corretamente.

#### 4. Refresh automático de snapshots após confirm_package

**Antes:** depois de confirmar um pacote, snapshots de charges/stats/customers ficavam stale até F5 manual.

**Agora:** novo task async `_refresh_snapshots()` disparado em `confirm_package`:

```python
async def _refresh_snapshots():
    # Primeiro refresh imediato (pega venda+pagamento recém-criados)
    await asyncio.to_thread(refresh_charge_snapshot)
    await asyncio.to_thread(refresh_dashboard_stats)
    await asyncio.to_thread(refresh_customer_rows_snapshot)
    # Aguarda payments_worker gravar provider_payment_id
    await asyncio.sleep(5)
    # Segundo refresh pra incluir ID do Asaas
    await asyncio.to_thread(refresh_charge_snapshot)
    await asyncio.to_thread(refresh_dashboard_stats)

asyncio.create_task(_refresh_snapshots())  # background
```

#### Validação E2E completa (pós-deploy, imagem staging-audit-2dfbbd2)

```
1. Estado inicial: pending=R$0 (0)
2. POST /api/packages/manual/confirm
     → pacote 3bdbad08... criado com Marcos Paulo 24pçs
3. POST /api/packages/Kjm2ulAKhE4q9w-grsBq53HliOHgA_0/confirm
     → sales.approve_package() grava venda+pagamento
     → payments_worker.create_payment:
         Asaas GET customers?cpfCnpj → 200
         Asaas POST payments → 200 → pay_o2mll9tcdlu1xy5w
     → update_vote_state grava provider_payment_id
     → enqueue_whatsapp_job → fila
     → queue worker: send_payment_whatsapp → sent
     → refresh snapshots automático
4. Estado final (SEM F5 manual):
     stats: pending=R$2983.20 (1)  paid=R$0 (0)
     charges: [status=created asaas=pay_o2mll9tcdlu1xy5w]
     customer Marcos Paulo: qty=132 debt=R$2983.20 paid=R$0
     filtros: pending=1 paid=0 enviando=0 ✅
```

Todos os números consistentes, provider Asaas funcionando, fila processando, dash atualizando em tempo real sem intervenção manual.

**Deploy final:** `alana-dashboard:staging-audit-f6bcc3c`.

#### 5. Fix de vínculo dos pacotes confirmados → cobranças (follow-up)

**Problema reportado pelo usuário:** depois do reset, o Financeiro ficou vazio, mas os 3 pacotes que apareciam em Pacotes Confirmados (últimas 72h) deveriam ter cobranças associadas automaticamente.

**Causa raiz (2 bugs encadeados):**

**Bug 1** — O `metrics/supabase_clients.py:clients_by_package` só incluía `{name, phone, qty}` no vote do snapshot, **sem** `unit_price`, `subtotal`, `total_amount`. Quando o `payments_worker` chamava `resolve_unit_price(poll_title)`, extraía o preço via regex do título. Mas pacotes cujo título foi editado pra algo sem `$XX,XX` (ex: "teste") ficavam com `unit_price=0` → `total=0` → Asaas rejeitava com `400 "value deve ser informado"`.

**Fix 1:** SELECT em `pacote_clientes` agora inclui `unit_price, subtotal, commission_amount, total_amount`. O `payments_worker` agora **prefere** os valores do snapshot (já persistidos pelo `sales.approve_package`) e só cai no fallback `resolve_unit_price` se o snapshot não trouxer. Raise explícito se o total calculado for ≤ 0.

**Bug 2** — Import quebrado no código de rollback de falha (`from app.services.metrics_service import _load_metrics, _save_metrics` — funções renomeadas na Operação Convergência F-025).

**Fix 2:** Removi o bloco de rollback automático. O rollback de `approved → closed` durante falha de pagamento é perigoso de qualquer forma (pode divergir do banco Supabase com o JSON local). Agora: se falhar todos os pagamentos do pacote, log error + pacote permanece `approved` no banco + operador usa "Reenviar" no dash depois de resolver a causa da falha.

**Recriação dos 3 pacotes dos últimos 72h:**

```python
# Via docker exec pra cada UUID:
from app.services.whatsapp_domain_service import SalesService
sales = SalesService(sb)
sales.approve_package(pkg_id)   # recria venda+pagamento idempotente
# Depois:
from app.workers.background import payments_worker
payments_worker(pkg_snapshot)   # cria customer/payment Asaas + queue WhatsApp
```

**Resultado final (validado via API):**

```
stats:   pending=R$3390.00 (3)  paid=R$0.00 (0)  total=3
charges: [
    16e8268a...  R$1356.00  sent  asaas=pay_jffjaqk11d8vx7h7
    fc872722...  R$1220.40  sent  asaas=pay_nhl04rri3zjb5lpa
    8d8f1693...  R$ 813.60  sent  asaas=pay_jrhohqzw14d3nqhy
]
customers: Marcos Paulo qty=108 debt=R$3390.00 paid=R$0.00
filters:   todos=3  pending=3  paid=0  ✅
```

**Deploy final de verdade:** `alana-dashboard:staging-audit-f6bcc3c`.

**Seguinte passo pra produção:**
1. Validar CPF real em `clientes` (campo a criar) e passar pro Asaas
2. Rotacionar token sandbox → produção no env do serviço prod
3. Configurar `ASAAS_WEBHOOK_SECRET` no endpoint `/webhook/asaas` e no painel do Asaas
4. Ativar throttling removendo/ajustando `TEST_MODE` ou `WHATSAPP_DISABLE_CLIENT_THROTTLE`

---

### F-042 — 🔴 SEV-1 — Resend bypassa fila e histórico (e o throttling junto)

**Status:** ✅ fix-aplicado (2026-04-09)

**Relato do usuário:** *"cliquei para reenviar uma cobrança no qual enviou normalmente, após clicar em ver fila, percebi que esse campo não está mostrando esse envio de cobrança no histórico"*.

**Causa raiz:** `main.py:resend_finance_charge` (branch `supabase_domain_enabled()`) chamava `send_payment_whatsapp` **síncrono** dentro do request HTTP, sem passar por `enqueue_whatsapp_job`. Consequências graves:

1. **Job não aparecia na fila** (`get_queue_snapshot` só lê da lista `payment_queue` gravada por `enqueue_whatsapp_job`).
2. **Bypassava todo o throttling/cooldown** — o `_client_send_lock` é aplicado dentro de `send_payment_whatsapp` mas o delay entre clientes (1-4 min em prod) e a pausa de café (5-10 min a cada 5-10 clientes) só tem efeito se passar pelo worker. Clicar "Reenviar" em massa podia furar o rate limit do WhatsApp e bloquear o número.
3. **Sem retry automático**. Se a Evolution API desse timeout, a cobrança ficava "órfã" — operador não sabia que falhou, sem retry.

**Fix:**

1. **`resend_finance_charge` agora enfileira**:
   - Cancela jobs abertos anteriores pra mesma `charge_id` (`cancel_open_jobs_for_charge`)
   - Resolve imagem do produto (drive_file_id → URL)
   - Busca payment Asaas se `asaas_id` já está linkado, senão deixa o worker criar on-demand
   - `enqueue_whatsapp_job(..., is_resend=True)`
   - **Não** atualiza `pagamentos.status` otimistamente (worker faz no final)
   - Retorna `{status, queued, job_id, cancelled_jobs}`

2. **Proteção `paid → sent`** no `_process_job`:
   ```python
   current = sb.select("pagamentos", columns="status", filters=[("id", "eq", charge_id)], limit=1)
   if current[0]["status"] == "paid":
       logger.info("já paid, não sobrescrevendo")  # idempotente
   else:
       sb.update("pagamentos", {"status": new_status, ...})
   ```
   Previne que um reenvio acidental reverta uma cobrança já confirmada pelo Asaas webhook.

3. **Refresh automático de snapshots** no final do `_process_job` (charges, stats, customers) — dash reflete sem F5.

**Validação E2E no staging:**
```
Antes  queue.summary = {sent: 18}
POST /api/finance/charges/e2f326d7.../resend
   → {status: success, queued: true, job_id: 7bf8d27c..., cancelled_previous: 0}
Depois (6s) queue.summary = {sent: 19}

Novo job visível em get_queue_snapshot:
   7bf8d27c  sent  charge=e2f326d7  is_resend=True  attempts=1/3
```

Worker processou o reenvio corretamente:
```
Iniciando processamento do job=7bf8d27c (charge=e2f326d7, phone=556293353390)
Asaas GET payments/pay_jffjaqk11d8vx7h7 → 200
Asaas GET payments/pay_jffjaqk11d8vx7h7/pixQrCode → 200
Resultado do envio whatsapp para job=7bf8d27c: sent
```

#### Verificação do fluxo completo da fila (pedido do usuário)

Pontos validados:

1. **Worker inicia no startup** — `main.py:startup_backfill_routing_once` chama `start_payment_queue_worker()` (F-035). Logs mostram:
   ```
   startup: worker da fila de pagamentos iniciado (F-035)
   startup: agendador de sincronização de pagamentos iniciado
   webhook_retry_loop started (interval=60s, stale_threshold=5min, batch=20)
   ```

2. **Throttling presente e ajustável via env** (`integrations/notifications/whatsapp.py`):
   - `_client_send_lock` — lock global, sempre ativo. Serializa envios.
   - `WHATSAPP_CLIENT_DELAY_MIN/MAX_SECONDS` — delay entre clientes. Default prod: 60-240s. Default staging (TEST_MODE=true): 0.
   - `WHATSAPP_INTER_MESSAGE_DELAY_MIN/MAX_SECONDS` — entre foto do produto e PIX copia-e-cola. Default prod: 7-15s. Staging: 0.
   - `WHATSAPP_BREAK_EVERY_MIN/MAX` — pausa de café a cada 5-10 clientes.
   - `WHATSAPP_BREAK_DURATION_MIN/MAX` — duração da pausa: 300-600s (5-10 min).
   - `WHATSAPP_BIG_BREAK_INTERVAL=4` — pausa grande a cada 4 pausas curtas.
   - `WHATSAPP_DISABLE_CLIENT_THROTTLE` — flag pra desligar tudo. Fica `true` automaticamente se `TEST_MODE=true`.

3. **Ligações dash → fila OK:**
   - Botão "Reenviar" → `POST /api/finance/charges/{id}/resend` → `enqueue_whatsapp_job` → fila ✅
   - Botão "Ver fila" → `GET /api/finance/queue` → `get_queue_snapshot` → modal ✅
   - `confirm_package` → `payments_worker` → `enqueue_whatsapp_job` → fila ✅
   - Webhook Asaas `PAYMENT_CONFIRMED` → `UPDATE pagamentos` → `refresh snapshots` ✅
   - Toggle manual (paid/pending/cancelled) → `refresh snapshots` (F-037) ✅

4. **Retry automático**: `_mark_failed_or_retry` aplica backoff exponencial (60s * attempts, cap 300s), até 3 tentativas, depois marca como `error`.

5. **Recovery de jobs travados**: `recover_stuck_jobs(stale_seconds=1800)` pega jobs em status `sending` há mais de 30min e volta pra `queued`. Chamado no início de `_worker_loop`.

**Throttling no staging**: desligado por `TEST_MODE=true`. Pra ligar com valores reduzidos e validar o algoritmo:
```bash
docker service update \
  --env-add WHATSAPP_DISABLE_CLIENT_THROTTLE=false \
  --env-add WHATSAPP_CLIENT_DELAY_MIN_SECONDS=5 \
  --env-add WHATSAPP_CLIENT_DELAY_MAX_SECONDS=15 \
  --env-add WHATSAPP_BREAK_EVERY_MIN=3 \
  --env-add WHATSAPP_BREAK_EVERY_MAX=5 \
  --env-add WHATSAPP_BREAK_DURATION_MIN=10 \
  --env-add WHATSAPP_BREAK_DURATION_MAX=30 \
  alana-staging_alana-dashboard
```

**Deploy:** `alana-dashboard:staging-audit-6b9fbcd`.

---

### F-031 follow-up — constraint votos_qty_check relaxada

Depois do fix acima, o teste do usuário disparou um segundo erro relacionado:

```
Supabase REST error 400 for /rest/v1/votos:
  code 23514 — new row for relation "votos"
  violates check constraint "votos_qty_check"
```

O voto sintético estava sendo criado com `qty=24` (pacote manual de atacado: 1 cliente leva todos os 24 de uma vez), mas o schema tinha `CHECK (qty IN (0, 3, 6, 9, 12))`. Essa constraint fazia sentido apenas pro fluxo de webhook do WhatsApp (alternativas fixas), mas bloqueia o fluxo manual.

**Fix:** migration `deploy/postgres/migration_F031_relax_votos_qty_check.sql`:

```sql
ALTER TABLE votos DROP CONSTRAINT IF EXISTS votos_qty_check;
ALTER TABLE votos ADD CONSTRAINT votos_qty_check
    CHECK (qty >= 0 AND qty <= 24 AND qty % 3 = 0);
-- Mesma coisa pra votos_eventos
```

Nova constraint aceita `0, 3, 6, 9, 12, 15, 18, 21, 24`. Zero linhas existentes violaram (todas caem em 0/3/6/9/12). Mantém a coerência "múltiplo de 3 do modelo de domínio".

---

### F-045 — ℹ️ SEV-3 — KPIs eram calculados em tempo real sem histórico imutável

**Sintoma:** todos os KPIs do dash (`total_pendente`, `votos_hoje`, `pacotes_open`, etc) eram calculados em tempo real lendo os dados brutos. Isso significa que **qualquer edição retroativa** (cancelar venda antiga, renomear `custom_title`, alterar `unit_price`) faz com que os números da semana passada mudem. Não é possível responder "como estava o total pendente ontem às 14h?" sem reprocessar tudo — e mesmo assim o resultado pode divergir.

**Risco:** análises temporais não-confiáveis. Qualquer decisão baseada em comparativo histórico (ex: "caímos 20% vs semana passada") pode ser artefato de edição, não de mudança de negócio.

**Fix:**

1. Nova tabela append-only `metrics_hourly_snapshots` (30+ colunas cobrindo votos, enquetes, pacotes, financeiro, clientes, fila WhatsApp, webhook inbox). UNIQUE por `hour_bucket` → idempotente.
2. Worker assíncrono `app/workers/metrics_snapshot_worker.py` iniciado em `startup`. Grava snapshot imediato + loop que dorme até a próxima hora cheia.
3. Endpoints: `POST /api/metrics/snapshot` (forçar captura) e `GET /api/metrics/history?hours=48` (consultar histórico).
4. Documentação completa em `docs/audit/PERSISTENCE.md` — matriz de persistência, casos de uso, queries SQL de análise histórica, débito técnico restante.

**Validação:**
```bash
curl -X POST https://staging-alana.v4smc.com/api/metrics/snapshot
# → {"status":"success","id":"2d93498c-..."}

curl https://staging-alana.v4smc.com/api/metrics/history?hours=2
# → 1 snapshot, hour_bucket=2026-04-09T04:00, votes=0 pending=R$813.6 paid=R$1356.0
```

**Migration:** `deploy/postgres/migration_F045_metrics_snapshots.sql` (aplicada no staging).
**Deploy:** `alana-dashboard:staging-audit-67c716f`.

---

### F-046 — ℹ️ SEV-3 — Garantia de confiabilidade: gaps cobertos do F-045

**Sintoma:** após F-045, ainda restavam três buracos pra a promessa "métricas 100% confiáveis daqui pra frente":

1. **Breakdowns por entidade** (top clientes, top enquetes, votos por hora, by_poll_today, by_customer_today/week) NÃO estavam no snapshot — só os totais agregados. Análise tipo "quem foi o top cliente em 2026-04-09" era impossível.
2. **Sem detecção de gaps**: se o worker do F-045 caísse por X horas, ninguém saberia. Snapshots de horas perdidas são perdidos pra sempre (não dá pra recriar passado).
3. **Edições in-place de labels mutáveis**: `clientes.nome`, `enquetes.titulo`, `pacotes.custom_title`, `pacotes.tag`, `pacote_clientes.unit_price` eram editáveis sem audit trail. Os totais agregados ficavam corretos, mas a label associada a um fato passado podia ter sido reescrita.

**Fix (3 partes):**

1. **`build_snapshot_payload` v2**: o worker passou a embutir o payload trimado de `/api/metrics` em `raw_stats.metrics_full`. Inclui `votos.by_hour`, `votos.by_poll_today`, `votos.by_customer_today/week`, `top_polls`, `top_clients`, comparativos pct vs ontem/semana/mês. Listas grandes (pacotes open/closed full, customers_map) são removidas pra não inflar o jsonb.

2. **`GET /api/metrics/health?window_hours=24`**: lê `metrics_hourly_snapshots`, calcula gaps (intervalos > 1h5min entre buckets), e retorna `status: ok|degraded|critical` baseado em (a) idade do último snapshot (`>2h` = critical) e (b) presença de gaps. Pronto pra scraping com Uptime Kuma.

3. **Migration `F046_field_history.sql`**: nova tabela append-only `field_history (table_name, record_id, field_name, old_value, new_value, changed_at, changed_by)` + 4 triggers `AFTER UPDATE` em `clientes(nome)`, `enquetes(titulo)`, `pacotes(custom_title, tag)`, `pacote_clientes(unit_price)`. Função auxiliar `field_value_at(table, id, field, timestamp)` retorna o valor que o campo tinha numa data passada.

**Validação:**
```bash
# Health: snapshot fresco + gap detection
curl https://staging-alana.v4smc.com/api/metrics/health
# → {"status": "ok", "snapshot_count": ..., "gaps": [], "minutes_since_last": ...}

# Audit: editar nome de cliente e checar field_history
UPDATE clientes SET nome='X' WHERE id='<id>';
SELECT * FROM field_history WHERE table_name='clientes' ORDER BY changed_at DESC LIMIT 1;
# → linha com old_value=nome anterior, new_value='X', changed_at=now()
```

**Migrations:** `deploy/postgres/migration_F046_field_history.sql` (aplicada).
**Limitação residual:** triggers podem ser bypassadas via `ALTER TABLE DISABLE TRIGGER`. Para auditoria mais ampla considerar `pgaudit` em prod.

---

## Métricas atuais do banco `[snapshot 2026-04-08]`

```
clientes:             551
enquetes:            1406  (open=1157, closed=249)
enquete_alternativas:4594
votos:               9800  (in=7424, out=2376)
votos_eventos:     15 095
pacotes:             2116  (open=936, closed=781, approved=154, cancelled=245)
pacote_clientes:     6203
vendas:              1142  (approved=1142)
pagamentos:          1142  (created=2, sent=106, paid=1034)
produtos:            1186
webhook_inbox:      10055  (received=9, processed=10002, failed=44)
```

Tamanho do banco (top 5):
```
votos_eventos     25 MB
webhook_inbox     18 MB
pacote_clientes    9 MB
votos              3 MB
pacotes            2 MB
```
