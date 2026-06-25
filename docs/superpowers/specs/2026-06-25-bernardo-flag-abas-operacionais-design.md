# Bernardo: badge de flag + abas operacionais (Estoque / Logística / Financeiro)

**Data:** 2026-06-25
**Branch:** feat/bernardo-abas-comercial (continuação)

## Contexto

A sessão Bernardo já segrega enquetes por título contendo "Bernardo"
(`app/sessions.py::session_for_title`). Pacotes Bernardo que chegam em **pago**
já aparecem no Estoque/Logística (esses grupos usam `session: "all"`), mas:

1. Não há **nenhuma marca visual** que identifique a origem Bernardo de um pacote
   quando ele aparece no Estoque/Logística — fica indistinguível de um comercial.
2. O usuário `bernardo` só enxerga o grupo **Bernardo**. Ele não tem acesso às
   abas **Estoque**, **Logística** e **Financeiro** pra acompanhar seus pacotes
   ao longo do fluxo operacional e financeiro.

## Objetivo

1. Exibir um **badge "Bernardo"** nos cards (lista + detalhe) sempre que
   `p.session === "Bernardo"`.
2. Liberar as abas **Estoque**, **Logística** e **Financeiro** pro usuário
   `bernardo`, **filtradas só pros itens com flag Bernardo**.

## Não-objetivos (YAGNI)

- Reescrever `/api/dashboard/packages` pra filtrar pacotes por role no backend.
  Pacotes seguem filtrados **no front** (mesmo padrão do grupo Bernardo atual).
- Segregar a view **Créditos** por Bernardo — saldo é por cliente, não amarrado a
  enquete/pacote. Créditos fica **oculto** pro usuário bernardo.
- Dar ao bernardo as abas Enquetes/Clientes. Continuam fora do escopo dele.

## Design

### Backend

**1. `app/services/auth_service.py::visible_groups`**

```python
if role == "bernardo":
    return ("bernardo", "estoque", "logistica", "financeiro")
```

(antes: `("bernardo",)`)

**2. Filtro de sessão nos builders de finance** (`app/services/finance_service.py`)

`build_receivables_by_client`, `build_aging_summary`, `build_paid_by_client`,
`build_paid_summary` ganham param opcional `session: str | None = None`.

Quando `session == "Bernardo"`, descartam as linhas/cobranças cujo título de
enquete **não** casa com a sessão Bernardo. A decisão reusa
`app.sessions.session_for_title(titulo)` pra manter a regra de match única
(substring case-insensitive "Bernardo"):

```python
from app.sessions import session_for_title

def _matches_session(titulo: str, session: str | None) -> bool:
    if not session:
        return True
    s = session_for_title(titulo or "")
    return bool(s) and s.get("name") == session
```

Aplicado por linha onde `enquete_titulo` já é resolvido (receivables/paid) e na
agregação de pendentes do aging-summary (resolver título igual receivables).

**3. Endpoints forçam a sessão pelo role** (`app/routers/finance.py`)

Os endpoints `/receivables`, `/aging-summary`, `/paid`, `/paid-summary` passam a
receber `request: Request` e derivam a sessão efetiva:

```python
def _session_for_request(request: Request) -> str | None:
    return "Bernardo" if getattr(request.state, "role", None) == "bernardo" else None
```

A sessão é **forçada pelo role**, não vem de query param do cliente — assim o
usuário bernardo não consegue ver dados comerciais batendo na API direto.
`/credits` fica intocado (oculto na UI pro bernardo).

### Frontend

**4. Badge "Bernardo"** (`static/js/dashboard_v2.js` + CSS em `dashboard_v2.html`)

Pill pequeno renderizado quando `p.session === "Bernardo"`:
- **Lista:** dentro de `.pkg-row-main`, junto ao nome.
- **Detalhe:** no header, perto do `.subtitle`.

Classe `.badge-bernardo` com cor distinta (reusa o padrão visual de chip/pill já
existente). Aparece naturalmente só onde há pacote Bernardo (grupo Bernardo,
Estoque, Logística — no Comercial os Bernardo são filtrados fora).

**5. `session` de Estoque/Logística dependente do role**

No `RAIL_GROUPS`, Estoque e Logística usam uma sessão calculada:

```js
const opSession = currentRole === "bernardo" ? "bernardo" : "all";
```

`itemsFor(session, state)` já trata `"bernardo"` (filtra `p.session === "Bernardo"`)
e `"all"` (sem filtro). Pra admin/estoque/logística nada muda; pro bernardo, as
abas operacionais passam a mostrar só pacotes Bernardo.

**6. `groupOpen` e abertura inicial**

`groupOpen` ganha as chaves novas conforme visibilidade. O usuário bernardo
continua abrindo no grupo **Bernardo** (primeiro visível dele).

**7. Esconder Créditos pro bernardo** (`static/js/finance.js` ou
`finance-toggle.js`)

O rail-step `[data-fin-view="credits"]` é ocultado quando
`window.currentRole === "bernardo"`. As demais views (A receber / Pagos) já vêm
filtradas do backend.

**8. Cache-bust:** bump `?v=` de `dashboard_v2.js` e `finance.js` (se alterado) em
`templates/dashboard_v2.html`.

## Testes

- `tests/unit/test_auth_service.py` (ou equivalente): `visible_groups("bernardo")`
  contém `estoque`, `logistica`, `financeiro` (e **não** `enquetes`/`clientes`).
- Finance builders com `session="Bernardo"` filtram só linhas com título Bernardo;
  com `session=None` retornam tudo (golden path + edge: título sem match).
- Endpoint de finance força `session="Bernardo"` quando `request.state.role ==
  "bernardo"` (e `None` pros demais roles).

## Validação no browser

1. Login admin → Estoque/Logística mostram badge "Bernardo" nos pacotes de origem
   Bernardo; Comercial sem badge.
2. Login bernardo → vê grupos Bernardo, Estoque, Logística, Financeiro. Estoque/
   Logística só com pacotes Bernardo. Financeiro só com cobranças Bernardo,
   **sem** a view Créditos.
3. Admin no Financeiro continua vendo tudo, com Créditos.

## Riscos / rollback

- Mudança de `visible_groups` é aditiva (só amplia o que bernardo vê).
- Filtro de finance é opcional (`session=None` = comportamento atual) → admin
  intocado.
- Frontend: badge é puramente aditivo; sessão role-dependente afeta só o role
  bernardo. Rollback = reverter os commits da branch.
