# Portal Redesign — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesenhar o portal do cliente (`/portal`) com identidade visual "Dourado Quente" e melhorar a UX do fluxo de pagamento PIX, mantendo mobile-first.

**Architecture:** Reescrever `static/css/portal.css` com novos tokens de cor e componentes. Atualizar `templates/portal_pedidos.html` para a estrutura de card com banner. Adicionar `static/js/portal-sheet.js` para o comportamento do bottom sheet do PIX (substitui a lógica inline atual em `static/js/portal.js`). Não alterar nenhum endpoint Python.

**Tech Stack:** HTML/CSS/Vanilla JS · FastAPI Jinja2 templates · Mobile-first CSS

---

## Identidade Visual — Dourado Quente

### Tokens de cor (sobrescreve portal.css)

| Token | Valor | Uso |
|---|---|---|
| `--bg-main` | `#0c0a09` | Fundo principal (mais quente que #0a0a0b) |
| `--bg-gradient` | `radial-gradient(ellipse at 0% 0%, rgba(212,175,55,0.08) 0%, transparent 55%), radial-gradient(ellipse at 100% 100%, rgba(180,100,50,0.06) 0%, transparent 50%)` | Fundo do body |
| `--card-bg` | `rgba(255,240,200,0.04)` | Cards (tom âmbar suave) |
| `--card-border` | `rgba(232,197,106,0.20)` | Bordas dos cards |
| `--accent` | `#e8c56a` | Dourado âmbar (mais quente que #d4af37) |
| `--accent-gradient` | `linear-gradient(135deg, #d4af37, #c49b2a)` | Botões de ação |
| `--accent-soft` | `rgba(212,175,55,0.15)` | Backgrounds de accent suave |
| `--text-primary` | `#fafaf9` | Texto principal |
| `--text-secondary` | `#a8a29e` | Texto secundário |
| `--text-muted` | `#6b6560` | Texto apagado (mais quente que #71717a) |
| `--success` | `#4ade80` | Pago |
| `--success-soft` | `rgba(74,222,128,0.12)` | Background pago |
| `--warning` | `#fbbf24` | Pendente |
| `--warning-soft` | `rgba(251,191,36,0.12)` | Background pendente |
| `--danger` | `#f87171` | Erro |
| `--danger-soft` | `rgba(248,113,113,0.15)` | Background erro |
| `--radius-card` | `20px` | Border-radius dos cards principais |
| `--radius-btn` | `13px` | Border-radius dos botões |
| `--radius-field` | `12px` | Border-radius dos campos |

### Tipografia
- Body: `Inter` (já carregado)
- Títulos e valores monetários: `Outfit` (já carregado)
- Sem Montserrat

### Logo
Todas as telas exibem `✦ RAYLOOK` — o símbolo `✦` faz parte da identidade.

---

## Componentes Redesenhados

### 1. Login (`portal_login.html` + `portal.css`)

- Fundo com `--bg-gradient` fixo
- Logo `✦ RAYLOOK` topo esquerdo, `font-size: 11px`, `letter-spacing: 0.25em`, cor `--accent`
- Card centralizado com `border-radius: 22px`, `--card-bg`, `--card-border`
- Ícone 🛍 em círculo: `46×46px`, `background: linear-gradient(135deg, rgba(212,175,55,0.2), rgba(180,100,50,0.12))`, borda `rgba(232,197,106,0.3)`
- Campos: `--card-bg` com borda `--card-border`, `border-radius: --radius-field`, ícone à esquerda em `--text-muted`
- Botão submit: `--accent-gradient`, cor texto `#0c0a09`, `border-radius: --radius-btn`, `font-family: Outfit`, `font-weight: 700`
- Link "Esqueci minha senha": `--text-muted`, hover `--accent`
- As mesmas regras se aplicam a `portal_setup.html`, `portal_reset.html`, `portal_reset_confirm.html` (já usam `portal.css`)

### 2. Card de Pedido (`portal_pedidos.html` + `portal.css`)

Estrutura HTML do card:
```html
<div class="order-card" data-status="{{ order.status }}" data-venda-id="{{ order.id }}">
  <div class="order-banner">{{ order.enquete_titulo or order.produto_nome | upper }}</div>
  <div class="order-body">
    <div class="order-top-row">
      <div class="order-title">{{ order.enquete_titulo or order.produto_nome }}</div>
      <span class="badge pending|paid|cancelled">…</span>
    </div>
    <div class="order-meta">{{ data }} · {{ qty }} peças</div>
    <div class="order-price-row">
      <div class="price-block">
        <div class="value">R$ {{ total }}</div>
        <div class="breakdown">{{ qty }} × R$ {{ unit }}</div>
      </div>
      <!-- botão pagar OU confirmação pago -->
    </div>
  </div>
</div>
```

CSS do card:
- `.order-card`: `background: --card-bg`, `border: 1px solid --card-border`, `border-radius: --radius-card`, sem thumbnail
- `.order-banner`: `height: 52px`, `background: linear-gradient(135deg, rgba(212,175,55,0.16), rgba(180,100,50,0.09))`, `border-bottom: 1px solid rgba(232,197,106,0.13)`, texto `font-family: Outfit`, `font-weight: 700`, `color: rgba(232,197,106,0.55)`, `font-size: 10px`, `letter-spacing: 0.18em`, `text-transform: uppercase`
- `.order-body`: `padding: 12px 14px`
- `.order-top-row`: `display: flex`, `justify-content: space-between`, `align-items: flex-start`
- `.order-title`: `font-family: Outfit`, `font-size: 13px`, `font-weight: 700`, `color: --text-primary`
- `.order-meta`: `font-size: 10px`, `color: --text-muted`, `margin: 4px 0 8px`
- `.order-price-row`: `display: flex`, `align-items: flex-end`, `justify-content: space-between`, `padding-top: 10px`, `border-top: 1px dashed rgba(232,197,106,0.12)`
- `.price-block .value`: `font-family: Outfit`, `font-size: 16px`, `font-weight: 700`, `color: --text-primary`
- `.price-block .breakdown`: `font-size: 10px`, `color: --text-muted`
- `.btn-pay`: `background: --accent-gradient`, `color: #0c0a09`, `border: none`, `border-radius: 11px`, `padding: 8px 14px`, `font-size: 11px`, `font-weight: 700`, `font-family: Outfit`
- Remover `.order-thumb` e `.order-top` (substituídos pelo `.order-banner`)

### 3. KPI Cards (`portal_pedidos.html`)

- Mantém grid 2 colunas
- `border-radius: 16px`, `--card-bg`, `--card-border`
- `.kpi-title i` cor `--accent`
- `.kpi-card.pending .kpi-value` cor `--warning`
- `.kpi-card.paid .kpi-value` cor `--success`

### 4. Filter Chips

- `.filter-chip`: `--card-bg`, `--card-border`, `color: --text-secondary`
- `.filter-chip.active`: `background: --accent`, `color: #0c0a09`, `border-color: --accent`

### 5. Header (`portal_pedidos.html`)

- Logo `✦ RAYLOOK` esquerda, `font-size: 10px`, `letter-spacing: 0.22em`, `color: --accent`
- Avatar: `28×28px`, `background: --accent-soft`, `border: 1px solid --card-border`, inicial do nome em `--accent`
- Logout: ícone simples, `color: --text-muted`

---

## Bottom Sheet PIX — novo componente

### Comportamento

1. Cliente clica em `btn-pay` em qualquer card
2. Overlay escuro (`rgba(0,0,0,0.65)`) + `backdrop-filter: blur(3px)` cobre a tela
3. Bottom sheet sobe com animação `transform: translateY(0)` (de `100%` para `0`)
4. Sheet mostra: nome do pedido, valor em destaque (26px âmbar), QR Code, campo do código + botão "Copiar código"
5. Botão `✕` ou toque no overlay fecha o sheet
6. Estado "copiado" muda botão para verde por 2 segundos

### HTML do sheet (fora do loop de pedidos, único elemento no body)

```html
<div id="pix-sheet-overlay" class="pix-overlay" style="display:none"></div>
<div id="pix-sheet" class="pix-sheet" style="display:none">
  <div class="pix-sheet-handle"></div>
  <div class="pix-sheet-header">
    <div class="pix-sheet-name" id="pix-sheet-name">—</div>
    <button class="pix-sheet-close" id="pix-sheet-close">✕</button>
  </div>
  <div class="pix-sheet-amount" id="pix-sheet-amount">—</div>
  <div class="pix-sheet-qr-row">
    <div class="pix-qr-box" id="pix-qr-img">▦</div>
    <div class="pix-qr-side">
      <div class="pix-qr-label">Código PIX copia e cola</div>
      <div class="pix-code-box" id="pix-code-text">—</div>
      <button class="pix-copy-btn" id="pix-copy-btn">📋 Copiar código</button>
    </div>
  </div>
  <div class="pix-sheet-divider"></div>
  <div class="pix-sheet-tip">Abra o app do banco e escaneie o QR Code ou cole o código</div>
</div>
```

### CSS do sheet (`portal.css`)

```css
.pix-overlay {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(0,0,0,0.65);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}
.pix-sheet {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 101;
  background: #14100c;
  border: 1px solid rgba(232,197,106,0.25);
  border-bottom: none;
  border-radius: 24px 24px 0 0;
  padding: 10px 20px 32px;
  box-shadow: 0 -30px 80px rgba(0,0,0,0.8);
  transform: translateY(100%);
  transition: transform 0.4s cubic-bezier(0.16,1,0.3,1);
}
.pix-sheet.open { transform: translateY(0); }
.pix-sheet-handle { width: 36px; height: 3px; background: rgba(232,197,106,0.3); border-radius: 99px; margin: 0 auto 16px; }
.pix-sheet-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.pix-sheet-name { font-family: 'Outfit', sans-serif; font-size: 14px; font-weight: 700; color: var(--text-primary); }
.pix-sheet-close { width: 28px; height: 28px; border-radius: 50%; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 12px; }
.pix-sheet-amount { font-family: 'Outfit', sans-serif; font-size: 28px; font-weight: 700; color: var(--accent); margin-bottom: 16px; line-height: 1; }
.pix-sheet-qr-row { display: flex; gap: 14px; align-items: center; margin-bottom: 12px; }
.pix-qr-box { width: 80px; height: 80px; background: #fff; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.pix-qr-box img { width: 100%; height: 100%; object-fit: cover; border-radius: 10px; }
.pix-qr-side { flex: 1; }
.pix-qr-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 6px; }
.pix-code-box { background: rgba(255,255,255,0.04); border: 1px solid var(--card-border); border-radius: 10px; padding: 7px 10px; font-family: monospace; font-size: 10px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 6px; }
.pix-copy-btn { width: 100%; background: var(--accent-soft); color: var(--accent); border: 1px solid var(--card-border); border-radius: 10px; padding: 7px 10px; font-size: 11px; font-weight: 700; font-family: 'Inter', sans-serif; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s; }
.pix-copy-btn.copied { background: var(--success-soft); color: var(--success); border-color: rgba(74,222,128,0.25); }
.pix-sheet-divider { height: 1px; background: rgba(232,197,106,0.1); margin: 12px 0; }
.pix-sheet-tip { font-size: 11px; color: var(--text-muted); text-align: center; }
```

### JS do sheet (`static/js/portal-sheet.js` — novo arquivo)

Responsabilidades:
1. Ao clicar em `.btn-pay[data-pagamento]`: chama `GET /portal/api/pix/{pagamento_id}`, preenche o sheet e abre
2. Ao clicar em `#pix-copy-btn`: copia o código, muda botão para "✓ Copiado!", volta ao normal após 2s
3. Ao clicar em `#pix-sheet-close` ou `#pix-sheet-overlay`: fecha o sheet
4. Ao abrir o sheet: `document.body.style.overflow = 'hidden'`; ao fechar: restaura
5. Substituir completamente `static/js/portal.js` (que faz lógica inline) por este arquivo

O arquivo atual `static/js/portal.js` tem a lógica de PIX inline e de "pay all" — tudo isso vai para `portal-sheet.js`. O antigo `portal.js` deve ser deletado. Em `portal_pedidos.html`, o `<script src="/static/js/portal.js">` deve ser trocado por `<script src="/static/js/portal-sheet.js">`.

---

## Telas de autenticação (setup, reset, reset_confirm)

Já usam `portal.css` — serão atualizadas automaticamente com os novos tokens. Nenhuma mudança de HTML necessária além do que já foi feito (fonte Inter/Outfit).

---

## Escopo: o que NÃO muda

- Nenhum endpoint Python (`app/routers/portal.py`, `app/services/portal_service.py`)
- Estrutura de rotas
- Lógica de autenticação
- API `/portal/api/pix/{id}` (já existente, só o frontend muda)
- Seção "Pagar todos" (`btn-pay-all`): recebe o mesmo estilo visual do `btn-pay`. O comportamento muda: ao clicar, em vez de expandir inline, abre o mesmo bottom sheet com `name = "Todos os pedidos pendentes"` e o `pagamento_id` do pagamento consolidado. O `portal-sheet.js` deve tratar o clique em `#btnPayAll` da mesma forma que trata `.btn-pay`, usando o atributo `data-pagamento` do botão.
