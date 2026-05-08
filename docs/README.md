# Documentação — Projeto Alana Dashboard

Repositório de documentação técnica, stories, backlog e decisões arquiteturais do Projeto Alana.

## Estrutura

```
docs/
├── README.md                    — Este índice
├── stories/                     — User stories executadas (formato AIOS)
│   └── 1.1.story.md            — Sprint 1 — Estabilização CI/CD e Testes
├── backlog/
│   └── backlog.md              — Product backlog priorizado
├── adr/
│   └── ADR-001-cicd-ghcr.md   — Decisão: migração de deploy para GHCR
└── sprint-reviews/
    └── sprint-1-review.md      — Review da Sprint 1 (2026-03-04)
```

## Contexto do Projeto

**Alana Dashboard** é uma aplicação FastAPI que monitora votos de enquetes via Baserow,
agrega métricas, gerencia pacotes de votos e dispara notificações WhatsApp + pagamentos PIX.

- **Produção:** https://alana.v4smc.com
- **Stack:** FastAPI + Docker Swarm + Traefik + Portainer
- **CI/CD:** GitHub Actions → GHCR → deploy manual via Portainer

## Observação de Processo

> A Sprint 1 foi executada integralmente pelo agente `@aios-master` (Orion) sem orquestração
> de sub-agentes especializados. O workflow correto (Story Development Cycle) está documentado
> em `docs/adr/ADR-001-cicd-ghcr.md` e deve ser adotado nas próximas sprints.
> Ver `docs/sprint-reviews/sprint-1-review.md` para análise completa do desvio de processo.
