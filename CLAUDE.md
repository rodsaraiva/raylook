# CLAUDE.md — raylook

Contexto e convenções deste projeto. Instruções globais do VPS em `/root/.claude/CLAUDE.md`.

---

## O que é

Raylook Dashboard — gerencia vendas via enquetes do WhatsApp (subset-sum em pacotes de 24 peças → cobrança PIX via Asaas). **Está em produção** em `https://raylook.v4smc.com`.

Stack em prod:
- FastAPI + Jinja2 (`main.py` ainda monolito ~3k linhas — tech debt herdado)
- Postgres 16 dedicado (stack `raylook_*` no Swarm, volume próprio)
- PostgREST 14 como camada REST
- Frontend admin: JS vanilla (`static/js/dashboard_v2.js`)
- Frontend cliente: portal em `templates/portal_*.html`
- Asaas (PIX cobrança real), WHAPI (WhatsApp Cloud), Resend (email)
- CI/CD: GitHub Actions builda + faz `docker stack deploy` via SSH

## ⚠️ Isolamento total

Tudo do raylook fica em containers `raylook_*` (postgres, postgrest, dashboard). **Não compartilha banco com Alana, N8N, Evolution ou outros projetos do VPS.** Cualquer mudança aqui não pode afetá-los.

Antes de qualquer migration em prod:
- `BEGIN; ... COMMIT;` com pré-check de violação
- `pg_get_constraintdef()` pra confirmar nomes
- ROLLBACK fácil se algo errar

## Flags de runtime (env vars no Swarm)

| Var | Prod | Função |
|---|---|---|
| `RAYLOOK_SANDBOX` | `false` | `true` faz Asaas + sync rodarem como stub |
| `RESEND_EMAIL_STUB` | `true` | `true` mantém envio de email (Resend) só logado — desacopla de `RAYLOOK_SANDBOX` |
| `DATA_BACKEND` | `postgres` | `sqlite` é só pra dev local |
| `ASAAS_PROD_TOKEN` | setado | Token de produção da conta Raylook no Asaas |

`deploy/.env` (gitignored) guarda esses valores e é lido pelo CI no `docker stack deploy`.

## Dev local

```bash
cd /root/rodrigo/raylook
PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 main.py
# ou
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Local roda com SQLite (`data/raylook.db`) e `RAYLOOK_SANDBOX=true` (default). Nada bate em APIs externas reais.

## Testes

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v
```

Convenções herdadas:
- **Integração > mock** — especialmente DB. Mocked tests passam enquanto migration quebra em prod.
- Type-check (`mypy`) e lint não garantem UX — UI precisa ser aberta no browser.

## Arquivos críticos

| Path | Por quê |
|------|---------|
| `app/config.py` | Flags (`RAYLOOK_SANDBOX`, `DATA_BACKEND`, `RESEND_EMAIL_STUB`) |
| `app/services/supabase_service.py` | Cliente PostgREST + lockout |
| `app/services/whatsapp_domain_service.py` | Webhook ingest + subset-sum + dedup via `webhook_inbox` |
| `app/services/portal_service.py` | Portal do cliente |
| `app/routers/dashboard.py` | API admin (`/api/dashboard/*`) |
| `integrations/asaas/client.py` | Cliente Asaas (gated por `_sandbox_enabled()`) |
| `deploy/postgres/schema.sql` | Schema canônico Postgres |
| `deploy/sqlite/schema.sql` | Schema canônico SQLite (espelha o Postgres) |
| `deploy/docker-stack.yml` | Service definitions Swarm |
| `deploy/.env` | Env vars de prod (gitignored, no host) |
| `tools/backfill_whapi.py` | Backfill polls + votos via WHAPI (dentro da imagem agora) |

## Deploy

**Sempre via GitHub Actions.** Push em `main` → CI builda imagem `ghcr.io/rodsaraiva/raylook:<sha>` + `:latest` → SSH no servidor → `docker stack deploy`. Tempo total: 40-90s.

Nunca rodar `docker service update --force` ou similar fora do CI sem autorização — sobrescrita do `--env-add` é fácil de perder se feita manual.

## Convenções herdadas

- **Datas em URL PostgREST:** sufixo `Z`, não `+00:00`.
- **Filtros múltiplos no mesmo campo:** lista de tuples em `select_all()`.
- **Phones:** só dígitos; `_phone_variants` pra comparar com/sem DDI 55.
- **Nomes de cliente:** `_sanitize_name` remove `\n\r\t` e colapsa espaços.

## Comunicação

- Respostas pt-BR, ≤100 palavras salvo necessidade real.
- Uma frase de contexto antes do primeiro tool call.
- Fim de tarefa: 1-2 frases com o que mudou + próximo passo.
- Não narrar raciocínio interno.
