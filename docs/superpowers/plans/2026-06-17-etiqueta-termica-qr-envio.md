# Etiqueta térmica + QR de envio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um formato de etiqueta para impressora térmica (1 etiqueta adesiva por cliente, com QR que marca o cliente como "enviado"), coexistindo com a etiqueta A4 atual.

**Architecture:** Reaproveita o pipeline `estoque/pdf_builder.py` (xhtml2pdf) com um template novo de tamanho parametrizável e 1 página por cliente. Cada etiqueta embute um QR (lib `qrcode`) apontando para uma rota pública `/s/{token}`; o token é HMAC-assinado (mesmo esquema do `auth_service`). A rota reusa a lógica de marcação `shipped_at` por cliente que **já existe** no backend — nenhuma migration nova.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, xhtml2pdf (pisa), qrcode + Pillow, pytest com `FakeSupabaseClient`.

## Global Constraints

- Idioma de todo texto/UI: **pt-BR**.
- Rodar testes com: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -v` (o middleware de auth é bypassado nessa flag).
- Testes seguem o padrão do projeto: `tests/_helpers/fake_supabase.py` (`FakeSupabaseClient`, `empty_tables`, `install_fake`). Não criar mocks ad-hoc de DB.
- Token do QR: **HMAC-SHA256, base64url, sem expiração**. Secret: env `LABEL_QR_SECRET`, fallback para o `_secret()` do `auth_service` (que lê `SESSION_SECRET`).
- Etiqueta térmica default: **60×40 mm**. Override por env `ETIQUETA_TERMICA_W_MM`/`ETIQUETA_TERMICA_H_MM` e por querystring `?w=&h=`.
- A etiqueta **A4 continua como default** (`?fmt=a4` ou sem query). Térmica é opt-in via `?fmt=termica`.
- Base URL pública: `os.getenv("DOMAIN_HOST", "raylook.v4smc.com")`.
- **Não fazer push** — o usuário aprova antes.

---

## File Structure

| Arquivo | Responsabilidade | Ação |
|---|---|---|
| `app/services/label_token.py` | Gerar/validar token HMAC `pacote_id:cliente_id` | Criar |
| `estoque/templates/etiqueta_termica.html` | Template Jinja 1-etiqueta-por-página | Criar |
| `estoque/pdf_builder.py` | `render_label_html` + `build_pdf` com `formato`/`w_mm`/`h_mm` + QR | Modificar |
| `app/routers/dashboard.py` | Helper `_mark_client_shipped`; `etiqueta.pdf` aceita `fmt/w/h` + passa `cliente_id` | Modificar |
| `app/routers/shipping_qr.py` | Rota pública `GET /s/{token}` + página de feedback | Criar |
| `main.py` | Montar router; whitelistar `/s/` no auth | Modificar |
| `static/js/dashboard_v2.js` | Botão "Etiqueta térmica" | Modificar |
| `tests/unit/test_label_token.py` | Testes do token | Criar |
| `tests/unit/test_etiqueta_termica_pdf.py` | Testes do HTML/PDF térmico | Criar |
| `tests/unit/test_mark_client_shipped.py` | Teste do helper | Criar |
| `tests/unit/test_shipping_qr_route.py` | Testes da rota `/s` | Criar |
| `tests/unit/test_etiqueta_pdf_endpoint.py` | Teste do download `?fmt=termica` | Criar |

---

## Task 1: Token assinado do QR

**Files:**
- Create: `app/services/label_token.py`
- Test: `tests/unit/test_label_token.py`

**Interfaces:**
- Produces:
  - `make_ship_token(pacote_id: str, cliente_id: str) -> str`
  - `read_ship_token(token: str) -> Optional[Tuple[str, str]]` — `(pacote_id, cliente_id)` ou `None` se inválido/adulterado.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_label_token.py
from app.services.label_token import make_ship_token, read_ship_token


def test_token_roundtrip(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    tok = make_ship_token("pac-1", "cli-1")
    assert read_ship_token(tok) == ("pac-1", "cli-1")


def test_token_tampered_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    tok = make_ship_token("pac-1", "cli-1")
    body, sig = tok.split(".", 1)
    forged = body + "x." + sig  # corpo alterado, assinatura não confere
    assert read_ship_token(forged) is None


def test_token_wrong_secret_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "secretA")
    tok = make_ship_token("pac-1", "cli-1")
    monkeypatch.setenv("LABEL_QR_SECRET", "secretB")
    assert read_ship_token(tok) is None


def test_token_malformed_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    assert read_ship_token("garbage") is None
    assert read_ship_token("") is None
    assert read_ship_token("a.b.c") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_label_token.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.services.label_token'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/label_token.py
"""Token assinado (HMAC-SHA256) que autoriza marcar um cliente como enviado via
QR da etiqueta. Mesmo esquema do auth_service: b64url(payload).b64url(sig).
Sem expiração — a etiqueta impressa é usada por dias e a única ação possível é
marcar envio de um cliente já pago."""
from __future__ import annotations

import base64
import hmac
import json
import os
from hashlib import sha256
from typing import Optional, Tuple


def _secret() -> bytes:
    env = os.getenv("LABEL_QR_SECRET", "").encode("utf-8")
    if env:
        return env
    from app.services.auth_service import _secret as _auth_secret
    return _auth_secret()


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_ship_token(pacote_id: str, cliente_id: str) -> str:
    payload = json.dumps({"p": pacote_id, "c": cliente_id}, separators=(",", ":")).encode()
    body = _b64url_encode(payload)
    sig = hmac.new(_secret(), body.encode(), sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def read_ship_token(token: str) -> Optional[Tuple[str, str]]:
    if not token or token.count(".") != 1:
        return None
    body, sig_b64 = token.split(".", 1)
    try:
        expected = hmac.new(_secret(), body.encode(), sha256).digest()
        provided = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        data = json.loads(_b64url_decode(body))
    except (ValueError, TypeError):
        return None
    p, c = data.get("p"), data.get("c")
    if not p or not c:
        return None
    return (str(p), str(c))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_label_token.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/label_token.py tests/unit/test_label_token.py
git commit -m "feat: token HMAC pra marcar cliente enviado via QR"
```

