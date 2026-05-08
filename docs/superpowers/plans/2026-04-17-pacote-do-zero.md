# Pacote do Zero — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar fluxo complementar "Pacote do Zero" no dashboard (produto novo + upload de imagem + clientes com qtys livres somando 24) sem quebrar o fluxo existente de pacote manual com enquete.

**Architecture:** Módulo paralelo isolado. Router novo em `app/api/adhoc_packages.py`, serviço novo em `app/services/adhoc_package_service.py`. Reusa `GoogleDriveClient.upload_file`, `SupabaseRestClient` e `run_post_confirmation_effects` sem modificá-los. Enquete "fantasma" com `source='manual'` encaixa no schema atual. Feature flag `ADHOC_PACKAGES_ENABLED` controla registro do router.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, Pillow (validação de imagem), Google Drive API (client existente), Supabase via PostgREST (`SupabaseRestClient`), pytest + TestClient. Frontend: Jinja2 + vanilla JS (sem framework).

**Spec:** `docs/superpowers/specs/2026-04-17-pacote-do-zero-design.md`

---

## Mapeamento de arquivos

### Criar
- `app/api/__init__.py` — pacote novo (pode já existir vazio; se não, criar).
- `app/api/adhoc_packages.py` — APIRouter com 3 endpoints.
- `app/services/adhoc_package_service.py` — lógica de persistência e orquestração.
- `tests/unit/test_adhoc_package_service.py`
- `tests/unit/test_adhoc_packages_api.py`
- `tests/unit/test_adhoc_image_upload.py`
- `tests/unit/test_adhoc_no_regression.py`
- `deploy/migrations/2026-04-17-adhoc-columns.sql` — migration idempotente (ADD COLUMN IF NOT EXISTS).
- `static/js/adhoc_package.js` — código JS novo (separado pra não inchar `dashboard.js`).

### Modificar
- `main.py:283-284` — acrescentar `app.include_router(adhoc_packages.router)` condicional à feature flag.
- `app/routers/customers.py` — adicionar `GET /search` (endpoint leve pro autocomplete).
- `app/config.py` — declarar `ADHOC_PACKAGES_ENABLED: bool = False`.
- `.env.example` — adicionar linha `ADHOC_PACKAGES_ENABLED=false`.
- `templates/index.html` — adicionar step 0 (escolha de fluxo) + markup dos steps 1-3 do fluxo novo. Markup dos steps existentes (`mode=poll`) intocado.
- `templates/index.html` — incluir `<script src="/static/js/adhoc_package.js">` após `dashboard.js`.

### Não modificar
- `main.py:1212-1289` (endpoints `/manual/preview` e `/manual/confirm`) — fluxo existente.
- `app/services/manual_package_service.py` — fluxo existente.
- `integrations/google_drive/__init__.py` — client reusado sem mudanças.
- `app/services/confirmation_pipeline.py` — `run_post_confirmation_effects` reusado.
- `static/js/dashboard.js` — fluxo `mode=poll` existente.

---

## Pré-requisitos de ambiente local

Antes de começar:

1. Repo em `/root/projects/alana` limpo (`git status` sem pendências).
2. `.env` local com:
   - `ADHOC_PACKAGES_ENABLED=true`
   - Credenciais do Supabase **de dev/staging** (nunca do prod durante desenvolvimento).
   - `GOOGLE_DRIVE_CREDENTIALS_FILE` apontando pro `secrets/credentials.json` local.
   - `GOOGLE_DRIVE_FOLDER_ID` da pasta de testes (não a pasta de prod).
3. `pip install -r requirements.txt` + `pip install pillow` (se ainda não estiver).
4. Confirmar com o usuário **qual banco usar em dev** (alana_staging compartilhado OU postgres local via docker-compose). Registrar a escolha como comentário no topo da PR.

---

## Task 1: Feature flag + pacote `app/api/` + endpoint health

**Files:**
- Create: `app/api/__init__.py` (vazio, só pra virar pacote)
- Create: `app/api/adhoc_packages.py`
- Create: `tests/unit/test_adhoc_packages_api.py`
- Modify: `app/config.py` (adicionar setting)
- Modify: `main.py:283-284` (adicionar include_router condicional)
- Modify: `.env.example` (adicionar flag)

- [ ] **Step 1: Escrever teste do endpoint de health com flag ON**

Conteúdo de `tests/unit/test_adhoc_packages_api.py`:

```python
from fastapi.testclient import TestClient


def test_health_returns_ok_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    # Recarrega main pra pegar a flag ligada
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    client = TestClient(main_module.app)
    response = client.get("/api/packages/adhoc/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_404_when_flag_disabled(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "false")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    client = TestClient(main_module.app)
    response = client.get("/api/packages/adhoc/health")
    assert response.status_code == 404
```

- [ ] **Step 2: Rodar teste pra garantir que falha**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: ambos falham (módulo `app.api.adhoc_packages` não existe).

- [ ] **Step 3: Criar pacote `app/api/`**

```bash
touch /root/projects/alana/app/api/__init__.py
```

- [ ] **Step 4: Adicionar setting em `app/config.py`**

Abrir `app/config.py` e, junto das outras settings booleanas, acrescentar:

```python
ADHOC_PACKAGES_ENABLED: bool = False
```

Se a classe `Settings` usar `BaseSettings` do pydantic, essa linha já carrega da env.

- [ ] **Step 5: Criar `app/api/adhoc_packages.py`**

```python
"""API router para criação de pacote do zero (adhoc)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/packages/adhoc")


@router.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 6: Registrar router condicional em `main.py`**

Abrir `main.py:283-284`, logo depois de `app.include_router(portal_router.router)`, adicionar:

```python
if settings.ADHOC_PACKAGES_ENABLED:
    from app.api import adhoc_packages as adhoc_packages_api
    app.include_router(adhoc_packages_api.router)
```

- [ ] **Step 7: Adicionar flag em `.env.example`**

No final do arquivo, bloco novo:

```
# Habilita endpoints de criação de pacote do zero (adhoc). Default: false.
ADHOC_PACKAGES_ENABLED=false
```

- [ ] **Step 8: Rodar testes e confirmar PASS**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: ambos passam.

- [ ] **Step 9: Commit**

```bash
cd /root/projects/alana
git add app/api/__init__.py app/api/adhoc_packages.py app/config.py main.py .env.example tests/unit/test_adhoc_packages_api.py
git commit -m "feat(adhoc): esqueleto do router com feature flag"
```

---

## Task 2: Migration idempotente de colunas

**Files:**
- Create: `deploy/migrations/2026-04-17-adhoc-columns.sql`

- [ ] **Step 1: Conferir colunas já existentes no banco de dev**

Conecte no banco Postgres usado em dev e rode:

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name IN ('enquetes','produtos','pacotes','votos')
  AND column_name IN ('source','created_via','synthetic');
```

Anote quais já existem. A migration só deve criar as que faltam (usa `IF NOT EXISTS`).

- [ ] **Step 2: Escrever a migration idempotente**

Conteúdo de `deploy/migrations/2026-04-17-adhoc-columns.sql`:

```sql
BEGIN;

ALTER TABLE enquetes
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'whapi';

ALTER TABLE produtos
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'whapi';

ALTER TABLE pacotes
  ADD COLUMN IF NOT EXISTS created_via TEXT NOT NULL DEFAULT 'poll';

ALTER TABLE votos
  ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;

-- Índices pra filtros comuns de listagem
CREATE INDEX IF NOT EXISTS idx_enquetes_source ON enquetes(source);
CREATE INDEX IF NOT EXISTS idx_produtos_source ON produtos(source);
CREATE INDEX IF NOT EXISTS idx_pacotes_created_via ON pacotes(created_via);

COMMIT;
```

- [ ] **Step 3: Aplicar migration no banco local**

