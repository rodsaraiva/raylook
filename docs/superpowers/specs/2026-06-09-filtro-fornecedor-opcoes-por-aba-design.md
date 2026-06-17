# Revisão: opções do filtro de fornecedor por aba — Design

**Data:** 2026-06-09
**Branch:** `feat/filtro-fornecedor`
**Revisa:** [2026-06-09-filtro-fornecedor-design.md](2026-06-09-filtro-fornecedor-design.md)

## Mudança de decisão

A spec original derivava as opções do dropdown da **lista cadastrada completa**
(`/api/enquetes/fornecedores`). Decisão revista: o dropdown mostra **apenas as
opções possíveis** — os fornecedores presentes nos dados carregados da **aba/estado
ativo** — mais `Todos os fornecedores` e `Sem fornecedor`.

## Comportamento

- Opções = fornecedores **distintos presentes em `currentItems()`** (itens da aba
  ativa, dentro do filtro de data atual), ordenados case-insensitive (pt-BR).
- `Todos os fornecedores` (value `""`, padrão) e `Sem fornecedor` (value `"__none__"`)
  **sempre presentes**.
- O dropdown **se atualiza ao trocar de aba** (cada estado lista só os seus
  fornecedores).
- **Persistência condicional:** ao repopular, se o fornecedor selecionado ainda
  existe na aba, mantém a seleção; senão, reseta para `Todos` e sincroniza
  `fornecedorFilter = ""`. (Antes a seleção persistia incondicionalmente.)

## Componentes (só frontend — `static/js/dashboard_v2.js`)

### `populateFornecedorFilter()` — síncrona, lê de `currentItems()`

- Deixa de usar `L.fetchFornecedores()` / o endpoint `/api/enquetes/fornecedores`
  (o modal de confirmar pacote continua usando esse helper — intacto).
- Monta as opções a partir do `Set` de `fornecedor` não-vazios dos itens da aba.
- Após montar, sincroniza `fornecedorFilter` para um valor válido (mantém se a
  opção existe, senão `""`).

### Invocação a cada `render()`

- Hoje `populateFornecedorFilter()` é chamada só no fim do `load()`.
- Passa a ser chamada no início de `render()` (que é chamado no `load()` e no
  clique de troca de aba), garantindo que o dropdown reflita sempre a aba ativa.
- O listener `change` do dropdown continua chamando só `renderList()` (não
  `render()`), então mudar o filtro **não** repopula/reseta o dropdown.

### Cache-bust

- Bump do `?v=` do `dashboard_v2.js` em `templates/dashboard_v2.html`.

## Sem mudança de backend

O campo `fornecedor` nas `client_rows` (Separado/Enviado) continua necessário e já
está implementado.

## Testes / verificação

- Sem test runner JS — validação no browser:
  - Dropdown lista só fornecedores presentes na aba ativa.
  - Trocar de aba atualiza as opções.
  - Filtrar por fornecedor e por `Sem fornecedor` funciona (combinado em E com a
    busca de texto).
  - Selecionar um fornecedor e trocar para uma aba que não o tem reseta para
    `Todos`.
