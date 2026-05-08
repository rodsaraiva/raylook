# raylook

Sandbox local derivado do Alana Dashboard. FastAPI + SQLite + integrações em stub.

> ⚠️ Sandbox de dev. Nada aqui pode tocar a prod da Alana (`alana.v4smc.com`).
> Salvaguardas em `CLAUDE.md`. **Não desfazer sem aprovação.**

## Rodar

```bash
cd /root/rodrigo/raylook
PYTHONPATH=.venv/lib/python3.12/site-packages:. python3 main.py
# ou
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

`data/raylook.db` é criado automaticamente do schema (`deploy/sqlite/schema.sql`)
na primeira execução.

## Configuração

Copie `.env.example` pra `.env`. Os defaults seguros já blindam contra impacto em prod:

| Var | Default | Efeito |
|---|---|---|
| `RAYLOOK_SANDBOX` | `true` | Asaas e Resend viram stub; nada bate em API real |
| `DATA_BACKEND` | `sqlite` | Usa `data/raylook.db`; jamais conecta no Postgres compartilhado |

## Testes

```bash
DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v
```

## Convenções e estrutura

Ver `CLAUDE.md` na raiz.
