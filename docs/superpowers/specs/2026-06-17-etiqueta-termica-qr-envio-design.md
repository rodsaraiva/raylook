# Etiqueta térmica + QR de "marcar enviado" por cliente

**Data:** 2026-06-17
**Status:** Design aprovado (aguardando revisão do spec)

## 1. Objetivo

Trocar a etiqueta atual (PDF A4, vários clientes empilhados numa folha) por um
formato adequado a **impressora térmica de etiqueta adesiva** (Zebra/Elgin/Argox):
uma etiqueta destacável por cliente, num rolo de tamanho parametrizável. Cada
etiqueta ganha um **QR code** que, ao ser escaneado, marca **aquele cliente** como
**enviado** — direto, sem tela de confirmação prévia.

## 2. Decisões tomadas (brainstorming)

| Tema | Decisão |
|------|---------|
| Mídia | Etiqueta adesiva pré-cortada (Zebra/Elgin/Argox) |
| Tamanho | **Parametrizável** (default 60×40 mm até o rolo ser definido) |
| Conteúdo por etiqueta | Nome + telefone, quantidade, código do pacote + nº do pedido (ex: `3/12`), valores (preço/total/comissão), QR |
| QR — ação | Marca **direto** (1 toque), **só "enviado"** (não "separado") |
| QR — granularidade | **Por cliente individual** |
| QR — confirmação | Sem tela prévia; página de **feedback pós-ação** ("✓ enviado") |
| Geração | **PDF tamanho-etiqueta via xhtml2pdf**, 1 etiqueta por página, QR como PNG embutido |
| Coexistência | **Os dois formatos disponíveis** — A4 (default) e térmica lado a lado; a A4 não é removida |

## 3. Descoberta-chave: o backend de "enviado por cliente" já existe

O modelo e a lógica de envio por cliente **já estão prontos** e não precisam de
mudança:

- `pacote_clientes.shipped_at` já existe nos dois schemas (`deploy/postgres/schema.sql`,
  `deploy/sqlite/schema.sql`).
- `_derive_client_state` (`app/routers/dashboard.py:141`) já deriva `enviado`
  quando `pc.shipped_at` está setado.
- `_derive_state` (`:165`) já promove o **pacote** a `enviado` só quando **todos**
  os `pacote_clientes` têm `shipped_at`; parcial fica em `separado`.
- `advance_client(?to=enviado)` (`:1167`) já marca `shipped_at` de um cliente
  individual (idempotente) e propaga `pkg.shipped_at` quando é o último.

**Logo, a feature nova é só: (a) reformatar a etiqueta e (b) criar a porta de
entrada pública do QR que reusa essa lógica.** Nenhuma migration nova.

## 4. Arquitetura

Quatro peças, cada uma com uma responsabilidade isolada.

### 4.1 Token assinado do QR — `app/services/label_token.py` (novo)

Reusa o padrão HMAC-SHA256 + base64url já presente em
`app/services/auth_service.py:60-78`.

- `make_ship_token(pacote_id, cliente_id) -> str`
  payload `{"p": pacote_id, "c": cliente_id}` → `b64url(payload).b64url(sig)`.
- `read_ship_token(token) -> tuple[pacote_id, cliente_id] | None`
  valida assinatura com `hmac.compare_digest`; retorna `None` se falsificado/malformado.
- **Sem expiração** — a etiqueta impressa é usada por dias.
- Secret: env `LABEL_QR_SECRET`, com fallback para `SESSION_SECRET` (mesmo
  `_secret()` do auth) e `dev-secret` em local.

**Por que assinar:** impede forjar URLs para clientes/pacotes arbitrários sem
conhecer o secret. (Ver trade-off em §8.)

### 4.2 Rota pública do QR — `app/routers/shipping_qr.py` (novo), montado em `/s`

`GET /s/{token}` — pública (sem sessão; a autorização é o HMAC).

1. `read_ship_token(token)` → 404/página de erro se inválido.
2. Carrega pacote + `pacote_cliente` + pagamento. 404 se sumiu.
3. Se o pagamento não está `paid` → página de erro "Cliente ainda não pagou".
4. Senão, seta `shipped_at = now()` naquele `pacote_cliente` se ainda não setado
   (e `payment_validated_at`/`pdf_sent_at` se faltarem, espelhando
   `advance_client(to=enviado)` — idempotente). Propaga `pkg.shipped_at` quando
   for o último cliente sem envio.
5. Renderiza página mínima de feedback (HTML, mobile-first):
   - sucesso novo: "✓ **{nome}** marcado como enviado"
   - reescan: "**{nome}** já estava enviado" (idempotente, sem erro)

A escrita reaproveita a função de marcação. Para evitar duplicar regra de
estado, extrair o corpo de `advance_client` que escreve `shipped_at` + propaga
para um helper compartilhado (`_mark_client_shipped(client, pkg, pc, role)`),
chamado tanto pelo endpoint admin quanto pela rota do QR. `role` na rota do QR =
`"qr"` (registrado em `shipped_by`).

`URL` impressa no QR: `https://{DOMAIN_HOST}/s/{token}`, com
`DOMAIN_HOST` lido como em `app/routers/portal.py:97`.

### 4.3 Geração da etiqueta — `estoque/pdf_builder.py` + template novo

- Novo template `estoque/templates/etiqueta_termica.html`:
  - `@page { size: {{ w_mm }}mm {{ h_mm }}mm; margin: 2mm }`
  - **uma etiqueta por página**: cada cliente num bloco com
    `page-break-after: always`.
  - layout em tabela (xhtml2pdf não tem flexbox): coluna de texto à esquerda,
    QR (~18–22 mm) à direita.
  - QR embutido como `<img src="data:image/png;base64,…">`.
