# Design — Criar Pacote do Zero (ad-hoc) no Dashboard

**Data:** 2026-04-17
**Status:** Draft — aguardando revisão do usuário
**Escopo:** Adicionar fluxo complementar de criação de pacote manual sem depender de enquete WHAPI.

---

## Contexto

Hoje, o dashboard Alana permite criar pacotes de 24 peças via duas vias: (1) automática, a partir de enquetes WhatsApp (subset-sum em votos reais); (2) manual, mas **ainda exigindo uma enquete existente das últimas 72h** como âncora. Ver `main.py:1212-1289` (endpoints `/api/packages/manual/preview` e `/confirm`) e `app/services/manual_package_service.py`.

Gestores precisam criar pacotes fora desse recorte — ex: montar um pacote com um produto novo que nunca foi enquetado, ou reabrir vendas de um mix específico. Hoje isso exige criar uma enquete "fake" antes, o que é trabalhoso e polui analytics.

## Objetivo

Adicionar fluxo **"Pacote do Zero"** no dashboard que permita:
- Cadastrar produto novo na hora (nome + preço unitário + imagem).
- Upload da imagem direto no form (pro mesmo Google Drive usado hoje).
- Montar lista de clientes com autocomplete e quantidades **individuais livres** (1-24), desde que a soma feche em 24.
- Persistir usando o mesmo pipeline pós-confirmação (PDF, Asaas, métricas) do fluxo atual.

**Requisito forte de não-regressão:** o fluxo atual (com enquete) não pode quebrar nem mudar de comportamento.

## Decisões de produto (aprovadas no brainstorm)

1. **Total do pacote:** sempre 24 peças (regra dura do sistema).
2. **Quantidades individuais:** livres (1-24), validação = soma 24. Remove restrição de `{3,6,9,12,24}` só neste fluxo.
3. **Produto:** criado novo na hora, vira registro normal no catálogo (reutilizável em enquetes futuras).
4. **Preço:** unitário por peça, sem comissão. Comissão 13% somada no cálculo de total, como no fluxo atual.
5. **Clientes:** autocomplete por nome/telefone; se não encontrar, cadastra novo.
6. **Imagem:** Google Drive (mesmo padrão atual), limite 5MB, formatos jpg/png/webp.
7. **Enquete:** fluxo cria uma "enquete fantasma" com `source='manual'` pra encaixar no schema existente sem migration pesada.
8. **Ponto de acesso:** mesmo botão "Criar Pacote"; adiciona step 0 com escolha "A partir de enquete / Pacote do zero".

## Abordagem de implementação

**Módulo paralelo, isolado.** Nada em `main.py:1212-1289` (fluxo atual) é modificado. Código novo em arquivos próprios, reuso só do cliente Google Drive e do `run_post_confirmation_effects()`.

## Arquitetura e arquivos

### Novos arquivos (backend)
- `app/services/adhoc_package_service.py` — orquestra criação de produto + enquete fantasma + pacote + votos sintéticos; chama `run_post_confirmation_effects()` ao final.
- `app/api/adhoc_packages.py` — APIRouter com os 4 endpoints listados abaixo.

### Alterações em arquivos existentes
- `main.py` — uma linha adicionada: `app.include_router(adhoc_packages.router)`. Nenhuma outra mudança. (Empurrãozinho no tech debt do god file sem refatorar o mundo.)
- `templates/index.html` — adiciona step 0 no modal "Criar Pacote" + markup dos steps 1-3 do fluxo novo. Markup dos steps existentes (`mode=poll`) **permanece idêntico**.
- `static/js/dashboard.js` — adiciona seção de código adhoc. Se a seção crescer >300 linhas, extrai pra `static/js/adhoc_package.js` importado pelo `index.html`.
- `integrations/google_drive/` — **zero alteração**; apenas consumido pelo novo serviço.

### Novos testes
- `tests/unit/test_adhoc_package_service.py`
- `tests/unit/test_adhoc_packages_api.py`
- `tests/unit/test_image_upload.py`
- `tests/unit/test_adhoc_no_regression.py` — garante que o fluxo `mode=poll` atual não mudou payload nem resposta.

### Schema do banco
- **Sem migration obrigatória.** Usa enquete fantasma com `source='manual'`.
- Colunas que **podem precisar ser adicionadas** se ainda não existirem (verificar na fase do plano):
  - `polls.source` (`'whapi'|'manual'`, default `'whapi'`).
  - `products.source` (`'whapi'|'manual'`, default `'whapi'`).
  - `packages.created_via` (`'poll'|'adhoc'`, default `'poll'`).
  - `votes.synthetic` (boolean, default `false`) — pra analytics filtrar.