---

## Task 2: Geração da etiqueta térmica (HTML + QR + PDF)

**Files:**
- Create: `estoque/templates/etiqueta_termica.html`
- Modify: `estoque/pdf_builder.py`
- Test: `tests/unit/test_etiqueta_termica_pdf.py`

**Interfaces:**
- Consumes: `make_ship_token` (Task 1).
- Produces:
  - `render_label_html(package: dict, commission_per_piece: float = 5.0, formato: str = "a4", w_mm: int = 60, h_mm: int = 40) -> str`
  - `build_pdf(package: dict, commission_per_piece: float = 5.0, formato: str = "a4", w_mm: int = 60, h_mm: int = 40) -> bytes`
  - Em `formato="termica"`, cada item de `package["votes"]` deve ter `cliente_id` para o QR; sem ele, a etiqueta sai sem QR.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_etiqueta_termica_pdf.py
import pytest
from estoque.pdf_builder import render_label_html, build_pdf

PACKAGE = {
    "id": "p1",
    "friendly_id": "R-12",
    "poll_title": "Blusas",
    "votes": [
        {"name": "Maria", "phone": "5562999990000", "qty": 3, "cliente_id": "c1"},
        {"name": "Ana", "phone": "5562988880000", "qty": 5, "cliente_id": "c2"},
    ],
}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    monkeypatch.setenv("DOMAIN_HOST", "raylook.v4smc.com")


def test_termica_html_one_block_per_client():
    html = render_label_html(PACKAGE, formato="termica", w_mm=60, h_mm=40)
    assert html.count('class="label"') == 2
    assert "60mm 40mm" in html  # @page size aplicado


def test_termica_html_has_qr_img_per_client():
    html = render_label_html(PACKAGE, formato="termica")
    assert html.count("<img") == 2
    assert "data:image/png;base64," in html


def test_termica_build_pdf_returns_pdf_bytes():
    pdf = build_pdf(PACKAGE, 5.0, formato="termica", w_mm=60, h_mm=40)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


