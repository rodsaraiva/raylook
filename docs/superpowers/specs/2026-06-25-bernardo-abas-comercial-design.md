# Spec — Sessão Bernardo com o layout/abas da Comercial (segregação por título)

Data: 2026-06-25
Branch: `feat/bernardo-abas-comercial`
Fase anterior: sessão Bernardo entregue como **painel de acúmulo** (PR #13 standalone, PR #14 integrada ao `/`), já em produção.

## Objetivo

Trocar a apresentação da sessão **Bernardo** no dashboard `/`: de painel de cards de
acúmulo para o **mesmo layout da Comercial**, com as mesmas abas/estados —
**Aberto, Fechado, Aguardando Pagamento, Pago e Cancelados**.

A separação entre Comercial e Bernardo passa a ser pelo **título da enquete**: se o
título contém a string **"Bernardo"** (config em `app/sessions.py`), os pacotes
daquela enquete aparecem **apenas na sessão Bernardo** e **não na Comercial**.

## Não-objetivos

- **Não alterar a Comercial** além de esconder os pacotes de título "Bernardo". Abas,
  fluxo, fechamento automático em 24, detalhe e tudo mais ficam **idênticos**.
- **Não alterar a lógica de fechamento/acúmulo**: enquetes Bernardo continuam
  acumulando votos sem limite de 24 e fechando manualmente via "Fechar pacote"
  (`whatsapp_domain_service.py` e o endpoint de close ficam como estão).
- Não mexer em Estoque/Logística: continuam mostrando **todos** os pacotes (inclusive
  Bernardo) — são estados operacionais (separação/envio).
- Não criar migration de banco.

## Decisões do brainstorming

- Fechamento: **mantém o acúmulo + botão "Fechar pacote"** (não vira fechamento em 24).
- Estoque/Logística: **mostram tudo** (sem filtro por sessão).
- Comercial: **inalterada**, só deixa de exibir os pacotes de título "Bernardo".

## Arquitetura atual relevante

- **`GET /api/dashboard/packages`** (`app/routers/dashboard.py`): devolve
  `counts` (por estado), `packages_by_state` (estado → lista) e `cancelled`. Cada
  item de pacote já inclui `enquete_title` e `enquete_id`.
- **`app/sessions.py`**: `session_for_title(titulo)` devolve a sessão (`{"name":
  "Bernardo", ...}`) por substring case-insensitive, ou `None`.
- **Front (`static/js/dashboard_v2.js`)**: navega por `activeState`; `currentItems()`
  retorna `data.packages_by_state[activeState]` (ou `data.cancelled`); o rail usa
  `data.counts[state]`. `RAIL_GROUPS` define os grupos; Comercial =
  `states: ["aberto","fechado","confirmado","pago"], extras: ["cancelled"]`.
  Bernardo hoje é `{ id: "bernardo", panel: true }` (abre o painel `#section-bernardo`).
- **Painel atual a remover**: `#section-bernardo`, `static/js/bernardo_section.js`,
  o ramo `panel` no `renderRail`, o listener `[data-panel]`, os globais
  `window._bernardo*` e as chamadas de cross-close em `enquetes.js`/`finance-toggle.js`/
  `clientes.js`. A página standalone `/bernardo` + `bernardo_cards.js` **ficam**.

## Design

### 1. Backend — taggear a sessão por pacote (`dashboard.py`)

Em `list_packages_by_state`, cada item (pacote e cancelado) ganha:

```python
"session": (session_for_title(enq.get("titulo")) or {}).get("name"),  # "Bernardo" ou None
```

Fonte única da regra continua em `app/sessions.py`. `counts` e `packages_by_state`
seguem com o mesmo shape (todos os pacotes); o split por sessão é feito no front.
Nenhuma mudança na lógica de fechamento.

### 2. Frontend — dimensão de sessão (`dashboard_v2.js`)

- **Bernardo vira grupo de estados** no `RAIL_GROUPS`, espelhando a Comercial:
  `{ id: "bernardo", label: "Bernardo", states: ["aberto","fechado","confirmado","pago"], extras: ["cancelled"] }`.
  Comercial permanece igual.
- Novo estado `activeSession` (`"comercial"` | `"bernardo"` | `"all"`). Cada
  `rail-step`/grupo carrega a sessão de origem:
  - grupo Comercial → `"comercial"`; grupo Bernardo → `"bernardo"`;
    Estoque/Logística → `"all"`.
- **Filtro central** — `currentItems()` passa a filtrar por sessão além do estado:
  - `activeSession === "comercial"` → itens com `session !== "Bernardo"`
  - `activeSession === "bernardo"` → itens com `session === "Bernardo"`
  - `activeSession === "all"` (estoque/logística) → sem filtro de sessão
- **Contagens** — no rail, as contagens por estado passam a ser calculadas **por
  sessão** a partir de `packages_by_state`/`cancelled` filtrados:
  - Comercial mostra contagens dos não-Bernardo; Bernardo mostra as dos Bernardo;
    Estoque/Logística seguem com a contagem total (sem filtro).
- Clicar num estado de um grupo define `activeSession` (do grupo) + `activeState`.
  O resto da renderização (lista, detalhe, paginação, busca) é reusado sem mudança
  estrutural.
- **Estado inicial**: `activeSession` default = sessão do **primeiro grupo visível**
  pro papel (admin → `"comercial"`; usuário `bernardo` → `"bernardo"`), alinhado com
  o grupo que já abre por padrão hoje (`groupOpen`).

### 3. Botão "Fechar pacote" (detalhe)

No painel de detalhe (`renderDetail`), quando o pacote selecionado for
**`session === "Bernardo"` e estado `aberto`**, renderizar um botão **"Fechar
pacote"** → `POST /api/bernardo/sessions/Bernardo/close` com `{enquete_id}`. Em
`status === "ok"`, recarrega os dados. Pacote Comercial aberto **não** tem o botão
(fecha sozinho em 24). Erros mapeados como hoje (`no_votes`, `rpc_error`, etc.).

### 4. Remoção do painel antigo

- Excluir `#section-bernardo` (markup + CSS `.bn-*` escopado), `bernardo_section.js`
  e seu `<script>`.
- Remover o ramo `panel` do `renderRail`, o listener `[data-panel]`, o seletor
  `:not([data-panel])` (volta a `.rail-group-header` simples) e os globais
  `window._bernardoOpen/_bernardoClose/_bernardoToggle`.
- Remover as chamadas `window._bernardoClose?.()` de `enquetes.js`,
  `finance-toggle.js`, `clientes.js`.
- Bump do cache-buster do `dashboard_v2.js` (convenção `?v=`).

### 5. Inalterado

- `auth_service.py` (papel `bernardo`, `visible_groups`) — o usuário `bernardo`
  segue vendo só o grupo Bernardo, que agora é o grupo de estados.
- Guard `/api/bernardo/*` (admin+bernardo), endpoint de close, acúmulo no
  `whatsapp_domain_service.py`, página standalone `/bernardo`.
- Comercial: só passa a esconder os pacotes Bernardo; nada mais muda.

## Testes

- **Backend** (`dashboard.py`): item de pacote ganha `session` correto — título com
  "Bernardo" → `"Bernardo"`; sem → `None`. Cobrir cancelado também.
- **Browser** (porta scratch + SQLite, dados semeados):
  - Admin: grupo **Bernardo** com as 5 abas; pacote Bernardo aparece **só** em
    Bernardo; pacote comum aparece **só** em Comercial; contagens batem por sessão.
  - Pacote Bernardo **Aberto** mostra "Fechar pacote" no detalhe → fecha → some do
    Aberto, aparece em Fechado.
  - **Comercial**: continua com as mesmas abas e o mesmo comportamento, sem os
    Bernardo.
  - **Estoque/Logística**: continuam mostrando todos os pacotes (inclui Bernardo).
  - Usuário `bernardo`: vê só o grupo Bernardo (com as abas) e mais nada.

## Riscos / observações

- Toca o dashboard principal (`dashboard.py`, `dashboard_v2.js`, `dashboard_v2.html`)
  — intencional. Sem migration.
- A Comercial passa a depender do `session` taggeado; se o backend não taggear, o
  filtro do front degrada para "mostra tudo" (não esconde Bernardo) — cobrir no teste.
- Estados compartilhados (ex.: `pago` aparece em Comercial e Estoque): o filtro de
  sessão é por **grupo clicado** (a sessão do rail-step), não pelo estado em si —
  então o mesmo pacote pago Bernardo aparece na aba Pago do **Bernardo** e na Fila de
  separação do **Estoque** (operacional), como esperado.