- Se alguma coluna já existir com nome/tipo diferente, reusar ao invés de duplicar.

## API

Todos os endpoints sob `/api/packages/adhoc/`. Feature flag `ADHOC_PACKAGES_ENABLED` (env var, default `false`) gate o registro do router.

### `POST /upload-image` (multipart/form-data)
- **Input:** arquivo `image` (jpg/png/webp, ≤5MB).
- **Valida:** MIME type, tamanho, nome sanitizado, conteúdo válido (Pillow).
- **Envia pro Drive** usando cliente existente; mesma pasta configurada em env var que os produtos atuais usam.
- **Retorna:** `{ drive_file_id, thumbnail_url, full_url }`.
- **Erro:** 4xx/5xx com mensagem clara; frontend oferece retry sem perder resto do form.

### `POST /preview` (JSON)
- **Input:**
  ```json
  {
    "product": { "name": "Vestido Floral Azul", "unit_price": 45.00, "image": { "drive_file_id": "..." } },
    "votes": [ { "phone": "5511999999999", "qty": 8, "customer_id": 123 }, ... ]
  }
  ```
- `customer_id` opcional: se veio do autocomplete, tem ID; senão backend marca pra criar novo cliente.
- **Valida:** soma qty = 24, phones regex `55\d{10,11}`, nome produto ≥3 chars, unit_price > 0.
- **Retorna:** preview com nomes resolvidos, total do pacote (`unit_price × 24`), comissão 13%, total final.
- **Não persiste nada.**

### `POST /confirm` (JSON — mesmo payload do preview)
- **Ordem transacional:**
  1. `INSERT polls` — enquete fantasma (`source='manual'`, `title` gerado, `whapi_poll_id=null`).
  2. `INSERT products` — produto novo (`source='manual'`).
  3. `INSERT packages` — vinculado à enquete fantasma, `status='closed'`, `manual_creation=true`, `created_via='adhoc'`.
  4. Pra cada voto: `UPSERT customers` por phone (se `customer_id` ausente).
  5. `INSERT votes` — voto sintético (`synthetic=true`, `voted_at=now()`, `status='in'`).
  6. `INSERT pacote_cliente` — subtotal `qty × unit_price`, comissão 13%, total.
  7. Chama `run_post_confirmation_effects()` (PDF, Asaas, métricas).
- **Atomicidade:** rollback em qualquer falha; padrão exato (RPC vs. compensatório) **definido na fase do plano** conforme o que o projeto já usa.
- **Retorna:** `{ package_id, legacy_package_id }`.

### `GET /customers/search?q=<termo>`
- Busca por nome ou telefone (LIKE/trigram), limite 10.
- **Verificar na fase do plano** se já existe endpoint similar e reusar.

## Fluxo UI

**Entrada:** mesmo botão "Criar Pacote" (zero mudança de posição).

### Step 0 — Escolha de fluxo *(novo)*
- Dois cards: "A partir de enquete" e "Pacote do zero".
- Ao escolher "enquete", segue pro fluxo existente sem alteração.
- Ao escolher "do zero", vai pro step 1 novo.
- Botão "Voltar" presente em todos os steps posteriores.

### Step 1 — Produto *(fluxo do zero)*
- Nome do produto (text, required, ≥3 chars).
- Preço unitário (currency mask). Label: *"preço/peça, sem comissão"*.
- Abaixo, cálculo em tempo real: *"Total do pacote: R$ X,XX (24 × Y,YY) + comissão 13% = R$ Z,ZZ"*.
- Upload de imagem (drag-drop ou clique), preview imediato, spinner durante upload, ✓ ao receber `drive_file_id`.
- Botão "Próximo" desabilitado até: nome + preço válido + imagem com sucesso.

### Step 2 — Clientes *(fluxo do zero)*
- Linhas dinâmicas `{cliente, qty}`, botão "+ Adicionar cliente".
- Campo cliente: autocomplete (GET `/customers/search?q=`, debounce 250ms). Dropdown mostra "Nome — (55) 11 9xxxx-xxxx"; última opção sempre "+ Cadastrar novo: <texto>".
- Campo qty: numérico livre 1-24.
- Contador sticky: "Faltam X" / "Ultrapassa em Y" / "✓ 24/24".
- Botão "Revisar" habilita em soma = 24.