def test_a4_still_default():
    html = render_label_html(PACKAGE)  # formato="a4"
    assert "size: A4" in html or "size:A4" in html.replace(" ", "") or "A4" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_etiqueta_termica_pdf.py -v`
Expected: FAIL com `ImportError: cannot import name 'render_label_html'`.

- [ ] **Step 3: Create the thermal template**

```html
<!-- estoque/templates/etiqueta_termica.html -->
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <style>
        @page { size: {{ w_mm }}mm {{ h_mm }}mm; margin: 2mm; }
        body { font-family: Helvetica, Arial, sans-serif; color: #000; margin: 0; }
        .label { page-break-after: always; }
        .label:last-child { page-break-after: auto; }
        table.l { width: 100%; border-collapse: collapse; }
        td.txt { vertical-align: top; }
        td.qr { width: 22mm; text-align: right; vertical-align: top; }
        td.qr img { width: 20mm; height: 20mm; }
        .meta { font-size: 7pt; color: #333; }
        .name { font-size: 12pt; font-weight: bold; text-transform: uppercase; }
        .phone { font-size: 8pt; }
        .qty { font-size: 15pt; font-weight: bold; margin: 1mm 0; }
        .vals { font-size: 7pt; }
        .vals.tot { font-weight: bold; }
    </style>
</head>
<body>
    {% for v in votes %}
    <div class="label">
        <table class="l"><tr>
            <td class="txt">
                <div class="meta">{{ friendly_id }} · pedido {{ v.order_num }}/{{ total_votes }}</div>
                <div class="name">{{ v.name }}</div>
                <div class="phone">{{ v.phone }}</div>
                <div class="qty">{{ v.qty }} {{ pieces_label }}</div>
                {% if unit_price > 0 %}
                <div class="vals">R$ {{ v.unit_price_fmt }} un · Total R$ {{ v.subtotal_fmt }}</div>
                <div class="vals tot">+ R$ {{ v.commission_fmt }} assess. = R$ {{ v.total_with_commission_fmt }}</div>
                {% endif %}
            </td>
            <td class="qr">{% if v.qr_uri %}<img src="{{ v.qr_uri }}">{% endif %}</td>
        </tr></table>
    </div>
    {% endfor %}
</body>
</html>
```

- [ ] **Step 4: Refactor `pdf_builder.py` — extract `render_label_html`, add QR + formato**

Add imports near the top of `estoque/pdf_builder.py` (after the existing imports):

```python
import base64
import os
import qrcode
from app.services.label_token import make_ship_token
```

Add this helper after `_fmt_brl`:

```python
def _qr_data_uri(url: str) -> str:
    """Gera o QR como PNG data-URI pra embutir no template (xhtml2pdf aceita)."""
    qr = qrcode.QRCode(box_size=4, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
```

Now split `build_pdf`. Replace the current body (the part that builds `context`, loads the template, and renders — lines ~48-112) so that everything up to and including `html_content = template.render(**context)` lives in a new `render_label_html`, and `build_pdf` calls it. The full new functions:

```python
def render_label_html(
    package: Dict[str, Any],
    commission_per_piece: float = 5.0,
    formato: str = "a4",
    w_mm: int = 60,
    h_mm: int = 40,
) -> str:
    """Renderiza o HTML da etiqueta. formato='a4' (folha com vários clientes) ou
    'termica' (1 etiqueta por página, com QR de envio)."""
    poll_title = package.get("poll_title", "Pedido")
    if poll_title and len(poll_title) > 30 and " " not in poll_title:
        poll_title = f"Enquete {poll_title[:10]}..."

    votes = package.get("votes", [])

    raw_tag = package.get("tag")
    pieces_label = "peças"
    if raw_tag is not None:
        s = str(raw_tag).strip()
        if s.lower() in {"none", "null", "undefined"}:
            s = ""
        pieces_label = s or "peças"
    pieces_label = html.escape(pieces_label, quote=True)

    valor_col = package.get("valor_col")
    unit_price = resolve_unit_price(poll_title, valor_col)

    sorted_votes = sorted(votes, key=lambda v: v.get("qty", 0), reverse=True)

    domain = os.getenv("DOMAIN_HOST", "raylook.v4smc.com")
    pacote_id = package.get("id") or ""

    processed_votes = []
    for i, v in enumerate(sorted_votes):
        try:
            qty = int(float(v.get("qty", 0) or 0))
        except Exception:
            try:
                qty = int(v.get("qty", 0))
            except Exception:
                qty = 0
        subtotal = qty * float(unit_price or 0.0)
        total_comm = subtotal + qty * float(commission_per_piece)

        qr_uri = ""
        if formato == "termica" and v.get("cliente_id") and pacote_id:
            token = make_ship_token(pacote_id, str(v["cliente_id"]))
            qr_uri = _qr_data_uri(f"https://{domain}/s/{token}")

        processed_votes.append({
            "order_num": i + 1,
            "name": v.get("name") or "Desconhecido",
            "phone": _format_phone(v.get("phone", "")),
            "qty": qty,
            "unit_price_fmt": _fmt_brl(unit_price),
            "subtotal_fmt": _fmt_brl(subtotal),
            "commission_fmt": _fmt_brl(qty * float(commission_per_piece)),
            "total_with_commission_fmt": _fmt_brl(total_comm),
            "qr_uri": qr_uri,
        })

    context = {
        "poll_title": poll_title,
        "friendly_id": package.get("friendly_id") or "",
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "votes": processed_votes,
        "total_votes": len(processed_votes),
        "unit_price": unit_price,
        "commission_per_piece": commission_per_piece,
        "pieces_label": pieces_label,
        "w_mm": w_mm,
        "h_mm": h_mm,
    }

    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template_name = "etiqueta_termica.html" if formato == "termica" else "etiqueta.html"
    template = env.get_template(template_name)
    return template.render(**context)


def build_pdf(
    package: Dict[str, Any],
    commission_per_piece: float = 5.0,
    formato: str = "a4",
    w_mm: int = 60,
    h_mm: int = 40,
) -> bytes:
    """Gera o PDF da etiqueta (A4 ou térmica)."""
    html_content = render_label_html(package, commission_per_piece, formato, w_mm, h_mm)

    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(
        io.BytesIO(html_content.encode("utf-8")),
        dest=pdf_buffer,
        encoding="utf-8",
    )
    if pisa_status.err:
        raise RuntimeError(f"Erro ao gerar PDF: {pisa_status.err}")
    return pdf_buffer.getvalue()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_etiqueta_termica_pdf.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Regression — A4 path still works**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -k "etiqueta or pdf or estoque" -v`
Expected: PASS (nenhuma regressão na geração A4).

- [ ] **Step 7: Commit**

```bash
git add estoque/pdf_builder.py estoque/templates/etiqueta_termica.html tests/unit/test_etiqueta_termica_pdf.py
git commit -m "feat: etiqueta térmica (1 por cliente, tamanho parametrizável, QR embutido)"
```

---

## Task 3: Helper `_mark_client_shipped`

**Files:**
- Modify: `app/routers/dashboard.py` (adicionar helper; refatorar ramo `enviado` de `advance_client`)
- Test: `tests/unit/test_mark_client_shipped.py`

**Interfaces:**
- Produces: `_mark_client_shipped(client, pkg: dict, pc: dict, role: str) -> bool` — seta `payment_validated_at`/`pdf_sent_at`/`shipped_at` faltantes no `pacote_cliente` (idempotente) e propaga `pkg.shipped_at` quando for o último cliente sem envio. Retorna `True` se marcou agora, `False` se já estava enviado.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mark_client_shipped.py
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.routers.dashboard as dash


def test_mark_client_shipped_sets_and_propagates():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})

    changed = dash._mark_client_shipped(
        fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr"
    )
    assert changed is True
    pc = fake.tables["pacote_clientes"][0]
    assert pc["shipped_at"] and pc["pdf_sent_at"] and pc["payment_validated_at"]
    # único cliente do pacote → pkg vira enviado
    assert fake.tables["pacotes"][0]["shipped_at"]
    assert fake.tables["pacotes"][0]["shipped_by"] == "qr"


def test_mark_client_shipped_idempotent():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})
    dash._mark_client_shipped(fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr")
    changed = dash._mark_client_shipped(
        fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr"
    )
    assert changed is False


def test_mark_client_shipped_partial_keeps_pkg_unshipped():
    fake = FakeSupabaseClient(empty_tables())
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].extend([
        {"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"},
        {"id": "pc2", "pacote_id": "p1", "cliente_id": "c2"},
    ])
    dash._mark_client_shipped(fake, fake.tables["pacotes"][0], fake.tables["pacote_clientes"][0], "qr")
    # só 1 de 2 enviado → pkg NÃO vira enviado
    assert not fake.tables["pacotes"][0].get("shipped_at")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_mark_client_shipped.py -v`
Expected: FAIL com `AttributeError: module 'app.routers.dashboard' has no attribute '_mark_client_shipped'`.

- [ ] **Step 3: Add the helper**

Insert this function in `app/routers/dashboard.py` just above `advance_client` (before the `@router.post("/packages/{pacote_id}/clients/{cliente_id}/advance")` decorator at line ~1167):

```python
def _mark_client_shipped(client, pkg: Dict[str, Any], pc: Dict[str, Any], role: str) -> bool:
    """Marca um pacote_cliente como enviado (idempotente). Seta também os
    timestamps anteriores (payment_validated_at, pdf_sent_at) se faltarem —
    equivale a avançar o cliente direto pra 'enviado'. Propaga pkg.shipped_at
    quando for o último cliente sem envio. Retorna True se marcou agora."""
    already_shipped = bool(pc.get("shipped_at"))
    now = client.now_iso()
    update_payload: Dict[str, Any] = {}
    if not pc.get("payment_validated_at"):
        update_payload["payment_validated_at"] = now
    if not pc.get("pdf_sent_at"):
        update_payload["pdf_sent_at"] = now
    if not already_shipped:
        update_payload["shipped_at"] = now
    if update_payload:
        client.update("pacote_clientes", update_payload, filters=[("id", "eq", pc["id"])])

    if not already_shipped and not pkg.get("shipped_at"):
        all_pcs = client.select(
            "pacote_clientes", filters=[("pacote_id", "eq", pkg["id"])]
        ) or []
        all_shipped = all(
            (other["id"] == pc["id"]) or other.get("shipped_at") for other in all_pcs
        )
        if all_shipped and all_pcs:
            client.update(
                "pacotes",
                {"shipped_at": now, "shipped_by": role},
                filters=[("id", "eq", pkg["id"])],
            )
    return not already_shipped
```

- [ ] **Step 4: Refactor `advance_client` to reuse it (DRY, no behavior change)**

In `advance_client`, replace the block at lines ~1221-1250 (from `now = client.now_iso()` through the `client.update("pacotes", {"shipped_at": now, ...})` propagation) with:

```python
    now = client.now_iso()
    if target_idx >= 3:
        # alvo 'enviado' (ou além): marca tudo + propaga via helper compartilhado
        _mark_client_shipped(client, pkg, pc, role)
    else:
        update_payload: Dict[str, Any] = {}
        if target_idx >= 1 and not pc.get("payment_validated_at"):
            update_payload["payment_validated_at"] = now
        if target_idx >= 2 and not pc.get("pdf_sent_at"):
            update_payload["pdf_sent_at"] = now
        if update_payload:
            client.update("pacote_clientes", update_payload, filters=[("id", "eq", pc["id"])])
```

- [ ] **Step 5: Run tests + regression**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_mark_client_shipped.py tests/unit/test_dashboard_advance.py tests/unit/test_dashboard_transitions.py -v`
Expected: PASS (helper novo verde + nenhuma regressão no advance existente).

- [ ] **Step 6: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_mark_client_shipped.py
git commit -m "refactor: extrai _mark_client_shipped pra reuso pelo QR de envio"
```

---

## Task 4: Rota pública `GET /s/{token}`

**Files:**
- Create: `app/routers/shipping_qr.py`
- Modify: `main.py` (montar router + whitelist `/s/`)
- Test: `tests/unit/test_shipping_qr_route.py`

**Interfaces:**
- Consumes: `read_ship_token` (Task 1), `_mark_client_shipped` (Task 3), `SupabaseRestClient`.
- Produces: rota `GET /s/{token}` → `HTMLResponse` (200 sucesso/idempotente, 400 erro).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_shipping_qr_route.py
import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake
from app.services.label_token import make_ship_token


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


def _seed_paid(fake, status="paid"):
    fake.tables["pacotes"].append({"id": "p1", "status": "approved"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1"})
    fake.tables["clientes"].append({"id": "c1", "nome": "Maria"})
    fake.tables["vendas"].append({"id": "v1", "pacote_cliente_id": "pc1"})
    fake.tables["pagamentos"].append({"id": "pg1", "venda_id": "v1", "status": status})


def test_qr_marks_shipped(fake_client):
    client, fake = fake_client
    _seed_paid(fake)
    res = client.get(f"/s/{make_ship_token('p1', 'c1')}")
    assert res.status_code == 200
    assert "Enviado" in res.text and "Maria" in res.text
    assert fake.tables["pacote_clientes"][0]["shipped_at"]
    assert fake.tables["pacotes"][0]["shipped_at"]


def test_qr_idempotent(fake_client):
    client, fake = fake_client
    _seed_paid(fake)
    tok = make_ship_token("p1", "c1")
    client.get(f"/s/{tok}")
    res = client.get(f"/s/{tok}")
    assert res.status_code == 200
    assert "Já enviado" in res.text


def test_qr_invalid_token(fake_client):
    client, _ = fake_client
    res = client.get("/s/garbage.sig")
    assert res.status_code == 400
    assert "inválido" in res.text.lower()


def test_qr_not_paid(fake_client):
    client, fake = fake_client
    _seed_paid(fake, status="created")
    res = client.get(f"/s/{make_ship_token('p1', 'c1')}")
    assert res.status_code == 400
    assert "pagou" in res.text.lower()


def test_qr_package_missing(fake_client):
    client, _ = fake_client
    res = client.get(f"/s/{make_ship_token('ghost', 'c1')}")
    assert res.status_code == 400
    assert "não" in res.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_shipping_qr_route.py -v`
Expected: FAIL com 404 em `/s/...` (rota ainda não existe).

- [ ] **Step 3: Create the router**

```python
# app/routers/shipping_qr.py
"""Rota pública acionada pelo QR da etiqueta térmica: marca um cliente como
enviado. Sem sessão — a autorização é o token HMAC assinado (label_token).
Trade-off aceito: quem tiver a etiqueta física consegue marcar envio; a ação é
idempotente e reversível pelo admin."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.routers.dashboard import _mark_client_shipped
from app.services.label_token import read_ship_token
from app.services.supabase_service import SupabaseRestClient

router = APIRouter()


def _page(title: str, body: str, ok: bool) -> HTMLResponse:
    color = "#16a34a" if ok else "#dc2626"
    icon = "✓" if ok else "⚠"
    html = (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title></head>"
        '<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px 20px;">'
        f'<div style="font-size:64px;color:{color};">{icon}</div>'
        f'<h1 style="font-size:20px;color:{color};margin:8px 0;">{title}</h1>'
        f'<p style="font-size:16px;color:#333;">{body}</p>'
        "</body></html>"
    )
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("/s/{token}")
def mark_shipped_via_qr(token: str, request: Request) -> HTMLResponse:
    parsed = read_ship_token(token)
    if not parsed:
        return _page("Link inválido", "QR não reconhecido ou adulterado.", ok=False)
    pacote_id, cliente_id = parsed

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        return _page("Pacote não encontrado", "Esse pacote não existe mais.", ok=False)
    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        return _page("Cliente não encontrado", "Cliente não está nesse pacote.", ok=False)

    venda = client.select("vendas", filters=[("pacote_cliente_id", "eq", pc["id"])], single=True)
    pag = (
        client.select("pagamentos", filters=[("venda_id", "eq", venda["id"])], single=True)
        if venda else None
    )
    if not pag or (pag.get("status") or "").lower() != "paid":
        return _page("Cliente não pagou", "Não dá pra marcar envio antes do pagamento.", ok=False)

    cli = client.select("clientes", columns="id,nome", filters=[("id", "eq", cliente_id)], single=True) or {}
    nome = cli.get("nome") or "Cliente"

    changed = _mark_client_shipped(client, pkg, pc, role="qr")
    if changed:
        return _page("Enviado!", f"{nome} marcado como enviado.", ok=True)
    return _page("Já enviado", f"{nome} já estava marcado como enviado.", ok=True)
```

- [ ] **Step 4: Mount router + whitelist the public path in `main.py`**

Add to the router imports block (near line 60-63):

```python
from app.routers import shipping_qr as shipping_qr_router
```

Add after `app.include_router(finance_router.router)` (line ~351):

```python
app.include_router(shipping_qr_router.router)
```

Add `"/s/"` to `_AUTH_PUBLIC_PREFIXES` (line ~260), so the QR route is reachable without a dashboard session:

```python
_AUTH_PUBLIC_PREFIXES = (
    "/health",
    "/api/supabase/health",
    "/webhook",
    "/static/",
    "/files/",
    "/metrics",
    "/portal",
    "/s/",       # QR da etiqueta térmica — marca cliente enviado (auth via token HMAC)
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_shipping_qr_route.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add app/routers/shipping_qr.py main.py tests/unit/test_shipping_qr_route.py
git commit -m "feat: rota pública /s/{token} que marca cliente enviado pelo QR"
```

---

## Task 5: Download da etiqueta aceita `fmt`/`w`/`h` e passa `cliente_id`

**Files:**
- Modify: `app/routers/dashboard.py` (`get_package_etiqueta_pdf`, linha ~1104)
- Test: `tests/unit/test_etiqueta_pdf_endpoint.py`

**Interfaces:**
- Consumes: `build_pdf(formato=..., w_mm=..., h_mm=...)` (Task 2).
- Produces: `GET /api/dashboard/packages/{id}/etiqueta.pdf?fmt=termica&w=60&h=40` → PDF térmico. Sem query → A4 (default inalterado).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_etiqueta_pdf_endpoint.py
import pytest
from fastapi.testclient import TestClient
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


def _seed_separado(fake):
    fake.tables["pacotes"].append({
        "id": "p1", "status": "approved", "pdf_sent_at": "2026-01-01T00:00:00Z",
        "enquete_id": "e1", "friendly_id": "R-9",
    })
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Blusas"})
    fake.tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "p1", "cliente_id": "c1", "qty": 3})
    fake.tables["clientes"].append({"id": "c1", "nome": "Maria", "celular": "5562999990000"})


def test_etiqueta_termica_returns_pdf(fake_client):
    client, fake = fake_client
    _seed_separado(fake)
    res = client.get("/api/dashboard/packages/p1/etiqueta.pdf?fmt=termica&w=60&h=40")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:4] == b"%PDF"


def test_etiqueta_a4_default_still_works(fake_client):
    client, fake = fake_client
    _seed_separado(fake)
    res = client.get("/api/dashboard/packages/p1/etiqueta.pdf")
    assert res.status_code == 200
    assert res.content[:4] == b"%PDF"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_etiqueta_pdf_endpoint.py -v`
Expected: FAIL — térmica ainda não suportada (PDF gerado é A4; ou faltam params). Confirme que `test_etiqueta_termica_returns_pdf` falha por o QR não aparecer / `cliente_id` ausente no voto. (Se ambos passarem por acaso, ainda assim siga pra garantir os params.)

- [ ] **Step 3: Update the endpoint**

In `app/routers/dashboard.py`, ensure `import os` exists at the top (add if missing). In `get_package_etiqueta_pdf` (line ~1104), add `cliente_id` to each vote and read query params. Replace the votes-building loop (lines ~1134-1141) so each vote carries `cliente_id`:

```python
    votes = []
    for pc in pcs:
        c = cliente_by_id.get(pc.get("cliente_id"), {})
        votes.append({
            "name": c.get("nome") or "Cliente",
            "phone": c.get("celular") or "",
            "qty": int(pc.get("qty") or 0),
            "cliente_id": pc.get("cliente_id"),
        })
```

Replace the `build_pdf` call (line ~1151) with format-aware params:

```python
    fmt = (request.query_params.get("fmt") or "a4").lower()

    def _qp_int(name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name) or default)
        except (TypeError, ValueError):
            return default

    w_mm = _qp_int("w", int(os.getenv("ETIQUETA_TERMICA_W_MM", "60")))
    h_mm = _qp_int("h", int(os.getenv("ETIQUETA_TERMICA_H_MM", "40")))

    try:
        pdf_bytes = build_pdf(
            package, settings.COMMISSION_PER_PIECE,
            formato=fmt, w_mm=w_mm, h_mm=h_mm,
        )
    except Exception:
        logger.exception("Falha ao gerar etiqueta on-demand pkg=%s", pacote_id)
        raise HTTPException(500, "Erro ao gerar PDF da etiqueta")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_etiqueta_pdf_endpoint.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py tests/unit/test_etiqueta_pdf_endpoint.py
git commit -m "feat: download da etiqueta aceita fmt=termica e tamanho parametrizável"
```

---

## Task 6: Botão "Etiqueta térmica" no dashboard

**Files:**
- Modify: `static/js/dashboard_v2.js` (linhas ~498-499, 598-599, 727, 825 — onde aparece o link "📄 Etiqueta")

**Interfaces:**
- Consumes: endpoint `?fmt=termica` (Task 5). Sem teste automatizado — validação no browser (UI).

- [ ] **Step 1: Add the thermal link next to the existing PDF label link**

For each place that renders the etiqueta link (when `pdf_sent_at` is set), add a sibling link with `?fmt=termica`. Example for the list row (`dashboard_v2.js:498-499`):

```javascript
        const etiquetaBtn = p.pdf_sent_at
            ? `<a class="row-action" href="/api/dashboard/packages/${p.pacote_id}/etiqueta.pdf" target="_blank" rel="noopener" title="Baixar PDF da etiqueta (A4)">📄 Etiqueta</a>
               <a class="row-action" href="/api/dashboard/packages/${p.pacote_id}/etiqueta.pdf?fmt=termica" target="_blank" rel="noopener" title="Etiqueta térmica (adesiva, 1 por cliente)">🏷️ Térmica</a>`
            : "";
```

Apply the same pattern (an extra `?fmt=termica` link labeled "🏷️ Térmica") at the other three spots: `dashboard_v2.js:598-599` (use `${p.id}`), `:727` (`${p.pacote_id}`, class `btn-ghost`), and `:825` (`${p.id}`, class `btn-ghost`). Match the surrounding link's classes and id variable at each spot.

- [ ] **Step 2: Validate in the browser**

Run the app locally:

```bash
cd /root/rodrigo/raylook
DASHBOARD_AUTH_DISABLED=true .venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, find a package in "separado"/"enviado", and confirm both links appear: "📄 Etiqueta" (A4) and "🏷️ Térmica". Click "🏷️ Térmica" and confirm a PDF with one small label per client + a QR opens. Scan/visit the QR URL and confirm the "Enviado!" page.

- [ ] **Step 3: Commit**

```bash
git add static/js/dashboard_v2.js
git commit -m "feat: botão de etiqueta térmica no dashboard"
```

---

## Self-Review

**Spec coverage:**
- §4.1 Token assinado → Task 1 ✅
- §4.2 Rota pública `/s/{token}` + feedback + helper compartilhado → Tasks 3 (helper) + 4 (rota) ✅
- §4.3 Geração térmica (template + QR + formato) → Task 2 ✅
- §4.4 Endpoint download `fmt/w/h` + `cliente_id` → Task 5 ✅
- §5 Config (envs `LABEL_QR_SECRET`, `ETIQUETA_TERMICA_W_MM/H_MM`) → lidas via `os.getenv` em Tasks 1/2/5 (sem mexer no pydantic Settings, seguindo o padrão de `auth_service`). ✅
- §2 Coexistência A4 + térmica → Tasks 5 (default a4) + 6 (dois botões) ✅
- §7 Edge cases (token inválido, não pago, 404, reescan idempotente, último cliente promove pacote) → Tasks 3/4 testes ✅
- §9 Testes (token, pdf, rota, integração) → Tasks 1-5 ✅

**Placeholder scan:** nenhum TODO/TBD; todo passo tem código ou comando concreto.

**Type consistency:** `make_ship_token`/`read_ship_token`, `render_label_html`/`build_pdf(formato,w_mm,h_mm)`, `_mark_client_shipped(client,pkg,pc,role)->bool` usados de forma idêntica entre tasks. ✅

**Nota de implementação:** a config das envs ficou via `os.getenv` direto (padrão do `auth_service`), não no pydantic `Settings` — divergência intencional do spec §5 pra evitar mexer na estrutura dupla de `app/config.py`. Comportamento idêntico ao descrito.
