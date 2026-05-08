# ADR-001 — Migração de CI/CD: SSH Deploy → Build + Push GHCR

**Data:** 2026-03-04
**Status:** ✅ Aceito
**Autores:** @aios-master (Orion), KssyanuX
**Contexto:** Story 1.1

---

## Contexto

O pipeline original em `.github/workflows/deploy.yml` tinha dois jobs:

1. `test` — rodava pytest com gate **falso** (`|| echo "Tests completed with warnings"`)
2. `deploy` — fazia SSH na VPS e rodava `docker pull + docker service update`

### Problema identificado

O `|| echo` tornava o step de testes sempre exitcode 0, fazendo o CI reportar `success`
mesmo com 24 testes falhando. Isso permitiu que um commit com regressões chegasse à produção
sem qualquer bloqueio.

Além disso, o modelo de deploy via SSH acoplava o CI/CD com credenciais da VPS e tornava
o processo frágil — qualquer mudança na VPS quebrava o pipeline.

---

## Decisão

Separar responsabilidades:

- **CI/CD (GitHub Actions):** apenas validar qualidade (testes) e publicar artefato (imagem Docker no GHCR)
- **Deploy (Portainer):** responsabilidade do operador — update manual da stack puxando a nova imagem

### Novo pipeline

```
push → main
  ├── Job: test
  │     - pytest tests/ -v --tb=short --timeout=30  (sem || echo)
  │     - Falha = bloqueia job build
  │
  └── Job: build (needs: test)
        - Login no GHCR (GITHUB_TOKEN automático)
        - docker/build-push-action@v5
        - Tags: latest + sha-{hash}
        - Push para ghcr.io/V4MarcosPaulo/alana-dashboard
```

### Fluxo de deploy

```
GitHub Actions (push) → GHCR (nova imagem)
                              ↓
                    Portainer → Update Stack → puxar :latest
```

---

## Consequências

### Positivas

- Gate de qualidade real — nenhum deploy com testes quebrando
- Sem credenciais SSH no CI — reduz superfície de ataque
- Imagem versionada por SHA — rollback trivial no Portainer
- Deploy desacoplado do CI — Portainer controla o momento do update
- `GITHUB_TOKEN` automático — sem configuração adicional de secrets

### Negativas / Trade-offs

- Deploy deixa de ser automático — requer ação manual no Portainer após cada push
- Se esquecer de atualizar o Portainer, produção fica desatualizada

### Mitigação do trade-off

Considerar no futuro (Story 3.1) um webhook do GHCR para notificar no WhatsApp quando
nova imagem estiver disponível, sinalizando que o Portainer precisa ser atualizado.

---

## Alternativas consideradas

| Alternativa | Por que descartada |
|------------|-------------------|
| Manter SSH deploy + corrigir só o gate | Acoplamento CI↔VPS, credenciais no CI, frágil |
| Deploy automático via Portainer webhook | Complexity — Portainer webhook requer configuração adicional |
| ArgoCD / Flux CD | Overkill para infraestrutura atual (single VPS + Swarm simples) |
