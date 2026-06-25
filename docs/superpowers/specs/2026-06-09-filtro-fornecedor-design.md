# Filtro por fornecedor na lista central — Design

**Data:** 2026-06-09
**Branch:** `feat/filtro-fornecedor`

## Objetivo

Adicionar um dropdown à esquerda do campo de busca por nome/telefone na lista
central de pacotes, permitindo filtrar os pacotes pelo fornecedor.

## Decisões

- **Fonte das opções:** lista cadastrada completa via `/api/enquetes/fornecedores`
  (mesma fonte do modal de confirmar pacote, já exposta por `L.fetchFornecedores()`).
- **Opção "Sem fornecedor":** incluída, pra localizar pacotes que ainda não têm
  fornecedor definido.
- **Escopo:** filtro aplica à aba/estado ativo, combinado em **E** com a busca de
  texto e o filtro de data já existentes.
- **Persistência:** a seleção persiste ao trocar de aba/estado (variável no escopo
  do módulo, igual ao `search`).

## Componentes

### 1. HTML — `templates/dashboard_v2.html` (`.pkg-list-head`, ~linha 1067-1073)

Adicionar um `<select id="fornecedor-filter">` à esquerda do `#search`, estilizado
para combinar com `.search-box`. As opções são montadas via JS no `load()`.

### 2. JS — `static/js/dashboard_v2.js`

- Nova variável de estado no escopo do módulo: `fornecedorFilter = ""`.
  - `""` → Todos (padrão)
  - `"__none__"` → Sem fornecedor (pacote com `fornecedor` vazio/ausente)
  - qualquer outro valor → match exato em `p.fornecedor`
- Popular o `<select>` no `load()` (ou na inicialização) via `L.fetchFornecedores()`:
  `Todos` (value `""`) + cada fornecedor + `Sem fornecedor` (value `"__none__"`).
  Preservar a seleção atual ao repopular.
- Em `renderList()`, após o filtro de busca por texto, aplicar o filtro por
  fornecedor sobre o resultado:
  - `__none__` → itens sem `fornecedor` (vazio/ausente)
  - nome exato → `p.fornecedor === valor`
- Listener `change` no select → resetar `listPage = 1` e chamar `renderList()`.

### 3. Backend — `app/routers/dashboard.py` (item `client_row`, ~linha 440-454)

Os estados **Separado** e **Enviado** renderizam linhas-por-cliente (`client_row`)
que hoje **não** trazem `fornecedor` no payload. Adicionar:

```python
"fornecedor": pkg.get("fornecedor") or "",
```

ao dicionário do `client_row`, pra o filtro funcionar também nessas abas.

## Fluxo de dados

1. `load()` busca os dados e popula o dropdown de fornecedores.
2. Usuário seleciona um fornecedor → `change` → `renderList()`.
3. `renderList()` pega `currentItems()` (estado ativo), aplica busca de texto,
   depois aplica `fornecedorFilter`, pagina e renderiza.
4. Trocar de aba mantém `fornecedorFilter`; a lista re-renderiza já filtrada.

## Edge cases

- Pacote sem fornecedor + filtro por nome → não aparece (correto).
- Filtro `__none__` → mostra pacotes sem fornecedor (incl. `client_row` agora que
  o campo existe).
- Dropdown vazio (nenhum fornecedor cadastrado) → só `Todos` + `Sem fornecedor`.
- Repopular o dropdown não deve perder a seleção atual do usuário.

## Testes / verificação

- Abrir o dashboard no browser e validar: filtro combina com busca, persiste ao
  trocar de aba, `Sem fornecedor` lista os corretos, funciona em Separado/Enviado.
- Sem mudança de contrato de API além do novo campo no `client_row`.
