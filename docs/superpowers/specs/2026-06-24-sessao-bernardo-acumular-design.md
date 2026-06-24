# Spec — Sessão "Bernardo" (fechamento por acúmulo)

**Data:** 2026-06-24
**Branch base:** `feat/etiqueta-termica-qr` (trabalho parte daqui)
**Status:** aprovado para implementação

## Problema

Hoje todo fechamento de pacote no raylook é automático e fixo: a cada voto,
`PackageService.rebuild_for_poll()` roda `_subset_sum(votes, 24)` e fecha pacotes
de **exatamente 24 peças** (`app/services/whatsapp_domain_service.py:440-692`).

Queremos uma **sessão "Bernardo"** com lógica diferente: os votos das enquetes
dessa sessão **acumulam indefinidamente** (sem alvo de 24) até que um operador
aperte um botão **"fechar pacote"**, que congela todos os votos pendentes naquele
momento num pacote `closed`. Votos posteriores entram num novo acúmulo.

"Sessão" aqui = **uma aba** no dashboard que agrupa as enquetes cujo `titulo`
contém uma string configurável (default `"Bernardo"`).

## Requisito de não-regressão (crítico)

**Tudo que existe hoje deve permanecer inalterado e funcional.** Enquetes que
**não** casam com nenhuma sessão de modo `accumulate` seguem usando exatamente o
fluxo atual de subset-sum 24. A ramificação é cirúrgica e centralizada para que
nenhum caminho legado mude de comportamento.

## Decisões (do brainstorming)

1. **Granularidade:** um pacote **por enquete**. Uma enquete pode gerar vários
   pacotes ao longo do tempo (fecha um snapshot, reabre acúmulo, fecha de novo…).
2. **Config:** em código — lista de sessões `{name, match, mode}`. Adicionar
   outra aba depois = 1 linha. Sem tabela nova, sem CRUD.
3. **Botão:** **um por enquete** (fecha o pacote aberto/acúmulo daquela enquete).
4. **Voto alterado após snapshot:** subtração por cliente. Se o cliente aumenta a
   qty depois do snapshot (6→9), o delta (+3) entra no **próximo** pacote; se
   diminui (9→3), o snapshot fica congelado e nada novo entra. Consistente com a
   lógica que o código já usa hoje para pacotes `approved`.

## Arquitetura

### 1. Config da sessão — `app/sessions.py` (novo)

```python
SESSIONS = [
    {"name": "Bernardo", "match": "Bernardo", "mode": "accumulate"},
]

def session_for_title(titulo: str | None) -> dict | None:
    """Retorna a sessão cujo `match` é substring (case-insensitive) do titulo,
    ou None. Match é feito sobre o titulo da enquete."""
```

- Lido pelo **backend** (no ingest/rebuild, pra decidir o modo) e exposto pro
  **frontend** (pra renderizar a aba e filtrar enquetes).
- `mode == "accumulate"` é o único modo especial por ora; ausência de match =
  comportamento legado.

### 2. Backend — ramo de fechamento (`PackageService`)

- **Dispatch centralizado:** no **topo** de `rebuild_for_poll(enquete_id)`,
  após carregar a enquete (incluir `titulo` no select), se
  `session_for_title(titulo)` tem `mode == "accumulate"`, delega para
  `_rebuild_accumulate(...)` e retorna. Como **todos** os callers passam por
  `rebuild_for_poll`, nenhum pacote `closed` da Bernardo é deletado/recriado por
  um rebuild legado.

- **`_rebuild_accumulate(enquete_id, ...)`:**
  - Calcula os **votos pendentes** = votos ativos (`status != 'out'`, `qty > 0`)
    **menos** a qty já consumida por cliente em pacotes `closed`/`approved` da
    enquete (subtração por cliente — mesma técnica já aplicada hoje só para
    `approved`, aqui estendida para `closed`+`approved`).
  - Mantém **um único** pacote `open` (summary: `total_qty`, `participants_count`,
    `capacidade_total = total_qty`, `sequence_no = 0`) com esses pendentes.
    Não cria `pacote_clientes` no open (igual ao open de hoje). Se não há
    pendente com `qty > 0`, remove o open.
  - **Nunca** roda subset-sum, **nunca** fecha sozinho, **nunca** toca em
    pacotes `closed`/`approved`/`cancelled`.

- **`close_accumulated(enquete_id) -> dict`:**
  - Recalcula os pendentes (mesma função do rebuild).
  - Se vazio → retorna `{"status": "no_votes"}`.
  - Monta `votes_payload` (unit_price, subtotal, commission, total — mesma
    aritmética do rebuild legado) e chama a RPC `close_package` com
    `p_total_qty = soma real`, `p_capacidade_total = soma real`, todos os
    pendentes. A RPC já é parametrizável (não força 24) e roda atômica com
    `pg_advisory_xact_lock` (Postgres) / `BEGIN IMMEDIATE` (SQLite).
  - Reaproveita `assign_friendly_id` e a propagação de `fornecedor` da enquete
    (mesmo trecho do rebuild legado).
  - Após o close, os votos viram consumidos pelo pacote `closed`; o próximo voto
    dispara `_rebuild_accumulate`, que reabre o acúmulo com `total_qty = 0`
    (ou seja, o open some até chegar voto novo).