### Step 3 — Preview *(fluxo do zero)*
- Card readonly: foto, nome, preço unitário, total, comissão, total final.
- Lista de clientes: nome + phone + qty + subtotal.
- Botão "Confirmar pacote" → `POST /confirm` → fecha modal, recarrega dashboard.

## Validações

| Campo | Frontend | Backend |
|---|---|---|
| Nome produto | ≥3 chars, trim | ≥3 chars, ≤120, não vazio |
| Preço unitário | > 0, mask currency | > 0, ≤ R$ 10.000 |
| Imagem | MIME + tamanho antes de enviar | MIME + tamanho + Pillow check |
| Phone | regex BR masked | regex `55\d{10,11}` |
| Qty individual | 1-24 numérico | 1-24 inteiro |
| Soma qty | = 24 (contador sticky) | = 24 (rejeita 400) |
| customer_id | existe no autocomplete | confere existência no banco |

## Tratamento de erros

- **Upload falha:** toast vermelho + retry; form preserva resto.
- **Nome produto duplicado:** backend 409 com opção de reusar existente ou renomear. *(Verificar na fase do plano se catálogo atual permite duplicados; se sim, não tratar como erro.)*
- **Falha de persistência pós-upload:** rollback do pacote; imagem fica no Drive, job de limpeza diário remove órfãs *(verificar se esse job existe; caso contrário, criar issue separada, fora do escopo deste spec)*.
- **Pós-confirmação falha parcial (Asaas fora):** mesmo comportamento do fluxo atual — pacote fica `closed` com `pending_charges`, jobs periódicos retentam.

## Testes

- Suite nova conforme listada em "Arquivos".
- **SQLite real em memória**, não mocks de DB (regra `.claude/rules/testing.md`).
- `test_adhoc_no_regression.py` é o guardião do requisito de não-regressão: exercita `mode=poll` end-to-end e compara payload/resposta com baseline fixo.
- Suite de regressão existente (49 arquivos em `tests/unit/`) deve continuar passando sem modificação.

## Rollout

1. Desenvolvimento com `ADHOC_PACKAGES_ENABLED=true` em ambiente local.
2. Rodar localmente via `docker build -t alana:dev .` + container apontando pro Postgres compartilhado (banco de dev/local) **ou** Postgres local isolado via `docker-compose.yml`. Decisão do banco fica com o usuário antes da implementação.
3. Validação manual em `localhost`: criar pacote do zero completo, abrir pacote criado, conferir PDF, conferir cobrança Asaas (sandbox ou mock).
4. Rodar suite de testes (regressão + nova) até tudo verde.
5. Revisão manual do diff pelo usuário.
6. **Só depois**, ao comando explícito do usuário, deploy (GitHub Actions → prod).

**Sem staging. Sem auto-deploy.** Feature flag `ADHOC_PACKAGES_ENABLED` fica como switch de segurança em produção.

## Riscos e pontos a verificar na fase do plano

1. **Schema:** confirmar colunas `polls.source`, `products.source`, `packages.created_via`, `votes.synthetic`; criar migrations idempotentes só onde necessário.
2. **Listagens existentes:** mapear consultas a `polls` e `products` que podem precisar filtrar `source='manual'` pra não poluir dashboards (ex: tela de enquetes ativas, catálogo exposto ao cliente, métricas).
3. **Atomicidade:** verificar o padrão já usado no projeto (RPC, transação via PostgREST, compensação manual) e seguir o mesmo.
4. **Endpoint `/customers/search`:** verificar se já existe algo equivalente e reusar em vez de criar duplicado.
5. **Catálogo duplicado:** verificar regra atual de nome de produto (unique ou não) antes de decidir tratamento 409.
6. **Job de limpeza de imagens órfãs no Drive:** verificar existência; se não existir, é issue separada.
7. **`manual_creation: true`:** flag já usada pelo fluxo manual-com-enquete. Adicionar `created_via='adhoc'` pra distinguir sem quebrar consumidores existentes dessa flag.

## Critério de aceite

- Admin consegue criar um pacote do zero end-to-end em `localhost` sem erros.
- Pacote criado aparece no dashboard igual aos demais, com foto, clientes, preços e total corretos.
- PDF e cobranças Asaas (sandbox) são gerados corretamente.
- Fluxo `mode=poll` existente não tem qualquer mudança de comportamento.
- Todos os testes (existentes + novos) passam.
- Feature flag `ADHOC_PACKAGES_ENABLED=false` deixa o sistema idêntico ao estado atual.
