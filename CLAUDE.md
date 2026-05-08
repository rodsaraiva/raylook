# CLAUDE.md — raylook (sandbox local)

Contexto e convenções deste projeto. Instruções globais do VPS estão em `/root/.claude/CLAUDE.md`.

---

## ⚠️ INVARIANTE PRINCIPAL — Não impactar a Alana

**raylook é sandbox de dev local.** A prod da Alana (`alana.v4smc.com`, container `alana_dashboard`, Postgres `alana_staging`, repo `V4MarcosPaulo/projeto_alana`) **deve ficar intocável a todo tempo**. Em qualquer dúvida, default = não toca.

Salvaguardas já implementadas:
- Sem `git remote` configurado → `git push` é impossível.
- Sem `.github/workflows/` → não há CI.
- `RAYLOOK_SANDBOX=true` (default) → Asaas/Resend são stub, Evolution removido.
- `DATA_BACKEND=sqlite` (default) + lockout em sandbox → nunca conecta no Postgres compartilhado, mesmo se alguém setar `SUPABASE_URL`.

**Não desfazer essas salvaguardas sem aprovação explícita.**

## O que é

Sandbox local derivado do Alana Dashboard. Mesma lógica de domínio (vendas por enquetes WhatsApp → pacotes via subset-sum → cobrança PIX), mas rodando 100% local com SQLite e integrações em stub.

Em uso ativo agora: refator UI v2 (`docs/ui-mockups/`, `static/ui-mockups/`), experimentação livre.

## Stack

- FastAPI (`main.py` monolito ~3000 linhas — tech debt herdado)
- SQLite local (`data/raylook.db`, schema em `deploy/sqlite/schema.sql`)
- Acesso via `SQLiteRestClient` (mesma interface do `SupabaseRestClient`, em `app/services/sqlite_service.py`)
- Stubs: Asaas, Resend (nunca batem em API real em sandbox)
- Removidos: Evolution API, MercadoPago, n8n, Docker stacks, deploy/postgres/

## Arquivos críticos

| Path | Por quê |
|------|---------|
| `app/config.py` | `RAYLOOK_SANDBOX`, `DATA_BACKEND` — flags de blindagem |
| `app/services/supabase_service.py` | `from_settings()` faz lockout pra SQLite em sandbox |
| `app/services/sqlite_service.py` | Backend SQLite com interface PostgREST |
| `deploy/sqlite/schema.sql` | Schema fonte da verdade |
| `main.py` | Endpoints + middleware auth + startup |
| `app/services/whatsapp_domain_service.py` | Ingestion webhook + subset-sum |
| `static/js/dashboard.js` | Frontend admin (~2700 linhas) |
| `templates/index.html` | Template principal admin |

## Convenções herdadas (ainda valem)

- **Datas em URL PostgREST:** sufixo `Z`, não `+00:00`.
- **Filtros múltiplos no mesmo campo:** lista de tuples em `select_all`.
- **Phones:** só dígitos; `_phone_variants` pra comparar com/sem DDI 55.
- **Nomes de cliente:** `_sanitize_name` remove `\n\r\t` e colapsa espaços.
- **Testes CI:** `DASHBOARD_AUTH_DISABLED=true` no env.

## Como rodar localmente

```bash
cd /root/rodrigo/raylook
PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 main.py
# ou
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

`data/raylook.db` é criado automaticamente do schema na primeira execução.

## Testes

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v
```

Testes de Evolution/Notifications/Estoque foram removidos junto com o código.

## Pendências do desvinculamento (ver `docs/superpowers/specs/` quando existir)

- 2.5 — WHAPI próprio do raylook (precisa token + grupo provisionados)
- 2.6 — Google Drive próprio do raylook (precisa Service Account + pasta)
- 6 — Verificação ponta a ponta (subir app, testar fluxo completo)

## ⚠️ Segredos vazados em git history (ações fora do código)

Estavam no código antes da limpeza. **Devem ser revogados no painel de cada serviço:**

- Resend API key: `re_MdJMdtW2_FWLJ3T7AFB1Kq5ZXQqFARKbw`
- Evolution API tokens: `EA80B304B771-...`, `B2141A40B331-...`, `580E7D47FF7B-...`
- N8N JWT (era `n8n_Alana/mcp_n8n.json`)

Recomendado: rodar `gitleaks detect --source . -v` ou `trufflehog filesystem .` pra varrer histórico.

## Comunicação

- Respostas pt-BR, ≤100 palavras salvo necessidade real.
- Uma frase de contexto antes do primeiro tool call.
- Fim de tarefa: 1-2 frases com o que mudou + próximo passo.
- Não narrar raciocínio interno.
