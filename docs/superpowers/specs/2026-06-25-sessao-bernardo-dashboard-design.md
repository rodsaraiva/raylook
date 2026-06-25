# Spec — Sessão Bernardo no dashboard `/` + usuário `bernardo`

Data: 2026-06-25
Branch: `feat/sessao-bernardo-dashboard`
Fase anterior: `/bernardo` standalone já em produção (PR #13).

## Objetivo

Expor a sessão **Bernardo** (acúmulo de votos + "fechar pacote") também dentro do
dashboard principal `/`, logo abaixo da sessão **Comercial**. A sessão só pode
aparecer para o usuário **admin** e para um novo usuário **bernardo** (senha
inicial `Bernard0`). O usuário `bernardo` enxerga **somente** a sessão Bernardo —
nada do resto do dashboard.

A página standalone `/bernardo` e os endpoints `/api/bernardo/*` já existem e
permanecem inalterados; esta fase reusa a API e adiciona a integração de UI + o
novo papel.

## Não-objetivos

- Não alterar a lógica de acúmulo/fechamento (`whatsapp_domain_service.py`,
  `app/routers/bernardo.py` business logic) — já validada e em prod.
- Não criar tela de cadastro/registro de usuários: papéis seguem fixos via env,
  como `admin`/`estoque`/`logistica` hoje.
- Não mexer no fluxo de pacotes normais (estados, RBAC de avanço).

## Arquitetura atual relevante

- **Auth** (`app/services/auth_service.py`): papéis fixos `ROLES = (admin, estoque,
  logistica)`. Senha = bcrypt contra `RAYLOOK_USER_<ROLE>_HASH` (env). Cookie
  HMAC-SHA256 carrega o role. `visible_groups(role)` devolve os ids de blocos da
  sidebar que o role enxerga.
- **Login** (`main.py`): `POST /login` valida `username ∈ ROLES` + senha; seta
  cookie. `GET /api/me` devolve `{username, role, visible_groups}`.
- **Sidebar do `/`** (`templates/dashboard_v2.html` + `static/js/dashboard_v2.js`):
  - `#rail` (aside) é preenchido por JS a partir de `RAIL_GROUPS`
    (comercial → estoque → logistica), filtrado por `visibleGroups`.
  - Blocos estáticos no HTML após o rail: `#enquetes-group`, `#fin-group`,
    `#clientes-group`. Hoje **só `#fin-group` é escondido** quando o role não tem
    `financeiro`; Enquetes e Clientes aparecem sempre, para qualquer role.
  - Cada "view" (Enquetes/Financeiro/Clientes) abre uma `#section-*` no conteúdo e
    fecha as demais (`enquetes.js`, `finance-toggle.js`, `clientes.js`).
- **API Bernardo** (`app/routers/bernardo.py`): `GET /api/bernardo/sessions/{name}`
  e `POST /api/bernardo/sessions/{name}/close`. Protegida pelo middleware (qualquer
  logado acessa hoje).

## Design

### 1. Novo papel `bernardo` (`auth_service.py`)

- `ROLES = ("admin", "estoque", "logistica", "bernardo")`.
  `verify_credentials` passa a aceitar `username == "bernardo"` automaticamente,
  validando contra `RAYLOOK_USER_BERNARDO_HASH`.
- `visible_groups` passa a ser a **fonte única de verdade** dos blocos da sidebar:
  - `admin` → `("comercial", "bernardo", "estoque", "logistica", "enquetes", "financeiro", "clientes")`
  - `bernardo` → `("bernardo",)`
  - `estoque` → `("estoque", "enquetes", "clientes")` *(ganha enquetes+clientes — preserva o que já vê hoje)*
  - `logistica` → `("logistica", "enquetes", "clientes")` *(idem)*
- RBAC de pacotes inalterado: `can_advance/can_regress/can_cancel/can_restore`
  retornam `False` para `bernardo` (cai no `return False` final). Bernardo não
  toca em estados de pacote.

**Ordem na tupla não importa** para o filtro; a ordem visual do rail é controlada
pelo array `RAIL_GROUPS` no JS.

### 2. Bernardo no rail, entre Comercial e Estoque (`dashboard_v2.js`)

- Adicionar entrada `bernardo` em `RAIL_GROUPS`, **logo após `comercial`**, marcada
  como view-panel (não navegação por estado). Forma proposta:
  ```js
  { id: "bernardo", label: "Bernardo", panel: true }
  ```
- Em `renderRail()`, ramo para `g.panel`: renderiza só o header clicável
  (sem `rail-step` de estados, sem contagem por estado). Continua filtrado por
  `visibleGroups.has(g.id)`.
- Clique no header de Bernardo: abre `#section-bernardo` e fecha as outras views
  (mesma coreografia que `enquetes.js` usa — fechar financeiro/clientes/enquetes).

### 3. Gating dos blocos estáticos por `visible_groups`

- `#enquetes-group`: esconder (`display:none`) quando `!visibleGroups.has("enquetes")`.
- `#clientes-group`: esconder quando `!visibleGroups.has("clientes")`.
- `#fin-group`: mantém o gate atual (`financeiro`).
- Resultado: `bernardo` (visible_groups = só `bernardo`) não vê rail de estados,
  nem Enquetes/Financeiro/Clientes. Admin/estoque/logística inalterados (porque
  ganharam os ids correspondentes na tupla).

### 4. View Bernardo no conteúdo (`#section-bernardo` + `static/js/bernardo_section.js`)

- Nova `#section-bernardo` na área de conteúdo do `dashboard_v2.html` (mesmo padrão
  de `#section-enquetes`: escondida por default, `.active` ao abrir).
- `bernardo_section.js`: ao abrir, faz `GET /api/bernardo/sessions/Bernardo`,
  renderiza os cards de acúmulo (titulo, total_qty, participantes) e o botão
  **"Fechar pacote"** (desabilitado quando `total_qty <= 0`) → `POST .../close` →
  re-render. `escapeHtml` em título/nome antes de `innerHTML` (mesma proteção do
  `bernardo_page.js`).
- O nome da sessão (`"Bernardo"`) vem do mesmo config em código (`app/sessions.py`).

### 5. Guard de autorização da API (`app/routers/bernardo.py`)

- Restringir `/api/bernardo/*` a `admin` + `bernardo` (defesa em profundidade —
  hoje qualquer role logado acessa). Ler `request.state.role` (setado pelo
  middleware); 403 para os demais. Em `DASHBOARD_AUTH_DISABLED=true` o middleware
  injeta role `admin`, então testes/local seguem funcionando.

### 6. Usuário `bernardo`: secret + deploy

- Gerar hash bcrypt de `Bernard0` (custo 12, como os demais).
- `deploy/docker-stack.yml`: adicionar
  `RAYLOOK_USER_BERNARDO_HASH: ${RAYLOOK_USER_BERNARDO_HASH:?obrigatório}` no bloco
  de env do service dashboard.
- `deploy/.env` (host, gitignored): adicionar `RAYLOOK_USER_BERNARDO_HASH=<hash>`.
  **Edição confirmada com o usuário antes de aplicar** (regra de secrets). O hash
  nunca é commitado nem impresso em logs.
- Deploy pelo fluxo normal (push em `main` → CI lê `deploy/.env` do host →
  `docker stack deploy`).

## Testes

- `tests/unit/test_auth_service.py` (ou equivalente):
  - `"bernardo" in ROLES`.
  - `visible_groups("bernardo") == ("bernardo",)`.
  - `visible_groups("admin")` contém `"bernardo"` e `"clientes"`.
  - `visible_groups("estoque")`/`("logistica")` contêm `enquetes` e `clientes`.
  - `verify_credentials("bernardo", "Bernard0")` com hash de teste == `"bernardo"`;
    senha errada → `None`.
  - `can_cancel("bernardo") is False`.
- API guard: `GET /api/bernardo/sessions/Bernardo` com role `estoque` → 403;
  com `admin`/`bernardo` → 200 (monkeypatch de role/middleware como nos testes
  existentes de bernardo).
- Browser (manual, porta livre + SQLite scratch):
  - login `bernardo` → vê só a sessão Bernardo, sem rail de estados/Enquetes/
    Financeiro/Clientes.
  - login `admin` → Bernardo aparece entre Comercial e Estoque; abre os cards;
    "Fechar pacote" gera pacote que entra no pipeline normal.

## Riscos / observações

- **Toca o dashboard normal** (`dashboard_v2.html`, `dashboard_v2.js`) — isso é
  intencional nesta fase (diferente da fase standalone). Sem migration de banco.
- `estoque`/`logistica` passam a ter `enquetes`/`clientes` explícitos na tupla só
  para **preservar** o comportamento atual (não é mudança de UX para eles).
- Deploy exige o env novo presente no `deploy/.env` do host **antes** do
  `docker stack deploy`, senão o `:?obrigatório` falha o deploy.