```bash
psql "$POSTGRES_URL_LOCAL" -f /root/projects/alana/deploy/migrations/2026-04-17-adhoc-columns.sql
```

Esperado: `BEGIN / ALTER TABLE / ... / COMMIT` sem erro. Rodar uma segunda vez deve ser no-op (idempotência).

- [ ] **Step 4: Conferir que colunas existem**

Mesma query do Step 1 — agora deve retornar as 4 colunas.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add deploy/migrations/2026-04-17-adhoc-columns.sql
git commit -m "feat(adhoc): migration idempotente para colunas source/created_via/synthetic"
```

---

## Task 3: Endpoint de upload de imagem

**Files:**
- Create: `tests/unit/test_adhoc_image_upload.py`
- Modify: `app/api/adhoc_packages.py` (adicionar endpoint)

- [ ] **Step 1: Escrever testes de upload**

Conteúdo de `tests/unit/test_adhoc_image_upload.py`:

```python
import io
from fastapi.testclient import TestClient


def _boot_app(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)
    return main_module.app


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    return buf.getvalue()


def test_upload_accepts_png(monkeypatch):
    app = _boot_app(monkeypatch)

    captured = {}

    class FakeDriveClient:
        def __init__(self, *a, **kw):
            pass
        def upload_file(self, name, content_bytes, parent_folder_id, mime_type="image/jpeg"):
            captured["name"] = name
            captured["mime"] = mime_type
            captured["size"] = len(content_bytes)
            return "FAKE_DRIVE_ID"
        def get_public_url(self, file_id):
            return f"https://lh3.googleusercontent.com/d/{file_id}"

    monkeypatch.setattr("app.api.adhoc_packages.GoogleDriveClient", FakeDriveClient)

    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("product.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["drive_file_id"] == "FAKE_DRIVE_ID"
    assert body["full_url"].endswith("FAKE_DRIVE_ID")
    assert captured["mime"] == "image/png"


def test_upload_rejects_unsupported_mime(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 415


def test_upload_rejects_oversized(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    big = b"\x00" * (6 * 1024 * 1024)  # 6MB
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("big.png", big, "image/png")},
    )
    assert response.status_code == 413


def test_upload_rejects_corrupt_image(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("fake.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_image_upload.py -v
```

Esperado: todos falham (endpoint não existe).

- [ ] **Step 3: Implementar endpoint em `app/api/adhoc_packages.py`**

Substituir o conteúdo do arquivo por:

```python
"""API router para criação de pacote do zero (adhoc)."""
import io
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.config import settings
from integrations.google_drive import GoogleDriveClient

logger = logging.getLogger("alana.adhoc")

router = APIRouter(prefix="/api/packages/adhoc")

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB


class UploadImageResponse(BaseModel):
    drive_file_id: str
    full_url: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/upload-image", response_model=UploadImageResponse)
async def upload_image(image: UploadFile = File(...)):
    if image.content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Formato não suportado. Use: {', '.join(sorted(ALLOWED_MIME))}.",
        )

    content = await image.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Imagem acima de 5MB.")

    # Pillow valida conteúdo (rejeita arquivo corrompido / extensão enganosa).
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Imagem inválida ou corrompida.") from exc

    safe_name = (image.filename or "adhoc.png").replace("/", "_").replace("\\", "_")[:100]

    drive = GoogleDriveClient()
    try:
        file_id = drive.upload_file(
            safe_name,
            content,
            parent_folder_id=settings.GOOGLE_DRIVE_FOLDER_ID,
            mime_type=image.content_type,
        )
    except Exception as exc:
        logger.exception("adhoc upload_image: falha no Drive")
        raise HTTPException(status_code=502, detail="Falha ao enviar pro Google Drive.") from exc

    return UploadImageResponse(drive_file_id=file_id, full_url=drive.get_public_url(file_id))
```

- [ ] **Step 4: Rodar testes e confirmar PASS**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_image_upload.py -v
```

Esperado: 4 passam.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add app/api/adhoc_packages.py tests/unit/test_adhoc_image_upload.py
git commit -m "feat(adhoc): endpoint POST /upload-image com validação Pillow"
```

---

## Task 4: Endpoint de busca de clientes (autocomplete)

**Files:**
- Modify: `app/routers/customers.py` (adicionar `GET /search`)
- Modify: `app/services/customer_service.py` (adicionar `search_customers_light`, se não existir equivalente) — **verificar primeiro** se já existe função apropriada e reusar.

- [ ] **Step 1: Ler `app/services/customer_service.py` e conferir se já existe busca leve**

```bash
grep -n "def search\|def list_customer_rows_page\|def find_customer" /root/projects/alana/app/services/customer_service.py
```

Se existir algo apropriado que retorne `[{phone, name}, ...]` com limite, reusar. Caso contrário, seguir Step 2.

- [ ] **Step 2: Escrever teste do endpoint**

Conteúdo de `tests/unit/test_customers_router.py` (anexar ao final, sem alterar testes existentes):

```python
def test_customers_search_returns_light_payload(monkeypatch):
    def fake_search(q, limit):
        assert q == "mar"
        assert limit == 10
        return [
            {"phone": "5511999999999", "name": "Maria Silva"},
            {"phone": "5511988887777", "name": "Marcos"},
        ]

    monkeypatch.setattr("app.routers.customers.search_customers_light", fake_search)

    from fastapi.testclient import TestClient
    import main as main_module
    client = TestClient(main_module.app)
    response = client.get("/api/customers/search?q=mar")

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {"phone": "5511999999999", "name": "Maria Silva"},
            {"phone": "5511988887777", "name": "Marcos"},
        ]
    }
```

- [ ] **Step 3: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_customers_router.py::test_customers_search_returns_light_payload -v
```

Esperado: falha (endpoint inexistente).

- [ ] **Step 4: Implementar `search_customers_light` em `app/services/customer_service.py`**

Se não existir equivalente, adicionar ao final do arquivo:

```python
def search_customers_light(q: str, limit: int = 10) -> list[dict]:
    """Busca clientes por nome OU telefone, retorna lista leve [{phone, name}]."""
    q_norm = (q or "").strip().lower()
    if not q_norm or len(q_norm) < 2:
        return []

    from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
    if not supabase_domain_enabled():
        return []

    client = SupabaseRestClient.from_settings()
    # Busca por celular (LIKE) ou nome (ILIKE) — OR via PostgREST `or=`
    digits = "".join(filter(str.isdigit, q))
    filters = []
    if digits:
        filters.append(f"celular.ilike.*{digits}*")
    filters.append(f"nome.ilike.*{q_norm}*")
    rows = client.select(
        "clientes",
        columns="celular,nome",
        or_filter=",".join(filters),
        limit=limit,
    ) or []
    return [{"phone": r.get("celular"), "name": r.get("nome")} for r in rows if r.get("celular")]
```

**Nota:** se o `SupabaseRestClient.select` não aceita `or_filter`, procure o método apropriado no módulo (`grep -n "def select\|or_filter\|or=" /root/projects/alana/app/services/supabase_service.py`) e adapte. Se nenhum método suporta OR, chame duas vezes (por nome e por celular) e mescle deduplicando por `celular`.

- [ ] **Step 5: Adicionar endpoint em `app/routers/customers.py`**

No final do arquivo, antes da última linha em branco:

```python
from app.services.customer_service import search_customers_light


@router.get("/search")
async def search(q: str = "", limit: int = 10):
    return {"results": search_customers_light(q, limit=min(max(limit, 1), 25))}
```

- [ ] **Step 6: Rodar testes**

```bash
cd /root/projects/alana && pytest tests/unit/test_customers_router.py -v
```

Esperado: todos passam (inclui os 2 testes pré-existentes + 1 novo).

- [ ] **Step 7: Commit**

```bash
cd /root/projects/alana
git add app/services/customer_service.py app/routers/customers.py tests/unit/test_customers_router.py
git commit -m "feat(adhoc): endpoint GET /api/customers/search pro autocomplete"
```