- `pdf_builder.build_pdf` ganha parâmetro `formato="a4" | "termica"` e
  `w_mm`/`h_mm`. Em `termica`, gera o QR de cada voto com a lib `qrcode`
  (já instalada) → PNG em memória → data-URI, e renderiza o template térmico.
  - O `vote` precisa carregar `cliente_id` (hoje o builder só recebe name/phone/qty);
    o endpoint de download passa a incluir `cliente_id` em cada voto para o token.

### 4.4 Endpoint de download — estende o existente

`GET /api/dashboard/packages/{id}/etiqueta.pdf` (`dashboard.py:1104`) ganha
querystring opcional:

- `?fmt=termica` → formato térmico (default permanece `a4`, nada quebra).
- `?w=&h=` → override do tamanho em mm (senão usa env/`default`).

O dashboard ganha um botão/seleção "Etiqueta térmica" ao lado do "📄 Etiqueta"
atual (`static/js/dashboard_v2.js`), apontando para `?fmt=termica`.

## 5. Parametrização (config)

Novas envs em `app/config.py` (todas com default; nenhuma obrigatória):

| Env | Default | Função |
|-----|---------|--------|
| `LABEL_QR_SECRET` | fallback `SESSION_SECRET` | assina o token do QR |
| `ETIQUETA_TERMICA_W_MM` | `60` | largura default da etiqueta |
| `ETIQUETA_TERMICA_H_MM` | `40` | altura default da etiqueta |

`DOMAIN_HOST` já existe (default `raylook.v4smc.com`).

## 6. Fluxo end-to-end

```
Estoque clica "Etiqueta térmica" no pacote
  → GET /api/dashboard/packages/{id}/etiqueta.pdf?fmt=termica
  → build_pdf(formato="termica"): 1 página por cliente, cada uma com
    QR = make_ship_token(pacote_id, cliente_id)
  → imprime no rolo pela impressora térmica (driver do SO)

Estoque separa a sacola do cliente, cola a etiqueta, despacha, escaneia o QR
  → GET https://raylook.v4smc.com/s/{token}
  → read_ship_token valida → _mark_client_shipped → shipped_at = now()
  → pkg vira "enviado" quando o último cliente sai
  → página: "✓ Maria — enviado"
```

## 7. Tratamento de erros / edge cases

| Caso | Comportamento |
|------|---------------|
| Token inválido/falsificado | Página de erro, HTTP 400/404, sem mudar nada |
| Pacote/cliente removido | Página de erro 404 |
| Cliente ainda não pagou | Página "não é possível enviar — cliente não pagou" |
| Reescan (já enviado) | Idempotente: "já estava enviado", sem erro |
| Pacote com 1 cliente | Ao escanear, pacote vira "enviado" direto |
| QR gerado mas `qrcode`/Pillow falha | build_pdf loga e retorna 500 (igual hoje no A4) |

## 8. Segurança e trade-offs

- **"Marca direto sem login"** (decisão do usuário): quem tiver a etiqueta física
  consegue marcar enviado. Para etiqueta **interna de estoque**, risco baixo e
  aceito. Mitigações: o HMAC impede forjar URLs de *outros* clientes; a ação é
  **idempotente** (reescan não causa dano) e reversível pelo admin
  (`advance_client`/regress já existem).
- Sem expiração no token: aceitável porque a única ação possível é marcar envio
  de um cliente já pago. Não expõe dado sensível na página de feedback além do
  nome do cliente.
- A rota `/s/...` é pública por design (escaneada no celular sem sessão). Ela
  **não** lê cookie de sessão nem concede acesso ao dashboard.

## 9. Testes

- **Unit `label_token`**: round-trip make/read; assinatura adulterada → `None`;
  payload malformado → `None`.
- **Unit `pdf_builder` térmico**: retorna bytes de PDF não-vazio; uma página por
  cliente (contagem de page-breaks); `<img` do QR presente; respeita `w_mm/h_mm`.
- **Integração rota `/s/{token}`** (SQLite em memória, padrão do projeto):
  marca `shipped_at`; reescan idempotente; token inválido → erro; cliente não
  pago → erro; ao enviar o último cliente, `pacote.shipped_at` é setado.
- UI: abrir o PDF térmico no browser e validar layout antes de declarar pronto
  (xhtml2pdf é limitado em CSS — layout apertado precisa de inspeção visual).

## 10. Fora de escopo

- Remover a etiqueta A4 — **fica permanentemente disponível** como `fmt=a4`
  (default). Os dois formatos coexistem; a térmica é uma opção adicional, não uma
  substituição.
- Envio direto à impressora (ZPL/raw socket) — descartado (inviável em app
  hospedado sem agente local).
- Estado "separado" por QR — o QR cobre só "enviado", por decisão do usuário.
- Mudanças no fluxo de estados ou no modelo de dados — nada novo; `shipped_at`
  já existe.

## 11. Itens de implementação (resumo)

1. `app/services/label_token.py` — make/read token HMAC + testes unit.
2. `estoque/templates/etiqueta_termica.html` — template 1-por-página com QR.
3. `estoque/pdf_builder.py` — `formato="termica"`, geração de QR, `w_mm/h_mm`.
4. `app/routers/dashboard.py` — `_mark_client_shipped` helper (extraído de
   `advance_client`); `etiqueta.pdf` aceita `?fmt/w/h` e passa `cliente_id`.
5. `app/routers/shipping_qr.py` — rota pública `GET /s/{token}` + página de
   feedback; montar router em `main.py`.
6. `app/config.py` — `LABEL_QR_SECRET`, `ETIQUETA_TERMICA_W_MM/H_MM`.
7. `static/js/dashboard_v2.js` — botão "Etiqueta térmica".
8. Testes: unit (token, pdf) + integração (rota `/s`).