### 3. Endpoint (admin)

`POST /api/dashboard/sessions/{session_name}/close`
- Body: `{"enquete_id": "<id>"}`.
- Valida que a enquete existe e que seu `titulo` casa com a sessão `{session_name}`
  em modo `accumulate` (defesa: não fecha por essa rota enquetes legadas).
- Chama `PackageService.close_accumulated(enquete_id)`.
- Respostas: `{"status":"ok", "pacote_id":..., "total_qty":..., "participants":...}`
  ou `{"status":"no_votes"}` (HTTP 200, frontend trata).
- Auth: mesma proteção dos demais endpoints `/api/dashboard/*`.

### 4. Frontend — aba "Bernardo"

- Nova aba no dashboard admin, **dirigida pela config de sessões** (renderiza uma
  aba por sessão `accumulate`; hoje só "Bernardo").
- A aba lista as **enquetes** cujo titulo casa com a sessão, e para cada uma:
  - o acúmulo ao vivo: `total_qty` + nº de participantes + lista
    cliente/qty (calculados no read a partir dos votos pendentes — o open é só
    summary, então o endpoint de leitura computa os pendentes).
  - botão **"fechar pacote"** (desabilitado se acúmulo == 0). Ao clicar →
    `POST .../close` → recarrega a aba.
- Pacotes Bernardo já `closed` **continuam aparecendo e fluindo nas seções
  normais** (fechado → confirmado → pago → … → enviado), sem nenhuma alteração.
- Endpoint de leitura da aba: reaproveita/estende o de packages, ou um
  `GET /api/dashboard/sessions/{session_name}` que devolve as enquetes da sessão
  + acúmulo corrente. (Decidir no plano; preferir reuso.)

### 5. Downstream inalterado

Um pacote Bernardo `closed` é um pacote normal: gerente aprova → vira `approved`
→ cobrança PIX Asaas → pago → separado → enviado. Idêntico ao fluxo atual.

### 6. Schema

**Nenhuma migration.** O modo é derivado do `titulo` da enquete + a config em
código. Banco isolado (`raylook_*`) intocado. Zero risco de regressão no schema.

## Edge cases

| Caso | Comportamento |
|------|---------------|
| Acúmulo vazio | Botão desabilitado; endpoint retorna `no_votes`. |
| Cliente aumenta voto após snapshot (6→9) | Delta (+3) entra no próximo pacote (subtração por cliente). |
| Cliente diminui voto após snapshot (9→3) | Snapshot congelado; `remaining = max(3-6,0)=0`, nada novo. |
| Concorrência (dois closes) | `close_package` serializa por advisory lock; segundo retorna pacote vazio/`no_votes`. |
| Enquete Bernardo nunca atinge "24" | Nunca fecha sozinha — só pelo botão. |
| Rota de close usada em enquete legada | Rejeitada (validação de sessão no endpoint). |
| Backfill/resync chamando rebuild | Passa pelo dispatch central → usa `_rebuild_accumulate`, não deleta `closed`. |

## Testes (SQLite real, sem mock de DB)

1. `session_for_title`: match case-insensitive, substring, None quando não casa.
2. `_rebuild_accumulate`: votos acumulam num único open sem fechar em 24; open
   reflete soma total; múltiplos votos > 24 não disparam fechamento.
3. `close_accumulated`: fecha pacote com a soma corrente (≠ 24), cria
   `pacote_clientes` certos, marca votos, atribui friendly_id; acúmulo zera.
4. Reabertura: voto novo após close entra em pacote novo (sequence_no+1).
5. Subtração por cliente: cliente que aumentou qty após snapshot → delta no
   próximo pacote.
6. Não-regressão: enquete **sem** match segue fechando em 24 exatamente como hoje.
7. Endpoint: `POST .../close` happy path + `no_votes` + rejeição de enquete
   legada.

## Critérios de aceite

- [ ] Enquetes Bernardo acumulam sem fechar em 24.
- [ ] Botão "fechar pacote" cria pacote `closed` com todos os votos do momento.
- [ ] Votos posteriores formam novo acúmulo / novo pacote.
- [ ] Pacote Bernardo `closed` flui no pipeline normal sem mudanças.
- [ ] Enquetes não-Bernardo: comportamento idêntico ao atual (testes legados
      passam).
- [ ] Sem migration; banco intocado.
- [ ] Testes 1-7 passando.