---

## Task 5: Serviço — criar enquete fantasma e produto novo

**Files:**
- Create: `app/services/adhoc_package_service.py`
- Create: `tests/unit/test_adhoc_package_service.py`

- [ ] **Step 1: Escrever teste que cria produto + enquete fantasma**

Conteúdo inicial de `tests/unit/test_adhoc_package_service.py`:

```python
from unittest.mock import MagicMock


def test_create_phantom_poll_and_product_inserts_both(monkeypatch):
    from app.services import adhoc_package_service

    fake_client = MagicMock()
    # insert() retorna lista com dict inserido
    fake_client.insert.side_effect = [
        [{"id": "PROD-1", "nome": "Vestido Floral"}],
        [{"id": "POLL-1", "titulo": "Pacote manual — Vestido Floral — 2026-04-17"}],
    ]

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake_client)),
    )

    produto_id, enquete_id = adhoc_package_service.create_phantom_poll_and_product(
        product_name="Vestido Floral",
        unit_price=45.00,
        drive_file_id="DRIVE-1",
    )

    assert produto_id == "PROD-1"
    assert enquete_id == "POLL-1"
    # Primeira chamada: produtos
    args_prod = fake_client.insert.call_args_list[0]
    assert args_prod.args[0] == "produtos"
    assert args_prod.args[1]["nome"] == "Vestido Floral"
    assert args_prod.args[1]["valor_unitario"] == 45.00
    assert args_prod.args[1]["drive_file_id"] == "DRIVE-1"
    assert args_prod.args[1]["source"] == "manual"
    # Segunda: enquetes
    args_poll = fake_client.insert.call_args_list[1]
    assert args_poll.args[0] == "enquetes"
    assert args_poll.args[1]["source"] == "manual"
    assert args_poll.args[1]["produto_id"] == "PROD-1"
    assert args_poll.args[1]["drive_file_id"] == "DRIVE-1"
    assert args_poll.args[1]["external_poll_id"] is None
```

- [ ] **Step 2: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_package_service.py::test_create_phantom_poll_and_product_inserts_both -v
```

Esperado: falha (módulo não existe).

- [ ] **Step 3: Criar `app/services/adhoc_package_service.py` com a função**

```python
"""Criação de pacote do zero (adhoc) — sem depender de enquete WHAPI."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("alana.adhoc_package")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _phantom_poll_title(product_name: str) -> str:
    return f"Pacote manual — {product_name} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def create_phantom_poll_and_product(
    product_name: str,
    unit_price: float,
    drive_file_id: str,
) -> Tuple[str, str]:
    """Insere produto novo e enquete fantasma. Retorna (produto_id, enquete_id)."""
    client = SupabaseRestClient.from_settings()

    produto = client.insert(
        "produtos",
        {
            "nome": product_name,
            "valor_unitario": unit_price,
            "drive_file_id": drive_file_id,
            "source": "manual",
        },
    )[0]
    produto_id = produto["id"]

    enquete = client.insert(
        "enquetes",
        {
            "titulo": _phantom_poll_title(product_name),
            "produto_id": produto_id,
            "drive_file_id": drive_file_id,
            "source": "manual",
            "external_poll_id": None,
            "created_at_provider": _now_iso(),
        },
    )[0]
    enquete_id = enquete["id"]

    logger.info(
        "adhoc: fantasma criada produto_id=%s enquete_id=%s", produto_id, enquete_id
    )
    return produto_id, enquete_id
```

- [ ] **Step 4: Rodar teste pra confirmar PASS**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_package_service.py -v
```

Esperado: passa.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add app/services/adhoc_package_service.py tests/unit/test_adhoc_package_service.py
git commit -m "feat(adhoc): service — criação de produto + enquete fantasma"
```

---

## Task 6: Serviço — criar pacote adhoc completo (persistência end-to-end)

**Files:**
- Modify: `app/services/adhoc_package_service.py`
- Modify: `tests/unit/test_adhoc_package_service.py`

- [ ] **Step 1: Adicionar teste de criação completa**

Anexar em `tests/unit/test_adhoc_package_service.py`:

```python
def test_create_adhoc_package_persists_pacote_votos_pacote_clientes(monkeypatch):
    from app.services import adhoc_package_service

    fake_client = MagicMock()
    inserted = []

    def fake_insert(table, payload, **kwargs):
        inserted.append((table, dict(payload)))
        if table == "produtos":
            return [{"id": "PROD-1"}]
        if table == "enquetes":
            return [{"id": "POLL-1"}]
        if table == "pacotes":
            return [{"id": "PKG-1"}]
        if table == "votos":
            return [{"id": f"VOTO-{len([t for t,_ in inserted if t=='votos'])}"}]
        if table == "pacote_clientes":
            return [{"id": "PC-1"}]
        return [{}]

    fake_client.insert.side_effect = fake_insert

    def fake_upsert_one(table, payload, on_conflict=None):
        if table == "clientes":
            return {"id": f"CLI-{payload['celular']}", **payload}
        return payload

    fake_client.upsert_one.side_effect = fake_upsert_one

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake_client)),
    )

    # Evita disparar pipeline real nesse teste unitário
    monkeypatch.setattr(
        adhoc_package_service, "run_post_confirmation_effects", MagicMock()
    )

    result = adhoc_package_service.create_adhoc_package(
        product_name="Vestido Floral",
        unit_price=45.00,
        drive_file_id="DRIVE-1",
        votes=[
            {"phone": "5511999999999", "qty": 10, "customer_id": None, "name": "Maria"},
            {"phone": "5511988887777", "qty": 14, "customer_id": None, "name": "João"},
        ],
    )

    assert result["package_id"] == "PKG-1"
    tables = [t for t, _ in inserted]
    assert tables.count("produtos") == 1
    assert tables.count("enquetes") == 1
    assert tables.count("pacotes") == 1
    assert tables.count("votos") == 2
    assert tables.count("pacote_clientes") == 2

    # Pacote aponta pra enquete fantasma e marca created_via
    pacote_payload = next(p for t, p in inserted if t == "pacotes")
    assert pacote_payload["enquete_id"] == "POLL-1"
    assert pacote_payload["created_via"] == "adhoc"
    assert pacote_payload["total_qty"] == 24

    # Votos sintéticos marcados
    voto_payloads = [p for t, p in inserted if t == "votos"]
    assert all(v["synthetic"] is True for v in voto_payloads)

    # Cálculo de subtotal e comissão
    pc_payloads = [p for t, p in inserted if t == "pacote_clientes"]
    pc_by_qty = {p["qty"]: p for p in pc_payloads}
    # 10 × 45 = 450; comissão 13% = 58.50; total = 508.50
    assert pc_by_qty[10]["subtotal"] == 450.0
    assert pc_by_qty[10]["commission_amount"] == 58.5
    assert pc_by_qty[10]["total_amount"] == 508.5


def test_create_adhoc_package_rejects_sum_not_24(monkeypatch):
    from app.services import adhoc_package_service

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=MagicMock())),
    )

    import pytest
    with pytest.raises(ValueError, match="24"):
        adhoc_package_service.create_adhoc_package(
            product_name="X",
            unit_price=10.0,
            drive_file_id="D",
            votes=[{"phone": "5511999999999", "qty": 5}],
        )
```

- [ ] **Step 2: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_package_service.py -v
```

Esperado: 2 novos falham (função `create_adhoc_package` não existe).

- [ ] **Step 3: Implementar `create_adhoc_package` em `app/services/adhoc_package_service.py`**

Adicionar ao final do arquivo:

```python
from app.services.confirmation_pipeline import run_post_confirmation_effects


def _clean_phone(phone: Any) -> str:
    return "".join(filter(str.isdigit, str(phone or "")))


def _total_qty(votes: List[Dict[str, Any]]) -> int:
    return sum(int(v.get("qty") or 0) for v in votes)


def create_adhoc_package(
    *,
    product_name: str,
    unit_price: float,
    drive_file_id: str,
    votes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fluxo completo: produto fantasma → enquete fantasma → pacote → votos sintéticos → pacote_clientes → pós-confirmação."""
    total = _total_qty(votes)
    if total != 24:
        raise ValueError(f"Pacote deve ter exatamente 24 peças, recebeu {total}.")

    client = SupabaseRestClient.from_settings()
    produto_id, enquete_id = create_phantom_poll_and_product(
        product_name=product_name,
        unit_price=unit_price,
        drive_file_id=drive_file_id,
    )

    now_iso = _now_iso()
    pacote = client.insert(
        "pacotes",
        {
            "enquete_id": enquete_id,
            "sequence_no": 1,
            "capacidade_total": 24,
            "total_qty": 24,
            "participants_count": len(votes),
            "status": "closed",
            "opened_at": now_iso,
            "closed_at": now_iso,
            "created_via": "adhoc",
        },
    )[0]
    pacote_id = pacote["id"]

    commission_pct = float(settings.COMMISSION_PERCENT)

    for v in votes:
        phone = _clean_phone(v.get("phone"))
        qty = int(v.get("qty") or 0)
        name = (v.get("name") or phone).strip()

        customer = client.upsert_one(
            "clientes",
            {"celular": phone, "nome": name},
            on_conflict="celular",
        )
        cliente_id = customer["id"]

        voto = client.insert(
            "votos",
            {
                "enquete_id": enquete_id,
                "cliente_id": cliente_id,
                "qty": qty,
                "status": "in",
                "voted_at": now_iso,
                "synthetic": True,
            },
        )[0]

        subtotal = round(unit_price * qty, 2)
        commission_amount = round(subtotal * (commission_pct / 100), 2)
        total_amount = round(subtotal + commission_amount, 2)

        client.insert(
            "pacote_clientes",
            {
                "pacote_id": pacote_id,
                "cliente_id": cliente_id,
                "voto_id": voto["id"],
                "produto_id": produto_id,
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "commission_percent": commission_pct,
                "commission_amount": commission_amount,
                "total_amount": total_amount,
                "status": "closed",
            },
        )

    legacy_package_id = f"adhoc_{pacote_id}"
    package_dict = {
        "id": legacy_package_id,
        "poll_title": _phantom_poll_title(product_name),
        "valor_col": unit_price,
        "qty": 24,
        "status": "confirmed",
        "manual_creation": True,
        "created_via": "adhoc",
        "confirmed_at": now_iso,
        "closed_at": now_iso,
        "votes": votes,
    }
    try:
        import asyncio
        asyncio.run(run_post_confirmation_effects(package_dict, legacy_package_id, metrics_data_to_save=None))
    except Exception:
        logger.exception("adhoc: pipeline pós-confirmação falhou (pacote persistido, efeitos podem ser retentados)")

    return {"package_id": str(pacote_id), "legacy_package_id": legacy_package_id}
