# Alana Dashboard — Audit & System Knowledge Base

> Material incremental sobre o sistema Alana (`staging-alana.v4smc.com`).
> Qualquer chat/pessoa que entrar no projeto deve começar lendo este README.

**Última atualização:** 2026-04-08
**Ambiente coberto:** `staging` (banco `alana_staging`, serviço Swarm `alana-staging_alana-dashboard`)
**Produção:** `alana.v4smc.com` → **NÃO É TOCADO POR ESSE AUDIT**

---

## 📚 Arquivos

| Arquivo | Conteúdo | Quando atualizar |
|---|---|---|
| [`SYSTEM.md`](./SYSTEM.md) | Verdade atual de como o sistema funciona: arquitetura, fluxos, invariantes, máquina de estados, dependências. | Sempre que descobrir algo novo sobre o funcionamento. |
| [`FINDINGS.md`](./FINDINGS.md) | Achados da auditoria por severidade (SEV-1/2/3/4). Cada achado tem: hipótese, evidência, impacto, fix sugerido, status. | Adicionar novos achados; atualizar status quando fix for aplicado. |
| [`TEST_PLAN.md`](./TEST_PLAN.md) | Cenários de teste executados (Fase 3+), resultados, queries usadas. | Atualizar ao rodar testes. |
| [`GLOSSARY.md`](./GLOSSARY.md) | Termos de domínio (enquete, pacote, voto, alternativa, etc.) com definição curta. | Quando termo novo aparecer. |

## 🧭 Regras de escrita desses docs

1. **Fato verificado vs hipótese**: sempre marque com `[verificado]`, `[hipótese]`, `[a verificar]`.
2. **Citar fontes**: toda afirmação sobre código cita `arquivo:linha`. Toda afirmação sobre dados cita a query SQL usada.
3. **Datas absolutas**: nunca "ontem" / "semana passada". Usar ISO-8601.
4. **Incremental, não destrutivo**: não apague seções — marque como `~~desatualizado~~` e adicione a versão nova.
5. **Severidade imutável**: SEV-1 crítico (perda de dados, transação quebrada), SEV-2 alto (inconsistência visível), SEV-3 médio (code smell, risco lógico), SEV-4 baixo (higiene).

## 🎯 Estado do audit

- ✅ Fase 1 — Mapeamento arquitetural (SYSTEM.md v1)
- ✅ Fase 2 — Auditoria de integridade read-only (FINDINGS.md v1)
- ⏳ Fase 3 — Teste de cenários de voto (aguardando destravamento git para começar)
- ⏸️ Fase 4 — Ciclo de vida de pacotes
- ⏸️ Fase 5 — Performance do dash
- ⏸️ Fase 6 — Race conditions / bugs lógicos (parcialmente coberto em FINDINGS.md)

## 🚫 Bloqueios atuais

1. **Git**: `/root/alana-staging-supabase/` não é repo git — sem `gh` CLI, sem SSH key privada, sem `~/.gitconfig`. Preciso do repo GitHub (URL + credenciais) pra commitar/pushar a branch ativa.
2. **Fluxo de deploy**: confirmar se é build local no VPS + `docker service update`, ou push GitHub → CI → pull. Existem 60+ imagens `alana-dashboard:staging-*` locais acumuladas, sugerindo build manual.
