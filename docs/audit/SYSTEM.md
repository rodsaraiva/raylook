# Alana Dashboard — System Knowledge

> **Verdade atual** de como o sistema funciona. Incremental — ver regras no `README.md`.
> **Versão:** v1 (2026-04-08) | **Ambiente referência:** staging

---

## 1. Visão geral

O Alana Dashboard é uma plataforma que:

1. **Recebe votos** de enquetes do WhatsApp (clientes votam em tamanhos PMG com quantidades 0/3/6/9/12).
2. **Agrupa votos em pacotes** de capacidade fixa (24 unidades).
3. **Confirma pacotes** manualmente pelo operador via dashboard web.
4. **Gera vendas e cobranças PIX** no Asaas ao confirmar.
5. **Emite PDF de etiqueta** e envia ao estoque via WhatsApp.

Domínio: **revenda de roupas por enquete de WhatsApp**, onde cada pacote de 24 peças é uma "rodada de venda".

### 1.1 Stack `[verificado]`

| Componente | Tecnologia | Local |
|---|---|---|
| Web dashboard + API | FastAPI + Jinja2 + Uvicorn (Python) | Serviço Swarm `alana-staging_alana-dashboard` |
| Banco | Postgres (imagem `pgvector/pgvector:pg16`) | Container local do VPS, porta 5432 |
| API REST auto-gerada | PostgREST v14.1 | Serviço Swarm `alana-postgrest-staging`, schema `public` |
| Ingestão WhatsApp | Webhook HTTP (WHAPI + Evolution API) | Endpoint `POST /webhook/whatsapp` |
| Listener Baileys | `/root/baileys-poll-listener/` | Projeto separado (não auditado ainda) |
| Pagamentos | Asaas (PIX) | Integração HTTP em `integrations/asaas/` |
| Envio mensagens | Evolution API v2.3.6 | Serviço Swarm `evolution_v2` |
| Reverse proxy | Traefik v3.0.1 + Let's Encrypt | `staging-alana.v4smc.com` |

**Fonte de verdade do código:** `/root/alana-staging-supabase/` (extraído de zip, **não é repo git**). Entry point real: `main.py` na raiz (885 linhas). `app/main.py` é só um wrapper ASGI de 10 linhas.

### 1.2 Topologia de dados `[verificado]`

```
 WhatsApp
    │  (cliente vota em enquete)
    ▼
 WHAPI / Evolution API
    │  (webhook HTTP POST)
    ▼
 POST /webhook/whatsapp  (main.py:155)
    │
    ├─► webhook_inbox  (idempotência via event_key UNIQUE)
    │
    ▼  normalize_webhook_events()  (whatsapp_domain_service.py:130)
    │
    ▼  VoteService.process_vote()   (whatsapp_domain_service.py:462)
    │
    ├─► enquetes          (upsert se nova)
    ├─► enquete_alternativas
    ├─► clientes          (upsert por celular)
    ├─► votos_eventos     (audit trail: action=vote|remove)
    ├─► votos             (upsert por enquete_id+cliente_id)
    │
    ▼  PackageService.rebuild_for_poll()   (whatsapp_domain_service.py:341)
    │
    ├─► pacotes           (INSERT quando acumula 24 qty)
    └─► pacote_clientes   (INSERT 1 por cliente no pacote fechado)

────────────────────────────────────────────────────────────

 Dashboard web (operador olha)
    │
    ▼  GET /  → template Jinja  → lê métricas do arquivo JSON em data/
    │
    ▼  POST /api/packages/{id}/confirm   (main.py:449)
    │     └─► SalesService.approve_package()
    │           ├─► UPSERT vendas
    │           └─► UPSERT pagamentos (status='created')
    │
    ▼  asyncio.create_task(pdf_worker)         → gera PDF + envia via WhatsApp
    ▼  asyncio.create_task(payments_worker)    → Asaas (customer + PIX) + envia link ao cliente
```

---

## 2. Modelo de dados `[verificado]`

12 tabelas, **zero triggers**, 1 função (`next_pacote_sequence`). Toda invariante vive no código Python.

### 2.1 Tabelas principais