```

**Nota sobre RuntimeError do asyncio.run:** se o caller já estiver em event loop (caso do FastAPI), `asyncio.run` vai quebrar. Nesse caso, o serviço é chamado via `asyncio.to_thread(create_adhoc_package, ...)` no endpoint (Task 8), isolando o contexto. Se isso não funcionar no teste manual, trocar por `asyncio.get_event_loop().run_until_complete` com proteção.

- [ ] **Step 4: Rodar testes**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_package_service.py -v
```

Esperado: todos passam (inclui os 3 testes dessa task + os 1 da anterior).

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add app/services/adhoc_package_service.py tests/unit/test_adhoc_package_service.py
git commit -m "feat(adhoc): service — create_adhoc_package end-to-end"
```

---

## Task 7: Endpoint `POST /preview`

**Files:**
- Modify: `app/api/adhoc_packages.py` (adicionar modelos e endpoint)
- Modify: `tests/unit/test_adhoc_packages_api.py`

- [ ] **Step 1: Escrever teste do preview**

Anexar em `tests/unit/test_adhoc_packages_api.py`:

```python
import re


def _preview_body():
    return {
        "product": {"name": "Vestido Floral", "unit_price": 45.00, "image": {"drive_file_id": "D1"}},
        "votes": [
            {"phone": "5511999999999", "qty": 10, "customer_id": None},
            {"phone": "5511988887777", "qty": 14, "customer_id": None},
        ],
    }


def test_preview_returns_totals(monkeypatch):
    app = _boot_app(monkeypatch)

    class FakeClient:
        def select(self, table, **kw):
            return [{"celular": "5511999999999", "nome": "Maria"}]

    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "app.api.adhoc_packages.SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=FakeClient())),
    )

    from fastapi.testclient import TestClient
    response = TestClient(app).post("/api/packages/adhoc/preview", json=_preview_body())
    assert response.status_code == 200
    body = response.json()
    assert body["total_qty"] == 24
    assert body["subtotal"] == 24 * 45.00
    # comissão 13% — valor exato vem de settings.COMMISSION_PERCENT
    assert body["commission_percent"] > 0
    assert body["total_final"] == round(body["subtotal"] + body["commission_amount"], 2)
    assert len(body["votes_resolved"]) == 2


def test_preview_rejects_sum_not_24(monkeypatch):
    app = _boot_app(monkeypatch)
    from fastapi.testclient import TestClient
    body = _preview_body()
    body["votes"][0]["qty"] = 1  # soma = 15
    response = TestClient(app).post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 400
    assert "24" in response.json()["detail"]


def test_preview_rejects_bad_phone(monkeypatch):
    app = _boot_app(monkeypatch)
    from fastapi.testclient import TestClient
    body = _preview_body()
    body["votes"][0]["phone"] = "123"
    response = TestClient(app).post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 422
```

Também precisa importar `_boot_app` no topo do arquivo — já está, do teste da Task 1.

- [ ] **Step 2: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: 3 novos falham.

- [ ] **Step 3: Adicionar modelos e endpoint em `app/api/adhoc_packages.py`**

Anexar ao arquivo:

```python
import re
from typing import List
from pydantic import Field, field_validator

from app.services.supabase_service import SupabaseRestClient
from app.config import settings as app_settings

PHONE_BR_RE = re.compile(r"^55\d{10,11}$")


