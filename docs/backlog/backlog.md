# Product Backlog — Alana Dashboard

**Última atualização:** 2026-03-04
**Owner:** @pm (Morgan)

---

## Épico 1 — Estabilização e Qualidade

| ID | Story | Prioridade | Status | Sprint |
|----|-------|-----------|--------|--------|
| 1.1 | Estabilização CI/CD e correção de testes pós-commit Rodrigo | 🔴 Crítico | ✅ Done | Sprint 1 |
| 1.2 | Refatorar código duplicado em main.py (dois caminhos de refresh) | 🟡 Médio | 📋 Backlog | — |
| 1.3 | Implementar feature branch workflow (parar commits diretos na main) | 🟡 Médio | 📋 Backlog | — |
| 1.4 | Configurar CODEOWNERS e branch protection rules no GitHub | 🟡 Médio | 📋 Backlog | — |

---

## Épico 2 — Workflow de Desenvolvimento

| ID | Story | Prioridade | Status | Sprint |
|----|-------|-----------|--------|--------|
| 2.1 | Estabelecer Story Development Cycle (SDC) com agentes AIOS | 🔴 Alto | 📋 Backlog | — |
| 2.2 | Configurar agente @devops para gestão exclusiva de push/PR | 🟡 Médio | 📋 Backlog | — |
| 2.3 | Criar template de PR com checklist de qualidade | 🟢 Baixo | 📋 Backlog | — |

---

## Épico 3 — Observabilidade e Monitoring

| ID | Story | Prioridade | Status | Sprint |
|----|-------|-----------|--------|--------|
| 3.1 | Adicionar alertas de falha de deploy no WhatsApp/Slack | 🟡 Médio | 📋 Backlog | — |
| 3.2 | Dashboard de health das integrações (Baserow, Asaas, Evolution) | 🟢 Baixo | 📋 Backlog | — |
| 3.3 | Implementar structured logging com nível DEBUG/INFO/ERROR | 🟢 Baixo | 📋 Backlog | — |

---

## Épico 4 — Funcionalidades de Negócio

| ID | Story | Prioridade | Status | Sprint |
|----|-------|-----------|--------|--------|
| 4.1 | Relatório semanal automático de pacotes via WhatsApp | 🟡 Médio | 📋 Backlog | — |
| 4.2 | Tela de histórico de pacotes confirmados por período | 🟢 Baixo | 📋 Backlog | — |

---

## Dívidas Técnicas Identificadas

| ID | Descrição | Risco | Origem |
|----|-----------|-------|--------|
| DT-01 | Dois caminhos de refresh em `main.py` e `app/services/metrics_service.py` com lógica duplicada | 🟡 Médio | Identificado na análise do commit 6c6f1bb |
| DT-02 | Commits diretos na main sem code review | 🔴 Alto | Causa raiz do incidente desta sprint |
| DT-03 | `asyncio.Lock()` com lazy-init global — vulnerável a race condition em inicialização | 🟡 Médio | Identificado no fix do teste de concorrência |
| DT-04 | `on_event` deprecated no FastAPI — deve migrar para `lifespan` handler | 🟢 Baixo | Warnings nos testes |

---

## Legenda

- 🔴 Crítico/Alto — Bloqueia operação ou gera risco de produção
- 🟡 Médio — Impacta qualidade ou velocidade de desenvolvimento
- 🟢 Baixo — Melhoria incremental
- ✅ Done | 🔄 In Progress | 📋 Backlog | ⏸ On Hold