| Tabela | Chave | Colunas críticas | FK |
|---|---|---|---|
| `enquetes` | `id uuid` | `external_poll_id UNIQUE`, `provider`, `titulo`, `status: enquete_status` (`open`/`closed`) | `produto_id→produtos` |
| `enquete_alternativas` | `id` | `enquete_id`, `label`, `qty` | → `enquetes` |
| `clientes` | `id` | `celular UNIQUE`, `nome` | — |
| `produtos` | `id` | `titulo`, `preco` | — |
| `votos` | `id` | `enquete_id+cliente_id UNIQUE`, `qty`, `alternativa_id`, `status: voto_status` (`in`/`out`), `asaas_customer_id`, `asaas_payment_id`, `financial_details jsonb` | → `enquetes`, `clientes`, `alternativas` |
| `votos_eventos` | `id` | `enquete_id`, `cliente_id`, `action` (`vote`/`remove`), `occurred_at` | → `enquetes`, `clientes` |
| `pacotes` | `id` | `enquete_id+sequence_no UNIQUE`, `capacidade_total=24`, `total_qty`, `participants_count`, `status: pacote_status`, `opened_at`, `closed_at`, `approved_at`, `cancelled_at`, `pdf_status`, `pdf_file_name`, `custom_title`, `tag` | → `enquetes` |
| `pacote_clientes` | `id` | `pacote_id+cliente_id UNIQUE`, `voto_id`, `qty`, `unit_price`, `subtotal`, `commission_percent=13`, `commission_amount`, `total_amount`, `status text='closed'` | → `pacotes`, `clientes`, `votos`, `produtos` |
| `vendas` | `id` | `pacote_id+cliente_id UNIQUE`, `pacote_cliente_id`, `qty`, `unit_price`, `subtotal`, `commission_percent CHECK=13`, `total_amount`, `status: venda_status` | → `pacotes`, `clientes`, `produtos`, `pacote_clientes` |
| `pagamentos` | `id` | `venda_id UNIQUE`, `provider` (`asaas`/`mercadopago`), `provider_payment_id UNIQUE`, `payment_link`, `pix_payload`, `paid_at`, `status: pagamento_status` | → `vendas` |
| `webhook_inbox` | `id` | `provider`, `event_kind`, `event_key UNIQUE`, `payload_json`, `received_at`, `processed_at`, `status: webhook_status`, `error` | — |
| `app_runtime_state` | ? | usado pelos workers | — |

### 2.2 Enums `[verificado]`

| Enum | Valores |
|---|---|
| `enquete_status` | `open`, `closed` |
| `pacote_status` | `open`, `closed`, `approved`, `cancelled` |
| `voto_status` | `in`, `out` |
| `venda_status` | **apenas `approved`** ⚠️ (sem `cancelled`/`refunded`) |
| `pagamento_status` | `created`, `sent`, `paid` (observados) |
| `webhook_status` | `received`, `processed`, `failed` |

### 2.3 Invariantes de domínio

Regras que o sistema deveria manter. Marcação `[V]` = verificada com usuário, `[H]` = hipótese.

1. **I1 [H]** — Todo `pacote` com `status='closed'` ou `'approved'` tem `total_qty = capacidade_total = 24`.
2. **I2 [V]** — `pacote.participants_count = count(pacote_clientes WHERE pacote_id=...)` e `pacote.total_qty = sum(pacote_clientes.qty)`. **Exceção**: pacotes `open` não materializam `pacote_clientes` (são virtuais).
3. **I3 [V]** — `sequence_no` é único por `enquete_id` (UNIQUE constraint no banco).
4. **I4 [H]** — Todo `voto.status='in'` tem `qty>0`; todo `voto.status='out'` tem `qty=0`.
5. **I5 [V]** — Toda `venda` tem exatamente 1 `pagamento` (1:1 via `pagamentos.venda_id UNIQUE`).
6. **I6 [H]** — Toda `venda` pertence a um `pacote` com `status='approved'` (vendas só nascem ao confirmar pacote).
7. ~~**I7** — `closed_at >= created_at`~~ → **REVOGADA**. Ver §2.4 abaixo.
8. **I8 [V]** — `pacote_clientes.voto_id` sempre aponta para um `voto` que existe.
9. **I9 [V]** — `webhook_inbox` com `status='received'` eventualmente transita para `processed` ou `failed` (não fica parado).
10. **I10 [V]** — `enquete.status='closed'` ⇒ não há `pacotes.status='open'` nessa enquete.