class ProductDraft(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    unit_price: float = Field(..., gt=0, le=10000)
    image: dict = Field(...)

    @field_validator("image")
    @classmethod
    def image_has_drive_id(cls, v):
        if not isinstance(v, dict) or not v.get("drive_file_id"):
            raise ValueError("image.drive_file_id obrigatório.")
        return v


class VoteLineAdhoc(BaseModel):
    phone: str
    qty: int = Field(ge=1, le=24)
    customer_id: Optional[str] = None
    name: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", (v or "").strip())
        if not PHONE_BR_RE.match(digits):
            raise ValueError("Celular deve estar no formato 55 + DDD + número (10 ou 11 dígitos).")
        return digits


class AdhocPackageRequest(BaseModel):
    product: ProductDraft
    votes: List[VoteLineAdhoc] = Field(..., min_length=1, max_length=24)


def _resolve_vote_names(votes: List[VoteLineAdhoc]) -> List[dict]:
    """Busca nomes cadastrados pra cada phone — mostra 'nome (cadastrado)' no preview."""
    client = SupabaseRestClient.from_settings()
    phones = [v.phone for v in votes]
    rows = client.select(
        "clientes",
        columns="celular,nome",
        filters=[("celular", "in", f"({','.join(phones)})")],
    ) or []
    name_by_phone = {r["celular"]: r.get("nome") for r in rows}
    return [
        {"phone": v.phone, "name": v.name or name_by_phone.get(v.phone) or "", "qty": v.qty}
        for v in votes
    ]


@router.post("/preview")
async def preview(body: AdhocPackageRequest):
    total = sum(v.qty for v in body.votes)
    if total != 24:
        raise HTTPException(status_code=400, detail="O pacote precisa ter exatamente 24 peças.")
    votes_resolved = _resolve_vote_names(body.votes)
    subtotal = round(body.product.unit_price * 24, 2)
    commission_pct = float(app_settings.COMMISSION_PERCENT)
    commission_amount = round(subtotal * (commission_pct / 100), 2)
    total_final = round(subtotal + commission_amount, 2)
    return {
        "total_qty": total,
        "subtotal": subtotal,
        "commission_percent": commission_pct,
        "commission_amount": commission_amount,
        "total_final": total_final,
        "votes_resolved": votes_resolved,
        "product": {
            "name": body.product.name,
            "unit_price": body.product.unit_price,
            "drive_file_id": body.product.image["drive_file_id"],
        },
    }
```

- [ ] **Step 4: Rodar testes**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: todos passam.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add app/api/adhoc_packages.py tests/unit/test_adhoc_packages_api.py
git commit -m "feat(adhoc): endpoint POST /preview com validações"
```

---

## Task 8: Endpoint `POST /confirm`

**Files:**
- Modify: `app/api/adhoc_packages.py`
- Modify: `tests/unit/test_adhoc_packages_api.py`

- [ ] **Step 1: Escrever teste do confirm**

Anexar em `tests/unit/test_adhoc_packages_api.py`:

```python
def test_confirm_persists_and_returns_package_id(monkeypatch):
    app = _boot_app(monkeypatch)
    from unittest.mock import MagicMock

    fake_service = MagicMock(return_value={"package_id": "PKG-1", "legacy_package_id": "adhoc_PKG-1"})
    monkeypatch.setattr("app.api.adhoc_packages.create_adhoc_package", fake_service)

    from fastapi.testclient import TestClient
    response = TestClient(app).post("/api/packages/adhoc/confirm", json=_preview_body())

    assert response.status_code == 200
    assert response.json()["package_id"] == "PKG-1"
    fake_service.assert_called_once()
    kwargs = fake_service.call_args.kwargs
    assert kwargs["product_name"] == "Vestido Floral"
    assert kwargs["unit_price"] == 45.00
    assert kwargs["drive_file_id"] == "D1"
    assert len(kwargs["votes"]) == 2


def test_confirm_rejects_sum_not_24(monkeypatch):
    app = _boot_app(monkeypatch)
    from fastapi.testclient import TestClient
    body = _preview_body()
    body["votes"][0]["qty"] = 1
    response = TestClient(app).post("/api/packages/adhoc/confirm", json=body)
    assert response.status_code == 400
```

- [ ] **Step 2: Rodar pra ver falhar**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: 2 novos falham.

- [ ] **Step 3: Adicionar endpoint em `app/api/adhoc_packages.py`**

Anexar:

```python
import asyncio
from app.services.adhoc_package_service import create_adhoc_package


@router.post("/confirm")
async def confirm(body: AdhocPackageRequest):
    total = sum(v.qty for v in body.votes)
    if total != 24:
        raise HTTPException(status_code=400, detail="O pacote precisa ter exatamente 24 peças.")

    try:
        result = await asyncio.to_thread(
            create_adhoc_package,
            product_name=body.product.name,
            unit_price=body.product.unit_price,
            drive_file_id=body.product.image["drive_file_id"],
            votes=[v.model_dump() for v in body.votes],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("adhoc confirm: falha ao criar pacote")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result
```

- [ ] **Step 4: Rodar testes**

```bash
cd /root/projects/alana && pytest tests/unit/test_adhoc_packages_api.py -v
```

Esperado: todos passam.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add app/api/adhoc_packages.py tests/unit/test_adhoc_packages_api.py
git commit -m "feat(adhoc): endpoint POST /confirm via asyncio.to_thread"
```

---

## Task 9: Frontend — step 0 no modal + markup novo

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Localizar markup do modal de criar pacote existente**

```bash
grep -n "Criar Pacote\|modal-pacote\|criar-pacote" /root/projects/alana/templates/index.html | head -20
```

Anote a linha de abertura do modal.

- [ ] **Step 2: Adicionar step 0 (escolha de fluxo)**

Dentro do modal, antes do markup do step atual, inserir:

```html
<div id="adhoc-step-choose" class="modal-step" style="display:none;">
  <h3>Como você quer criar esse pacote?</h3>
  <div class="choice-cards">
    <button type="button" class="choice-card" data-mode="poll">
      <span class="choice-icon">📊</span>
      <span class="choice-title">A partir de enquete</span>
      <span class="choice-sub">Usa enquete do WhatsApp das últimas 72h.</span>
    </button>
    <button type="button" class="choice-card" data-mode="adhoc">
      <span class="choice-icon">✨</span>
      <span class="choice-title">Pacote do zero</span>
      <span class="choice-sub">Produto novo + imagem + clientes manualmente.</span>
    </button>
  </div>
</div>
```

E na lógica JS que abre o modal (`dashboard.js` — localizar função tipo `openCreatePackageModal`), começar mostrando `#adhoc-step-choose` em vez de ir direto pro step atual. Esse ajuste mínimo no `dashboard.js` é o ÚNICO que mexe no fluxo existente — documentar isso no commit.

- [ ] **Step 3: Adicionar markup dos steps do fluxo adhoc**

Logo após o step 0, adicionar containers vazios (preenchidos por `adhoc_package.js`):

```html
<div id="adhoc-step-product" class="modal-step" style="display:none;"></div>
<div id="adhoc-step-votes" class="modal-step" style="display:none;"></div>
<div id="adhoc-step-preview" class="modal-step" style="display:none;"></div>
```

- [ ] **Step 4: Incluir `adhoc_package.js`**

Antes de `</body>`, depois do script `dashboard.js`:

```html
<script src="/static/js/adhoc_package.js"></script>
```

- [ ] **Step 5: Validar que a página ainda renderiza sem erro**

```bash
cd /root/projects/alana && python -c "from fastapi.testclient import TestClient; import main; print(TestClient(main.app).get('/').status_code)"
```

Esperado: 200 ou 302 (redirect de login), não 500.

- [ ] **Step 6: Commit**

```bash
cd /root/projects/alana
git add templates/index.html
git commit -m "feat(adhoc): step 0 de escolha no modal + containers novos"
```

---

## Task 10: Frontend — lógica JS do fluxo adhoc

**Files:**
- Create: `static/js/adhoc_package.js`
- Modify: `static/js/dashboard.js` — **mínimo**: hook pra acionar step 0 ao abrir modal.

- [ ] **Step 1: Localizar ponto de hook em `dashboard.js`**

```bash
grep -n "function.*[Oo]pen.*[Pp]acote\|openCreatePackageModal\|showModal.*pacote\|addEventListener.*criar" /root/projects/alana/static/js/dashboard.js | head -10
```

- [ ] **Step 2: Adicionar hook mínimo em `dashboard.js`**

Na função que abre o modal, logo no início, dispachar um evento custom:

```javascript
// Dispara evento pro módulo adhoc interceptar a abertura do modal.
document.dispatchEvent(new CustomEvent('adhoc:modal-open'));
```

O restante do fluxo existente permanece.

- [ ] **Step 3: Criar `static/js/adhoc_package.js` com estado, step 0 e step 1**

```javascript
(function () {
  'use strict';

  const STEPS = {
    CHOOSE: 'adhoc-step-choose',
    PRODUCT: 'adhoc-step-product',
    VOTES: 'adhoc-step-votes',
    PREVIEW: 'adhoc-step-preview',
  };

  const state = {
    mode: null,
    product: { name: '', unit_price: 0, drive_file_id: null, full_url: null },
    votes: [],
    preview: null,
  };

  function hideAll() {
    Object.values(STEPS).forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    // Esconde também os steps do fluxo enquete (mesmo seletor que dashboard.js usa)
    document.querySelectorAll('.modal-step-poll').forEach((el) => { el.style.display = 'none'; });
  }

  function show(stepId) {
    hideAll();
    const el = document.getElementById(stepId);
    if (el) el.style.display = 'block';
  }

  function goChoose() { show(STEPS.CHOOSE); }

  document.addEventListener('adhoc:modal-open', goChoose);

  // Clique nos cards de escolha
  document.addEventListener('click', (e) => {
    const card = e.target.closest('.choice-card');
    if (!card) return;
    const mode = card.dataset.mode;
    state.mode = mode;
    if (mode === 'adhoc') {
      renderProductStep();
    } else {
      // Devolve controle pro fluxo antigo
      hideAll();
      document.dispatchEvent(new CustomEvent('adhoc:fallback-to-poll'));
    }
  });

  function renderProductStep() {
    const root = document.getElementById(STEPS.PRODUCT);
    root.innerHTML = `
      <h3>Produto novo</h3>
      <label>Nome <input id="adhoc-product-name" maxlength="120" required></label>
      <label>Preço por peça (sem comissão)
        <input id="adhoc-product-price" type="number" step="0.01" min="0.01" required>
      </label>
      <div id="adhoc-price-calc" class="muted"></div>
      <label>Imagem
        <input id="adhoc-product-image" type="file" accept="image/jpeg,image/png,image/webp" required>
      </label>
      <div id="adhoc-image-preview"></div>
      <div class="modal-actions">
        <button type="button" id="adhoc-back-to-choose">Voltar</button>
        <button type="button" id="adhoc-next-to-votes" disabled>Próximo</button>
      </div>
    `;
    show(STEPS.PRODUCT);

    const nameEl = document.getElementById('adhoc-product-name');
    const priceEl = document.getElementById('adhoc-product-price');
    const imgEl = document.getElementById('adhoc-product-image');
    const calcEl = document.getElementById('adhoc-price-calc');
    const nextEl = document.getElementById('adhoc-next-to-votes');

    function recalc() {
      const price = parseFloat(priceEl.value || '0');
      if (price > 0) {
        const subtotal = price * 24;
        const commission = subtotal * 0.13;
        calcEl.textContent = `Total do pacote: R$ ${subtotal.toFixed(2)} (24 × ${price.toFixed(2)}) + comissão 13% = R$ ${(subtotal + commission).toFixed(2)}`;
      } else {
        calcEl.textContent = '';
      }
      nextEl.disabled = !(
        nameEl.value.trim().length >= 3 &&
        price > 0 &&
        state.product.drive_file_id
      );
    }

    nameEl.addEventListener('input', () => { state.product.name = nameEl.value.trim(); recalc(); });
    priceEl.addEventListener('input', () => { state.product.unit_price = parseFloat(priceEl.value || '0'); recalc(); });

    imgEl.addEventListener('change', async () => {
      const file = imgEl.files && imgEl.files[0];
      if (!file) return;
      const previewEl = document.getElementById('adhoc-image-preview');
      previewEl.textContent = 'Enviando…';
      const fd = new FormData();
      fd.append('image', file);
      try {
        const resp = await fetch('/api/packages/adhoc/upload-image', { method: 'POST', body: fd });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        state.product.drive_file_id = data.drive_file_id;
        state.product.full_url = data.full_url;
        previewEl.innerHTML = `<img src="${data.full_url}" alt="" style="max-width:200px"> ✓`;
        recalc();
      } catch (err) {
        previewEl.innerHTML = `<span class="error">Falha: ${err.message}. <button type="button" onclick="document.getElementById('adhoc-product-image').click()">Tentar de novo</button></span>`;
      }
    });

    document.getElementById('adhoc-back-to-choose').addEventListener('click', goChoose);
    document.getElementById('adhoc-next-to-votes').addEventListener('click', renderVotesStep);
  }

  function renderVotesStep() {
    // Implementado na Task 11 — placeholder por enquanto
    const root = document.getElementById(STEPS.VOTES);
    root.innerHTML = '<p>Step 2 — clientes (implementação na Task 11).</p>';
    show(STEPS.VOTES);
  }
})();
```

- [ ] **Step 4: Abrir navegador em localhost e validar step 0 + step 1**

```bash
cd /root/projects/alana
docker build -t alana:dev . && docker run --rm -p 8000:8000 --env-file .env -v $(pwd)/secrets:/app/secrets alana:dev
```

Em outro terminal, curl smoke:

```bash
curl -s http://localhost:8000/api/packages/adhoc/health
```

Esperado: `{"status":"ok"}`. Depois, abrir `http://localhost:8000/` no browser, logar, clicar "Criar Pacote", ver step 0; escolher "Pacote do zero", preencher nome + preço + upload. Conferir: cálculo em tempo real aparece, upload retorna ✓.

- [ ] **Step 5: Commit**

```bash
cd /root/projects/alana
git add static/js/adhoc_package.js static/js/dashboard.js
git commit -m "feat(adhoc): frontend step 0 (escolha) + step 1 (produto+upload)"
```

---

## Task 11: Frontend — step 2 (clientes com autocomplete)

**Files:**
- Modify: `static/js/adhoc_package.js`

- [ ] **Step 1: Substituir a função `renderVotesStep` pela implementação completa**

Em `static/js/adhoc_package.js`, substituir o body de `renderVotesStep` por:

```javascript
  function renderVotesStep() {
    const root = document.getElementById(STEPS.VOTES);
    root.innerHTML = `
      <h3>Clientes</h3>
      <div id="adhoc-votes-list"></div>
      <button type="button" id="adhoc-add-vote">+ Adicionar cliente</button>
      <div id="adhoc-votes-counter" class="sticky-counter"></div>
      <div class="modal-actions">
        <button type="button" id="adhoc-back-to-product">Voltar</button>
        <button type="button" id="adhoc-next-to-preview" disabled>Revisar</button>
      </div>
    `;
    show(STEPS.VOTES);

    if (state.votes.length === 0) {
      state.votes.push({ phone: '', qty: 0, customer_id: null, name: '' });
    }
    renderVoteRows();

    document.getElementById('adhoc-add-vote').addEventListener('click', () => {
      state.votes.push({ phone: '', qty: 0, customer_id: null, name: '' });
      renderVoteRows();
    });
    document.getElementById('adhoc-back-to-product').addEventListener('click', renderProductStep);
    document.getElementById('adhoc-next-to-preview').addEventListener('click', renderPreviewStep);
  }

  function renderVoteRows() {
    const list = document.getElementById('adhoc-votes-list');
    list.innerHTML = '';
    state.votes.forEach((v, i) => {
      const row = document.createElement('div');
      row.className = 'vote-row';
      row.innerHTML = `
        <div class="autocomplete-wrap">
          <input class="vote-search" data-i="${i}" placeholder="Nome ou telefone" value="${v.name || v.phone || ''}" autocomplete="off">
          <div class="autocomplete-results" data-i="${i}"></div>
        </div>
        <input class="vote-qty" data-i="${i}" type="number" min="1" max="24" value="${v.qty || ''}" placeholder="qty">
        <button type="button" class="vote-remove" data-i="${i}">×</button>
      `;
      list.appendChild(row);
    });
    wireVoteRows();
    updateCounter();
  }

  function wireVoteRows() {
    document.querySelectorAll('.vote-search').forEach((el) => {
      el.addEventListener('input', debounce(async (e) => {
        const i = Number(e.target.dataset.i);
        const q = e.target.value.trim();
        const resultsEl = document.querySelector(`.autocomplete-results[data-i="${i}"]`);
        if (q.length < 2) { resultsEl.innerHTML = ''; return; }
        const resp = await fetch(`/api/customers/search?q=${encodeURIComponent(q)}`);
        const { results } = await resp.json();
        resultsEl.innerHTML = results.map((r) =>
          `<button type="button" class="autocomplete-pick" data-i="${i}" data-phone="${r.phone}" data-name="${r.name}">${r.name} — ${r.phone}</button>`
        ).join('') + `<button type="button" class="autocomplete-pick-new" data-i="${i}" data-raw="${q}">+ Cadastrar novo: ${q}</button>`;
      }, 250));
    });

    document.querySelectorAll('.vote-qty').forEach((el) => {
      el.addEventListener('input', (e) => {
        const i = Number(e.target.dataset.i);
        state.votes[i].qty = parseInt(e.target.value || '0', 10);
        updateCounter();
      });
    });

    document.querySelectorAll('.vote-remove').forEach((el) => {
      el.addEventListener('click', (e) => {
        const i = Number(e.target.dataset.i);
        state.votes.splice(i, 1);
        renderVoteRows();
      });
    });

    document.body.addEventListener('click', (e) => {
      const pick = e.target.closest('.autocomplete-pick');
      if (pick) {
        const i = Number(pick.dataset.i);
        state.votes[i].phone = pick.dataset.phone;
        state.votes[i].name = pick.dataset.name;
        state.votes[i].customer_id = null; // id vindo do catálogo é opcional; backend resolve por phone
        renderVoteRows();
        return;
      }
      const pickNew = e.target.closest('.autocomplete-pick-new');
      if (pickNew) {
        const i = Number(pickNew.dataset.i);
        const raw = pickNew.dataset.raw || '';
        // Se raw tem só dígitos, trata como phone; senão como nome
        const digits = raw.replace(/\D/g, '');
        if (digits.length >= 10) {
          state.votes[i].phone = digits.startsWith('55') ? digits : ('55' + digits);
          state.votes[i].name = '';
        } else {
          state.votes[i].name = raw;
        }
        state.votes[i].customer_id = null;
        renderVoteRows();
      }
    }, { once: false });
  }

  function updateCounter() {
    const total = state.votes.reduce((s, v) => s + (parseInt(v.qty || 0, 10)), 0);
    const el = document.getElementById('adhoc-votes-counter');
    const nextBtn = document.getElementById('adhoc-next-to-preview');
    if (total < 24) {
      el.textContent = `Faltam ${24 - total} peças`;
      el.className = 'sticky-counter pending';
      nextBtn.disabled = true;
    } else if (total > 24) {
      el.textContent = `Ultrapassa em ${total - 24} peças`;
      el.className = 'sticky-counter error';
      nextBtn.disabled = true;
    } else {
      el.textContent = `✓ Pacote fechado (24/24)`;
      el.className = 'sticky-counter ok';
      // Exige também phone válido em todas as linhas
      const allValid = state.votes.every((v) => /^55\d{10,11}$/.test((v.phone || '').replace(/\D/g, '')));
      nextBtn.disabled = !allValid;
    }
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  function renderPreviewStep() {
    // Implementado na Task 12 — placeholder
    const root = document.getElementById(STEPS.PREVIEW);
    root.innerHTML = '<p>Step 3 — preview (implementação na Task 12).</p>';
    show(STEPS.PREVIEW);
  }
```

- [ ] **Step 2: Testar manualmente no browser**

Recarregar o container (`docker build && docker run`), abrir, ir até step 2, digitar 2-3 clientes, ver autocomplete funcionando, preencher qtys que somam 24, botão "Revisar" habilita.

- [ ] **Step 3: Commit**

```bash
cd /root/projects/alana
git add static/js/adhoc_package.js
git commit -m "feat(adhoc): frontend step 2 (clientes + autocomplete + contador)"
```

---

## Task 12: Frontend — step 3 (preview) + confirmação

**Files:**
- Modify: `static/js/adhoc_package.js`

- [ ] **Step 1: Substituir `renderPreviewStep` pela implementação completa**

```javascript
  async function renderPreviewStep() {
    const root = document.getElementById(STEPS.PREVIEW);
    root.innerHTML = '<p>Carregando preview…</p>';
    show(STEPS.PREVIEW);

    const payload = {
      product: {
        name: state.product.name,
        unit_price: state.product.unit_price,
        image: { drive_file_id: state.product.drive_file_id },
      },
      votes: state.votes.map((v) => ({
        phone: (v.phone || '').replace(/\D/g, ''),
        qty: v.qty,
        customer_id: v.customer_id,
        name: v.name,
      })),
    };

    try {
      const resp = await fetch('/api/packages/adhoc/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      state.preview = await resp.json();
    } catch (err) {
      root.innerHTML = `<p class="error">Erro no preview: ${err.message}</p>
        <button type="button" id="adhoc-back-to-votes">Voltar</button>`;
      document.getElementById('adhoc-back-to-votes').addEventListener('click', renderVotesStep);
      return;
    }

    const p = state.preview;
    root.innerHTML = `
      <h3>Revisar pacote</h3>
      <div class="preview-card">
        <img src="${state.product.full_url}" alt="" style="max-width:240px">
        <div><strong>${p.product.name}</strong></div>
        <div>Preço/peça: R$ ${p.product.unit_price.toFixed(2)}</div>
        <div>Subtotal (24 peças): R$ ${p.subtotal.toFixed(2)}</div>
        <div>Comissão ${p.commission_percent}%: R$ ${p.commission_amount.toFixed(2)}</div>
        <div><strong>Total: R$ ${p.total_final.toFixed(2)}</strong></div>
      </div>
      <h4>Clientes</h4>
      <ul class="preview-votes">
        ${p.votes_resolved.map((v) => `
          <li>${v.name || '(sem nome)'} — ${v.phone} — ${v.qty} peças — R$ ${(v.qty * p.product.unit_price).toFixed(2)}</li>
        `).join('')}
      </ul>
      <div class="modal-actions">
        <button type="button" id="adhoc-back-to-votes">Voltar</button>
        <button type="button" id="adhoc-confirm">Confirmar pacote</button>
      </div>
    `;
    document.getElementById('adhoc-back-to-votes').addEventListener('click', renderVotesStep);
    document.getElementById('adhoc-confirm').addEventListener('click', confirmAdhoc);
  }

  async function confirmAdhoc() {
    const btn = document.getElementById('adhoc-confirm');
    btn.disabled = true;
    btn.textContent = 'Confirmando…';
    const payload = {
      product: {
        name: state.product.name,
        unit_price: state.product.unit_price,
        image: { drive_file_id: state.product.drive_file_id },
      },
      votes: state.votes.map((v) => ({
        phone: (v.phone || '').replace(/\D/g, ''),
        qty: v.qty,
        customer_id: v.customer_id,
        name: v.name,
      })),
    };
    try {
      const resp = await fetch('/api/packages/adhoc/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      // Fecha modal e recarrega
      window.location.reload();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Confirmar pacote';
      alert(`Falha ao confirmar: ${err.message}`);
    }
  }
```

- [ ] **Step 2: Smoke teste manual completo**

Rodar container, executar o fluxo completo do zero (nome + preço + imagem → 2-3 clientes → preview → confirmar). Abrir o dashboard e conferir que o pacote recém-criado aparece na listagem com imagem, clientes e total corretos.

- [ ] **Step 3: Commit**

```bash
cd /root/projects/alana
git add static/js/adhoc_package.js
git commit -m "feat(adhoc): frontend step 3 (preview + confirm com reload)"
```

---

## Task 13: Teste de não-regressão do fluxo existente

**Files:**
- Create: `tests/unit/test_adhoc_no_regression.py`

- [ ] **Step 1: Escrever teste que exercita `POST /api/packages/manual/preview` com payload antigo**

Conteúdo:

```python
"""Garantia de que o fluxo manual existente (com enquete) não regrediu com a
introdução do módulo adhoc."""
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


def test_manual_preview_with_adhoc_enabled_still_works(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    fake_preview = {
        "poll_title": "Teste",
        "valor_col": 45.0,
        "total_qty": 24,
        "image": None,
        "image_thumb": None,
        "votes": [{"phone": "5511999999999", "name": "Maria", "qty": 24}],
    }
    monkeypatch.setattr(
        "main.build_preview_payload",
        lambda poll_id, votes: fake_preview,
    )

    client = TestClient(main_module.app)
    body = {
        "pollId": "POLL-X",
        "votes": [{"phone": "5511999999999", "qty": 24}],
    }
    response = client.post("/api/packages/manual/preview", json=body)
    assert response.status_code == 200
    assert response.json()["preview"]["total_qty"] == 24


def test_manual_preview_rejects_invalid_qty_as_before(monkeypatch):
    """Regra MANUAL_ALLOWED_QTY ({3,6,9,12,24}) deve continuar valendo no fluxo
    antigo mesmo com adhoc ligado."""
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    client = TestClient(main_module.app)
    body = {
        "pollId": "POLL-X",
        "votes": [{"phone": "5511999999999", "qty": 7}],  # 7 não é allowed
    }
    response = client.post("/api/packages/manual/preview", json=body)
    assert response.status_code == 422  # Pydantic rejeita antes


def test_adhoc_allows_arbitrary_qty_between_1_and_24(monkeypatch):
    """Confirma que o fluxo novo NÃO herda a restrição de qtys do fluxo antigo."""
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    class FakeClient:
        def select(self, *a, **kw): return []
    monkeypatch.setattr(
        "app.api.adhoc_packages.SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=FakeClient())),
    )

    client = TestClient(main_module.app)
    body = {
        "product": {"name": "X", "unit_price": 10.0, "image": {"drive_file_id": "D"}},
        "votes": [
            {"phone": "5511999999999", "qty": 7},   # 7 — ilegal no antigo, OK no novo
            {"phone": "5511988887777", "qty": 17},
        ],
    }
    response = client.post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 200
    assert response.json()["total_qty"] == 24
```

- [ ] **Step 2: Rodar suite completa**

```bash
cd /root/projects/alana && pytest tests/unit/ -v
```

Esperado: **todos os testes passam**, incluindo os 49 pré-existentes.

- [ ] **Step 3: Commit**

```bash
cd /root/projects/alana
git add tests/unit/test_adhoc_no_regression.py
git commit -m "test(adhoc): regressão — fluxo manual com enquete intocado"
```

---

## Task 14: Checklist de aceite em localhost

**Files:**
- Modify (opcional): `docs/superpowers/specs/2026-04-17-pacote-do-zero-design.md` — marcar spec como "Implementado em localhost, pendente deploy".

- [ ] **Step 1: Subir container limpo com `ADHOC_PACKAGES_ENABLED=true`**

```bash
cd /root/projects/alana
docker build -t alana:dev .
docker run --rm -p 8000:8000 --env-file .env -v $(pwd)/secrets:/app/secrets alana:dev
```

- [ ] **Step 2: Executar o roteiro de aceite**

Checklist (marcar no terminal/navegador):

- [ ] `GET /api/packages/adhoc/health` retorna 200 `{"status":"ok"}`.
- [ ] Abrir dashboard, botão "Criar Pacote" abre step 0 com 2 cards.
- [ ] Escolher "A partir de enquete" leva pro fluxo antigo **sem alterações visuais ou de comportamento**.
- [ ] Escolher "Pacote do zero" abre step 1 do novo fluxo.
- [ ] Step 1: nome + preço + upload funcionam; cálculo atualiza em tempo real; "Próximo" só habilita com tudo preenchido.
- [ ] Upload de PDF (ou qualquer não-imagem) é rejeitado no frontend ou volta 415.
- [ ] Upload de arquivo 6MB volta 413.
- [ ] Step 2: autocomplete sugere clientes ao digitar 2+ chars; opção "+ Cadastrar novo" sempre presente.
- [ ] Step 2: contador mostra "Faltam X / Ultrapassa Y / ✓ 24/24" corretamente.
- [ ] Step 2: "Revisar" só habilita com soma=24 e todos os phones válidos.
- [ ] Step 3: preview mostra imagem, nome, preço, subtotal, comissão 13%, total, lista de clientes.
- [ ] "Confirmar pacote" persiste, recarrega dashboard, pacote novo aparece na lista.
- [ ] No banco: `SELECT source FROM enquetes WHERE id='<POLL-ID>'` retorna `'manual'`.
- [ ] No banco: `SELECT synthetic FROM votos WHERE enquete_id='<POLL-ID>'` retorna `true` pra todos.
- [ ] No banco: `SELECT created_via FROM pacotes WHERE id='<PKG-ID>'` retorna `'adhoc'`.
- [ ] PDF do pacote é gerado (verificar no pipeline de saída).
- [ ] Cobrança Asaas aparece no log (sandbox) — ou é enfileirada sem erro.
- [ ] Desativar flag (`ADHOC_PACKAGES_ENABLED=false`), reiniciar container, `GET /api/packages/adhoc/health` retorna 404; dashboard abre normal, "Criar Pacote" vai direto pro fluxo antigo (step 0 não aparece ou cai pro fluxo enquete sem o card adhoc).

- [ ] **Step 3: Rodar suite de testes inteira**

```bash
cd /root/projects/alana && pytest tests/unit/ -v
```

Esperado: 100% verde.

- [ ] **Step 4: Revisar diff completo com o usuário**

```bash
cd /root/projects/alana
git log --oneline main..HEAD
git diff main..HEAD --stat
```

- [ ] **Step 5: Parar aqui e aguardar decisão de deploy**

**Não fazer push nem deploy sem comando explícito do usuário.** Spec diz: validação local → aprovação → deploy.

---

## Observações finais pro engenheiro

1. **Comissão:** percentual vem de `settings.COMMISSION_PERCENT`. Se por algum motivo a setting não existir, o endpoint `/preview` vai falhar — nesse caso, adicionar como fallback `float(settings, "COMMISSION_PERCENT", 13.0)` ou equivalente, depois de conferir em `app/config.py`.

2. **Busca `in` do PostgREST:** o filtro `("celular", "in", "(v1,v2)")` no `SupabaseRestClient.select` depende da implementação do client. Se não suportar, fazer N chamadas individuais por phone (lista costuma ser pequena, ≤24 por pacote).

3. **`SupabaseRestClient.insert` retorna lista:** confirme lendo `app/services/supabase_service.py` se o retorno é sempre `[obj]` ou apenas `obj`; ajustar indexação em conformidade.

4. **Upload 50KB vs 5MB:** o limite está hardcoded em `MAX_IMAGE_BYTES`. Se o usuário pedir outro valor, é um toque só ali.

5. **Catálogo duplicado:** a spec levantou "verificar se produtos permite nome duplicado". Se a tabela tem unique constraint, o `client.insert("produtos", ...)` vai falhar em 409. Tratar no endpoint `/confirm` com mensagem "já existe produto com esse nome — escolha outro". Se não tem constraint, ignore.

6. **Padrão de atomicidade:** o fluxo manual-com-enquete (template desse módulo) **não usa transação explícita**; confia na ordem de inserts e tolera inconsistências menores (ex: pacote criado mas pacote_clientes parcial). Manter esse padrão no novo módulo por coerência. Se quiser transação real, vira issue separada.

7. **CSS dos novos elementos:** `.choice-cards`, `.vote-row`, `.autocomplete-results`, `.sticky-counter`, `.preview-card`, `.modal-actions` podem precisar de classes no CSS do projeto. Localizar `static/css/` (ou similar) e adicionar estilos básicos. Essa parte **ficou de fora do plano** porque é trivial/estética — faça inline ou adicione `static/css/adhoc.css` conforme preferência do projeto.

8. **Migration em prod:** quando o usuário autorizar deploy, rodar o SQL da Task 2 no banco de produção **antes** de subir a imagem nova (colunas precisam existir antes do código ligar a flag).
