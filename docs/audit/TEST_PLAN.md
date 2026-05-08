# Alana Dashboard — Plano de Testes & Execução

> Cenários de teste das Fases 3-5. Cada cenário tem: **objetivo → passos → resultado esperado → resultado observado → achado**.
> **Regra:** todo dado sintético criado aqui deve ter prefixo `[audit]` no título/nome pra limpeza fácil depois.

---

## Setup de testes

**Ambiente:** `staging` apenas. Banco `alana_staging` no VPS.
**Ferramenta de simulação:** `curl` direto em `POST /webhook/whatsapp` ou inserção em `webhook_inbox` + chamada do endpoint.
**Marcador:** todos os artefatos de teste terão `[audit]` no título da enquete ou `audit_` no `event_key`.

**Tabela de limpeza (a executar ao fim):**
```sql
-- Será populada durante os testes
```

---

## Fase 3 — Cenários de voto `[a executar]`

### T3.01 — Voto novo em enquete nova

**Objetivo:** Caminho feliz. Verifica que um voto normal é persistido corretamente.

**Passos:** (a definir ao executar)
1. Criar `enquete_alternativas` manualmente ou deixar o fluxo criar.
2. POST no webhook simulando 1 cliente votando com qty=3.
3. Verificar registros em `enquetes`, `clientes`, `votos`, `votos_eventos`, `webhook_inbox`.

**Resultado esperado:**
- `webhook_inbox.status='processed'`
- `votos`: 1 linha, `status='in'`, `qty=3`
- `votos_eventos`: 1 linha, `action='vote'`
- `pacotes`: 1 linha com `status='open'`, `total_qty=3`, `participants_count=1`

---

### T3.02 — Mesmo cliente muda o voto

**Cenário:** Após T3.01, mesmo cliente manda nova escolha (qty=6).
**Esperado:** `votos` UPSERT, `qty=6`. `votos_eventos` ganha linha adicional. `pacotes.total_qty=6`.

---

### T3.03 — Cancelar voto (qty=0)

**Cenário:** Após T3.02, cliente cancela (qty=0).
**Esperado:** `voto.status='out'`, `qty=0`. `votos_eventos.action='remove'`. `pacotes.total_qty=0`, `participants_count=0`.

---

### T3.04 — Re-votar após cancelar

**Cenário:** Cliente que havia cancelado volta e vota qty=9.
**Esperado:** `voto.status='in'`, `qty=9`. Tudo volta a contar.

---

### T3.05 — Votar em pacote já fechado

**Cenário:** Fazer 8 clientes votarem qty=3 cada (total 24) → fecha pacote 1. Um 9º cliente vota qty=3.
**Esperado:** Pacote 1 permanece imutável. Novo voto entra no pacote `open` (sequence_no=0).

---

### T3.06 — Cliente muda voto depois de entrar em pacote fechado

**Cenário:** Cliente dentro do pacote fechado tenta mudar o voto.
**Esperado (hipótese):** O voto é atualizado mas o pacote fechado **não** é afetado. Precisamos confirmar qual é o comportamento real e se é correto pro negócio.

**⚠️ Este é um cenário crítico — precisa decisão de negócio.**

---

### T3.07 — Concorrência: 2 votos simultâneos fechando pacote

**Cenário:** Fazer 2 webhooks chegarem "ao mesmo tempo" (via paralelismo curl) onde a soma fecha um pacote.
**Esperado:** Apenas 1 pacote é criado, ou erro de duplicate key é tratado graciosamente. **Hipótese (F-004):** vai gerar erro `23505`.

---

### T3.08 — Concorrência: 2 votos simultâneos que excedem 24

**Cenário:** Pacote em 21. Dois clientes votam qty=6 simultaneamente (total 33). Algoritmo subset_sum deve achar 24.
**Esperado:** 1 pacote fechado com subset de 24, sobras voltam pro open.

---

## Fase 4 — Ciclo de vida de pacotes `[a executar]`

### T4.01 — Confirmar pacote (happy path)

**Passos:** Fechar pacote → POST `/api/packages/{id}/confirm`.
**Esperado:**
- `pacote.status='approved'`, `approved_at` setado.
- `vendas`: N linhas (1 por `pacote_cliente`), `status='approved'`.
- `pagamentos`: N linhas, `status='created'`.
- pdf_worker roda em background → `pdf_status='sent'` eventualmente.
- payments_worker roda → `voto.asaas_payment_id` preenchido.

---

### T4.02 — Reverter confirmação

**Passos:** Após T4.01, POST `/api/packages/{id}/revert`.
**Esperado:** `pacote.status='closed'`, `approved_at=NULL`. **Pergunta pendente:** o que acontece com as `vendas`/`pagamentos` já criados? Hoje não há como cancelar venda (F-006).

---

### T4.03 — Rejeitar pacote

**Passos:** Pacote `closed` → POST `/api/packages/{id}/reject`.
**Esperado:** `pacote.status='cancelled'`, `cancelled_at`, `cancelled_by` setados.

---

### T4.04 — Excluir pacote (se existe)

**Passos:** Verificar se há endpoint DELETE. Se sim, testar.
**Hipótese:** Não há endpoint DELETE no código. Deletes observados em F-004 são SQL manual.

---

### T4.05 — Retry pagamentos

**Passos:** Após T4.01, forçar falha no Asaas, depois POST `/api/packages/{id}/retry_payments`.
**Esperado:** payments_worker re-roda só pros que não têm `asaas_payment_id`.

---

### T4.06 — sequence_no correto ao fechar múltiplos pacotes

**Passos:** Fechar 3 pacotes em sequência na mesma enquete.
**Esperado:** `sequence_no=1, 2, 3`, todos UNIQUE, sem gaps indevidos.

---

## Fase 5 — Performance `[a executar]`

### T5.01 — Tempo de resposta `/` (dashboard home)

```bash
for i in 1 2 3 4 5; do
  curl -o /dev/null -s -w "%{time_total}s\n" https://staging-alana.v4smc.com/
done
```

**Esperado:** < 1s consistente. Se > 2s → investigar.

---

### T5.02 — `/api/metrics` response time

---

### T5.03 — `/api/finance/charges` com muitas linhas

---

### T5.04 — Query mais pesada do dash (identificar via logs)

---

### T5.05 — Benchmark com e sem os 7 índices faltando (F-011)

Criar índices → medir → rollback (ou manter se OK).

---

## Fase 6 — Race conditions (análise + prova)

Cobertas parcialmente pela evidência real em F-004. A fase 6 vai:

1. Reproduzir cada race em ambiente controlado.
2. Propor fix específico.
3. Validar fix não quebra o caminho feliz.

---

## Registro de execução

Cada teste, quando executado, ganha uma entrada aqui:

```
## [YYYY-MM-DD] T3.XX — Nome
- **Executado por:** Claude / [operador]
- **Comando/query:** ...
- **Resultado observado:** ...
- **Status:** ✅ passou / ❌ falhou / ⚠️ parcial
- **Findings gerados:** F-XXX
- **Limpeza:** SQL executado pra remover artefatos
```