### 2.4 Semântica de timestamps `[V — confirmada com usuário 2026-04-08]`

⚠️ **Crítico para qualquer relatório temporal.**

| Campo | O que representa | Fonte |
|---|---|---|
| `pacotes.created_at` | Quando a linha foi inserida no Postgres. **Não tem significado de negócio.** Pode ser de hoje mesmo o pacote sendo de meses atrás (replay/migração). | `DEFAULT now()` |
| `pacotes.closed_at` | **Timestamp do voto que fechou o pacote** (último voto que completou os 24). Vem do timestamp original da mensagem WhatsApp. | Mensagem WHAPI/Evolution |
| `pacotes.opened_at` | (a verificar) provavelmente o timestamp do **primeiro** voto do pacote. | — |
| `pacotes.approved_at` | Quando o operador clicou "Confirmar" no dashboard. | `now()` ao executar `/confirm` |
| `pacotes.cancelled_at` | Quando o operador clicou "Rejeitar" no dashboard. | `now()` ao executar `/reject` |
| `votos.voted_at` | Timestamp original da mensagem do WhatsApp. | Mensagem WHAPI/Evolution |
| `webhook_inbox.received_at` | Quando o webhook chegou no servidor. | `DEFAULT now()` |

**Regra de uso em queries:**
- Para "histórico cronológico do negócio" → `closed_at` / `opened_at` / `voted_at` / `approved_at`
- Para "auditoria de quando entrou no banco" → `created_at` / `received_at`
- **NUNCA** `ORDER BY pacotes.created_at` em relatórios mostrados ao operador.

### 2.5 Edição de pacotes confirmados `[V — descrição do usuário 2026-04-08; FEATURE NÃO EXISTE NO CÓDIGO ATUAL — ver F-022]`

**Comportamento esperado pelo usuário:**

- Pacote `confirmado` (`status='approved'`) é **imutável por padrão**.
- Existe um botão "Editar Pacote Confirmado" no dashboard que permite:
  1. Remover um membro do pacote → arrastá-lo de volta pra "votos disponíveis".
  2. O membro removido aparece na fila com **as peças atualmente selecionadas** no `votos` (último voto), **não** com a quantidade que ele tinha quando o pacote fechou.
  3. Se for substituído por outro membro e voltar pra fila depois, ele aparece com seu **último voto** (pode ter mudado).
- Substituições recompõem o pacote para somar 24 novamente.

⚠️ **No código deployado hoje (2026-04-08, imagem `staging-edit-columns-fix`), esse feature NÃO EXISTE.** Ver `FINDINGS.md#F-022`. O botão `✏️` só permite editar o título via `prompt()`, e nem isso é persistido (chama um endpoint inexistente).

---

## 3. Fluxos principais

### 3.1 Fluxo de voto `[verificado via código]`

Entrada: `POST /webhook/whatsapp` (`main.py:155`) autenticado por `x-webhook-secret` (opcional — se `WHATSAPP_WEBHOOK_SECRET` não setado, aceita qualquer).

```python
# main.py:155-187 (simplificado)
@app.post("/webhook/whatsapp")
async def webhook(payload: dict):
    events = normalize_webhook_events(payload)  # WHAPI/Evolution parser
    for ev in events:
        # 1. Gravar em webhook_inbox (idempotente via event_key)
        client.insert("webhook_inbox", {...}, on_conflict="event_key")

        # 2. VoteService.process_vote(ev)
        # 2.1 upsert enquete
        poll = client.select("enquetes", filter=external_poll_id==ev.poll_id)
        if not poll:
            poll = client.insert("enquetes", {...})

        # 2.2 upsert cliente (por celular)
        cli = client.upsert_one("clientes", {"celular": ev.phone, ...}, on_conflict="celular")

        # 2.3 insert votos_eventos (append-only audit)
        client.insert("votos_eventos", {"action": "vote" if qty>0 else "remove", ...})

        # 2.4 upsert votos
        client.upsert_one("votos", {
            "enquete_id": poll["id"], "cliente_id": cli["id"],
            "qty": ev.qty, "status": "in" if ev.qty>0 else "out",
            ...
        }, on_conflict="enquete_id,cliente_id")

        # 2.5 PackageService.rebuild_for_poll(poll.id)
        votes = client.select("votos", filter=enquete_id==poll.id AND qty>0 AND status=="in")
        subsets = subset_sum_24(votes)   # algoritmo em tests/test_subset_sum_edge_cases.py
        for subset in subsets:
            pacote = client.insert("pacotes", {"status": "closed", "total_qty":24, ...})
            for vote in subset:
                client.insert("pacote_clientes", {...})
        # sobras ficam em pacote "open" virtual (sem pacote_clientes)
```

**Idempotência:** garantida pela UNIQUE `webhook_inbox.event_key`. Mas a falha pós-inserção no webhook_inbox **não é retentada automaticamente** — worker de retry não encontrado no código-fonte auditado.

**Mudança de voto:** o `upsert on_conflict="enquete_id,cliente_id"` sobrescreve o voto anterior. `votos_eventos` mantém histórico.

**Cancelamento de voto (`qty=0`):** `voto.status='out'`, `qty=0`. **Pacotes já fechados NÃO são reabertos** — decisão de negócio implícita. O voto sai das sobras, mas não remove o cliente de pacotes fechados.

### 3.2 Ciclo de vida do pacote `[parcialmente verificado]`

```
        ┌──────────────────────────┐
        ▼                          │
     [open]  ◄── sobra de rebuild_for_poll
        │
        │ (acumula 24 qty automaticamente)
        ▼
    [closed] ─── closed_at setado
        │
        │ POST /api/packages/{id}/confirm  (main.py:449)
        │   └─► SalesService.approve_package()
        │        ├─ INSERT vendas (1 por pacote_cliente)
        │        └─ INSERT pagamentos (status='created')
        │   └─► asyncio.create_task(pdf_worker)
        │   └─► asyncio.create_task(payments_worker)   → Asaas cria PIX → envia WhatsApp
        ▼
    [approved] ─── approved_at setado
        │
        │ POST /api/packages/{id}/revert  (main.py:757)
        │ ↩─ volta para [closed] (revert_package)
        │
        │ POST /api/packages/{id}/reject  (main.py:740)
        ▼
   [cancelled] ─── cancelled_at, cancelled_by devem ser setados
```

⚠️ **Observação importante**: no estado atual do banco, **todos os 245 pacotes `cancelled` têm `cancelled_at=NULL`, `cancelled_by=NULL`, `approved_at=NULL`** (ver `FINDINGS.md#F-005`), e todos foram criados no mesmo dia (2026-03-30). Isso **não é** o fluxo normal de `/reject` — é um bulk UPDATE feito por script externo.

### 3.3 Geração de pagamento `[parcialmente verificado]`

- **payments_worker** (`app/workers/background.py`) roda após confirm, em background.
- Para cada `voto` do pacote:
  1. `ensure_asaas_customer(voto)` → cria cliente Asaas se não existe, grava `voto.asaas_customer_id`.
  2. `create_payment_pix(customer_id, amount, due_date, description)` → grava `voto.asaas_payment_id`, `voto.financial_details`.
  3. `send_payment_whatsapp(voto)` → envia QR/link via Evolution API.
- **Idempotência:** cada passo checa se o ID já existe antes de chamar. Mas estado parcial (ex: customer criado, payment falhou) não é retentado.

### 3.4 Máquina de estados da venda `[verificado]`

**Inexistente.** O enum `venda_status` só tem `approved`. Vendas nunca mudam de estado. Não há cancelamento, reembolso ou estorno a nível de dado. O status financeiro real é derivado do `pagamento` (`created`/`sent`/`paid`).

---

## 4. Rotas HTTP `[verificado — main.py]`

### 4.1 Públicas (sem auth)

| Método | Rota | Função | Linha |
|---|---|---|---|
| GET | `/health` | health check Traefik | 136 |
| GET | `/api/supabase/health` | conectividade Postgres | 142 |
| GET | `/` | dashboard HTML | 229 |
| GET | `/api/metrics` | métricas JSON | 234 |
| GET | `/metrics` | Prometheus metrics | 872 |

### 4.2 Webhook (auth opcional por secret)

| Método | Rota | Função | Linha |
|---|---|---|---|
| POST/PATCH | `/webhook/whatsapp` | ingestão de votos | 155 |

### 4.3 Administrativas — **SEM AUTH** ⚠️

Ver `FINDINGS.md#F-009`.

| Método | Rota | Função | Linha |
|---|---|---|---|
| POST | `/api/refresh` | recalcula métricas | 252 |
| POST | `/api/packages/{pkg_id}/confirm` | aprovar pacote → gera vendas + PIX | 449 |
| POST | `/api/packages/{pkg_id}/approve` | alias de confirm | 734 |
| POST | `/api/packages/{pkg_id}/reject` | cancelar pacote | 740 |
| POST | `/api/packages/{pkg_id}/revert` | reverter aprovação | 757 |
| POST | `/api/packages/{pkg_id}/retry_payments` | retentar pagamentos | 774 |
| POST | `/api/packages/backfill-routing` | enriquecer poll_id/chat_id | 792 |
| GET | `/api/finance/charges` | lista cobranças (expõe financeiro) | 812 |
| GET | `/api/finance/stats` | estatísticas receita | 819 |

---

## 5. Variáveis de ambiente `[verificado — .env.example]`

Ver lista completa em `app/config.py` e `.env.example`. Críticas:

```
# Banco / PostgREST
SUPABASE_URL=...                   # base PostgREST
SUPABASE_SERVICE_ROLE_KEY=...      # JWT com role admin
SUPABASE_SCHEMA=public
SUPABASE_DOMAIN_ENABLED=true       # ativa novo fluxo Supabase (vs Baserow legado)

# Webhook
WHATSAPP_WEBHOOK_ENABLED=true
WHATSAPP_WEBHOOK_SECRET=...        # ⚠️ se vazio, webhook aceita sem validar

# Integrações
EVOLUTION_API_URL=...
EVOLUTION_API_KEY=...
EVOLUTION_INSTANCE_NAME=...
AS_AASAAS_TOKEN=...                # ⚠️ typo conhecido no nome
AS_AASAAS_URL=https://api.asaas.com/v3/

# Negócio
COMMISSION_PERCENT=13
PACKAGE_LIMIT_TOTAL=24
ESTOQUE_PHONE_NUMBER=...
OFFICIAL_GROUP_CHAT_ID=...
```

---

## 6. Workers & tarefas em background `[verificado]`

Definidos em `app/workers/background.py`. Lançados via `asyncio.create_task()` dentro do request de `/confirm` — **não sobrevivem a restart do container** e **não são retentados** se falharem.

| Worker | Gatilho | Efeito |
|---|---|---|
| `pdf_worker(pkg_id)` | `POST /confirm` | Gera PDF de etiqueta (`estoque/pdf_builder.py`), envia ao WhatsApp do estoque. Atualiza `pdf_status`/`pdf_file_name`/`pdf_sent_at`/`pdf_attempts`. |
| `payments_worker(pkg_id)` | `POST /confirm` | Cria customer Asaas + PIX + envia link ao cliente. Atualiza `voto.asaas_*`. |

**Não há worker de retry de `webhook_inbox`** (ver `FINDINGS.md#F-003`).

---

## 7. Dependências externas `[verificado]`

- **Asaas** (pagamentos PIX) — endpoint `https://api.asaas.com/v3/` (staging usa sandbox `api-sandbox.asaas.com`)
- **Evolution API** v2.3.6 (WhatsApp) — serviço Swarm local
- **WHAPI** (webhook source) — externa
- **Google Drive** (thumbs de fotos) — credentials em `/root/alana-staging-nojson-20260330/credentials.json` (mount do serviço)
- **Baserow** — banco legado da produção, ver §7.1

### 7.1 Baserow (banco legado / origem de verdade da PROD) `[verificado 2026-04-08]`

A **produção** (`alana.v4smc.com`) usa o Baserow como fonte de verdade de votos e enquetes (`METRICS_SOURCE` não está setado como `supabase` em prod — verificar). O **staging** foi migrado para Postgres (`METRICS_SOURCE=supabase`, `BASEROW_COMPAT_WRITE=false`) — o staging hoje não escreve mais no Baserow.

- **URL:** `https://base.v4smc.com`
- **Database ID:** 6
- **Auth:** `BASEROW_API_TOKEN` (32 chars, disponível no env do serviço Swarm `alana-dashboard_alana-dashboard` de prod)
- **Acesso a partir do staging:** ✅ token funciona (testado: HTTP 200, ~470ms)

**Schema (campos):**

| Tabela | ID | Linhas (2026-04-08) | Propósito |
|---|---|---|---|
| **Votos** | 17 | 20 169 | Eventos brutos de voto do WhatsApp (1 linha por vote_updated). Campos: `eventKey`, `eventType`, `chatId`, `pollId`, `voterPhone`, `voterName`, `optionId`, `optionName`, `qty`, `votesJson`, `timestamp`, `timestampBR`, `rawJson`, `Id_pkg` (vínculo a pacote, preenchido só após fechamento). |
| **Enquetes** | 18 | 2 135 | Postagens de enquete. Campos: `pollId`, `chatId`, `messageId`, `createdAtTs`, `createdAtBR`, `title`, `optionsJson`, `optionsCount`, `rawJson`, `driveFolderId`, `driveFileId`, `mediaMessageId`, `mediaTs`, `produto`, `valor`, `tamanhos`, `detalhes`. |
| **Pagamentos** | 19 | 123 | Webhooks do Asaas (payment.created / payment.confirmed / ...). Campos: `ID`, `Event`, `Account.id`, `Payment.id`, `Payment.dateCreated`, `Payment.customer`, `Payment.value`, `Payment.description`, `Payment.billingType`, `Payment.pixTransaction`, `Payment.pixQrCodeId`, `Payment.status`, `Payment.paymentDate`, `Payment.confirmedDate`, `Payment.netValue`, `Payment.dueDate`, `Payment.invoiceUrl`, `Payment.invoiceNumber`, `Payment.object`. |

**⚠️ NÃO existe tabela de "pacotes" no Baserow.** A entidade "pacote" é uma invenção do novo backend Supabase — no Baserow os pacotes são **implícitos** (campo `Id_pkg` na tabela de votos indica que um voto foi alocado a um pacote). A migração Baserow → Postgres teve que **reconstruir** os pacotes rodando `rebuild_for_poll` sobre os votos importados.

**Divergência atual staging ↔ prod (Baserow):**

| Entidade | Baserow (prod) | Postgres (staging) | Delta |
|---|---:|---:|---:|
| Votos (eventos) | 20 169 | 15 095 | **−5 074** no staging |
| Enquetes | 2 135 | 1 406 | **−729** no staging |
| Pagamentos | 123 | 1 142 | **+1 019** no staging ⚠️ |

**Interpretação:** Staging tem MENOS votos/enquetes (migração incompleta ou filtro de data) e MAIS pagamentos (inserções sintéticas — provavelmente geradas por testes ou pelo fluxo `approve_package` que insere em `pagamentos` local sem bater no Asaas/Baserow). Isso explica as anomalias de F-002 e F-005.

---

## 8. Testes existentes `[verificado]`

`/root/alana-staging-supabase/tests/unit/` — 20+ arquivos, ~150 testes pytest. Cobertura principal:

- `test_analyze_votos*.py` — lógica de agrupamento de votos (33 testes)
- `test_subset_sum_edge_cases.py` — algoritmo de pacote 24 (13 testes)
- `test_main_api_*.py` — endpoints (10 testes)
- `test_whatsapp_domain_service.py` — ingestão webhook

**Não há CI/CD** — testes rodam manualmente ou não rodam. O dir `tests/` está dentro do zip de deploy, então vai pro container, mas não há evidência de execução automatizada.

---

## 9. Pontos que ainda precisam ser verificados `[a verificar]`

- [ ] Código do `/root/baileys-poll-listener/` — como o listener Baileys se integra.
- [ ] Existe alguma task/worker de retry de `webhook_inbox`?
- [ ] Como exatamente o operador "cancela" um pacote na UI — confere com o fluxo `/reject`?
- [ ] `app_runtime_state` — usado pra quê?
- [ ] `pacote.tag`, `pacote.custom_title` — de onde vêm?
- [ ] Fluxo real de aprovação de pagamento no Asaas — webhook de volta? polling?
- [ ] Onde vivem os arquivos `data/metrics.json`, `data/payments.json` (volume Docker)?
- [ ] Relação `produto_id` em `enquetes` vs `pacote_clientes` vs `vendas` — por que redundante?
- [ ] Por que `pacote_clientes.commission_percent` pode variar mas `vendas.commission_percent` tem CHECK=13?
