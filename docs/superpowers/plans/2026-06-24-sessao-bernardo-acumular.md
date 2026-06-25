# Sessão "Bernardo" (fechamento por acúmulo) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a sessão "Bernardo" numa **página standalone `/bernardo`** cujas enquetes acumulam votos indefinidamente até um botão "fechar pacote" criar um pacote com todos os votos do momento — sem alterar o fluxo legado (subset-sum 24) **nem o dashboard normal**.

**Architecture:** Sessões definidas em código (`app/sessions.py`). `PackageService.rebuild_for_poll` ganha um ramo no topo: enquete que casa com sessão `accumulate` é desviada para `_rebuild_accumulate` (mantém um único pacote `open`, nunca fecha em 24, nunca toca pacotes `closed`/`approved`). Um botão chama `close_accumulated`, que reusa a RPC transacional `close_package` com `total_qty` = soma real. Pacote fechado segue o pipeline normal. **Entrega isolada (fase 1):** UI numa página própria `/bernardo` + router dedicado `app/routers/bernardo.py` (`/api/bernardo/*`); o dashboard normal (`dashboard.py` + arquivos de UI) fica **intocado**. Os commits T4/T5 revertem o protótipo-aba e movem os endpoints pra o router novo.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, JS vanilla, SQLite (dev) / PostgREST+Postgres (prod), pytest.

## Global Constraints

- **Não-regressão:** enquetes que não casam com sessão `accumulate` mantêm comportamento idêntico ao atual. A ramificação fica no topo de `rebuild_for_poll`, antes de qualquer lógica de subset-sum.
- **Isolamento do dashboard normal (fase 1):** a feature vive em `/bernardo` + `app/routers/bernardo.py`. Ao fim, `app/routers/dashboard.py`, `templates/dashboard_v2.html`, `static/js/dashboard_v2.js`, `static/js/clientes.js`, `static/js/enquetes.js`, `static/js/finance-toggle.js` devem ficar **sem diff** vs. `main`. Únicos compartilhados tocados: o ramo guardado em `whatsapp_domain_service.py` e 1 linha de registro de router no `main.py`.
- **Sem migration / sem mudança de schema.** Modo derivado do `titulo` + config em código.
- **DB isolado** (`raylook_*`): nada pode afetar outros projetos.
- **Match da sessão:** substring case-insensitive sobre `enquetes.titulo`. Default: `{"name":"Bernardo","match":"Bernardo","mode":"accumulate"}`.
- **RPC `close_package` é parametrizável** em `p_total_qty`/`p_capacidade_total` (defaults 24, mas sem CHECK forçando 24). Usar a soma real.
- **Testes:** rodar com `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/<arquivo> -v`. Seguir o padrão `FakeClient` do módulo (em `tests/unit/test_whatsapp_domain_package_rebuild.py`) para PackageService e `FakeSupabaseClient` (`tests/_helpers/fake_supabase.py`) para endpoints.
- **Commits pequenos**, mensagem foca no *porquê*. Terminar mensagem com:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Não fazer `git push`** (aprovação pendente do usuário).

---

### Task 1: Config de sessões (`app/sessions.py`)

**Files:**
- Create: `app/sessions.py`
- Test: `tests/unit/test_sessions.py`

**Interfaces:**
- Produces:
  - `SESSIONS: list[dict]` — cada item `{"name": str, "match": str, "mode": str}`.
  - `session_for_title(titulo: str | None) -> dict | None` — sessão cujo `match` é substring (case-insensitive) do titulo, ou None.
  - `accumulate_session_for_title(titulo: str | None) -> dict | None` — idem, mas só retorna se `mode == "accumulate"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sessions.py
from app.sessions import session_for_title, accumulate_session_for_title, SESSIONS


def test_match_case_insensitive_substring():
    assert session_for_title("Promo BERNARDO 24/06")["name"] == "Bernardo"
    assert session_for_title("bernardo")["name"] == "Bernardo"


def test_no_match_returns_none():
    assert session_for_title("Camisa básica") is None
    assert session_for_title("") is None
    assert session_for_title(None) is None


def test_accumulate_helper_filters_by_mode():
    assert accumulate_session_for_title("Bernardo lote 1")["mode"] == "accumulate"
    assert accumulate_session_for_title("nada") is None


def test_default_session_is_bernardo_accumulate():
    assert any(s["name"] == "Bernardo" and s["mode"] == "accumulate" for s in SESSIONS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sessions'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/sessions.py
"""Sessões do dashboard: agrupam enquetes por substring do título e definem
o modo de fechamento. 'accumulate' = votos acumulam até o botão 'fechar pacote'
(sem subset-sum 24). Ausência de match = comportamento legado.

Lido pelo backend (ingest/rebuild) e pelo dashboard (aba + filtro)."""
from __future__ import annotations

from typing import Optional

SESSIONS: list[dict] = [
    {"name": "Bernardo", "match": "Bernardo", "mode": "accumulate"},
]


def session_for_title(titulo: Optional[str]) -> Optional[dict]:
    if not titulo:
        return None
    alvo = titulo.casefold()
    for sessao in SESSIONS:
        if sessao["match"].casefold() in alvo:
            return sessao
    return None


def accumulate_session_for_title(titulo: Optional[str]) -> Optional[dict]:
    sessao = session_for_title(titulo)
    if sessao and sessao.get("mode") == "accumulate":
        return sessao
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_sessions.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/sessions.py tests/unit/test_sessions.py
git commit -m "feat: config de sessões do dashboard (match por título + modo)

Por quê: a aba Bernardo precisa de um critério configurável (substring no
título) lido tanto no backend quanto no front, sem tabela nova.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Ramo de acúmulo no rebuild (`PackageService._rebuild_accumulate`)

**Files:**
- Modify: `app/services/whatsapp_domain_service.py` (poll select da `rebuild_for_poll` ~470-476; inserir branch após `unit_price`/`produto_id`/`enquete_fornecedor` resolvidos, ~488; adicionar métodos `_accumulate_pending` e `_rebuild_accumulate` na classe `PackageService`)
- Test: `tests/unit/test_whatsapp_domain_accumulate.py`

**Interfaces:**
- Consumes: `app.sessions.accumulate_session_for_title`; helpers existentes `_safe_datetime`, `settings.COMMISSION_PER_PIECE`; `self.client` (select/insert/update/delete/rpc).
- Produces:
  - `PackageService._accumulate_pending(self, enquete_id: str, active_votes: list[dict]) -> list[dict]` — votos pendentes (cada `{**voto, "qty": remaining}`), = ativos menos qty já consumida por cliente em pacotes `closed`/`approved`.
  - `PackageService._rebuild_accumulate(self, enquete_id, active_votes, produto_id, unit_price, enquete_fornecedor) -> dict` — mantém um único pacote `open` (seq 0) com os pendentes; retorna `{"mode":"accumulate","open_qty":int,"participants":int,"closed_count":0}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_whatsapp_domain_accumulate.py
from copy import deepcopy
import importlib

from app.services.whatsapp_domain_service import PackageService

fake_mod = importlib.import_module("tests.unit.test_whatsapp_domain_package_rebuild")
FakeClient = fake_mod.FakeClient


def _base_tables(titulo, votos):
    return {
        "enquetes": [{"id": "e1", "titulo": titulo, "produto_id": "prod1", "fornecedor": None,
                      "produtos": {"id": "prod1", "valor_unitario": 10.0}}],
        "produtos": [{"id": "prod1", "valor_unitario": 10.0}],
        "votos": votos,
        "pacotes": [],
        "pacote_clientes": [],
    }


def _vote(vid, cid, qty):
    return {"id": vid, "cliente_id": cid, "alternativa_id": None, "qty": qty,
            "voted_at": f"2026-06-24T10:0{vid[-1]}:00Z", "status": "in"}


def test_accumulate_does_not_close_at_24():
    # 16 + 16 = 32 (passaria de 24): no modo Bernardo NÃO fecha nada.
    tables = _base_tables("Lote Bernardo 1", [_vote("v1", "c1", 16), _vote("v2", "c2", 16)])
    client = FakeClient(tables)
    res = PackageService(client).rebuild_for_poll("e1")
    assert res["mode"] == "accumulate"
    pacotes = client.tables["pacotes"]
    assert len(pacotes) == 1
    assert pacotes[0]["status"] == "open"
    assert pacotes[0]["sequence_no"] == 0
    assert pacotes[0]["total_qty"] == 32
    assert pacotes[0]["participants_count"] == 2


def test_accumulate_never_deletes_closed_package():
    tables = _base_tables("Bernardo", [_vote("v3", "c3", 9)])
    tables["pacotes"].append({"id": "old", "enquete_id": "e1", "sequence_no": 1,
                              "status": "closed", "total_qty": 24, "capacidade_total": 24})
    client = FakeClient(tables)
    PackageService(client).rebuild_for_poll("e1")
    ids = {p["id"] for p in client.tables["pacotes"]}
    assert "old" in ids  # pacote closed preservado


def test_accumulate_subtracts_votes_already_in_closed_package():
    # c1 já tem 6 num pacote closed; agora aumentou pra 9 -> só +3 fica pendente.
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 9)])
    tables["pacotes"].append({"id": "old", "enquete_id": "e1", "sequence_no": 1,
                              "status": "closed", "total_qty": 6, "capacidade_total": 6})
    tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "old", "cliente_id": "c1",
                                      "voto_id": "v1", "produto_id": "prod1", "qty": 6})
    client = FakeClient(tables)
    PackageService(client).rebuild_for_poll("e1")
    opens = [p for p in client.tables["pacotes"] if p["status"] == "open"]
    assert len(opens) == 1
    assert opens[0]["total_qty"] == 3


def test_non_bernardo_still_closes_at_24():
    # Não-regressão: título sem match fecha exatamente 24 (12+12).
    tables = _base_tables("Camisa lisa", [_vote("v1", "c1", 12), _vote("v2", "c2", 12)])
    client = FakeClient(tables)
    res = PackageService(client).rebuild_for_poll("e1")
    assert res.get("closed_count") == 1
    closed = [p for p in client.tables["pacotes"] if p["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["total_qty"] == 24
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_accumulate.py -v`
Expected: FAIL (`KeyError: 'mode'` / acúmulo não existe — rebuild ainda fecha/tenta 24).

- [ ] **Step 3: Implement — adicionar import, branch e métodos**

No topo do arquivo `app/services/whatsapp_domain_service.py`, junto aos imports internos, adicionar:

```python
from app.sessions import accumulate_session_for_title
```

Na `rebuild_for_poll`, **incluir `titulo` no select da enquete** (era `columns="id,produto_id,fornecedor,produtos(id,valor_unitario)"`):

```python
        poll = self.client.select(
            "enquetes",
            columns="id,titulo,produto_id,fornecedor,produtos(id,valor_unitario)",
            filters=[("id", "eq", enquete_id)],
            single=True,
        )
```

Logo **após** as linhas que resolvem `unit_price`, `produto_id` e `enquete_fornecedor` (depois do bloco `if isinstance(produto, dict): ...`) e **antes** do `if not produto_id: return ...`, inserir o desvio:

```python
        # Ramo de acúmulo (sessão tipo Bernardo): nunca usa subset-sum 24.
        if accumulate_session_for_title(poll.get("titulo")):
            return self._rebuild_accumulate(
                enquete_id, active_votes, produto_id, unit_price, enquete_fornecedor
            )
```

Adicionar os dois métodos na classe `PackageService` (logo após `_subset_sum`):

```python
    def _accumulate_pending(
        self, enquete_id: str, active_votes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Votos ainda não congelados: ativos menos a qty já consumida por
        cliente em pacotes closed/approved desta enquete (subtração por cliente).
        """
        from collections import defaultdict

        pkgs = self.client.select(
            "pacotes", columns="id,status",
            filters=[("enquete_id", "eq", enquete_id), ("status", "in", ["closed", "approved"])],
        )
        pkg_ids = [str(p["id"]) for p in (pkgs if isinstance(pkgs, list) else [])]
        consumed_by_client: Dict[str, int] = defaultdict(int)
        if pkg_ids:
            rows = self.client.select(
                "pacote_clientes", columns="cliente_id,qty",
                filters=[("pacote_id", "in", pkg_ids)],
            )
            for r in (rows if isinstance(rows, list) else []):
                consumed_by_client[str(r["cliente_id"])] += int(r.get("qty") or 0)

        pending: List[Dict[str, Any]] = []
        for v in active_votes:
            cid = str(v["cliente_id"])
            remaining = max(int(v.get("qty") or 0) - consumed_by_client.get(cid, 0), 0)
            if remaining > 0:
                pending.append({**v, "qty": remaining})
        return pending

    def _rebuild_accumulate(
        self,
        enquete_id: str,
        active_votes: List[Dict[str, Any]],
        produto_id: Optional[str],
        unit_price: float,
        enquete_fornecedor: Optional[str],
    ) -> Dict[str, Any]:
        """Mantém UM pacote open (seq 0) com os votos pendentes. Nunca fecha
        sozinho, nunca toca closed/approved/cancelled."""
        pending = self._accumulate_pending(enquete_id, active_votes)
        open_qty = sum(int(v.get("qty") or 0) for v in pending)

        if open_qty > 0:
            payload: Dict[str, Any] = {
                "enquete_id": enquete_id,
                "sequence_no": 0,
                "capacidade_total": open_qty,
                "total_qty": open_qty,
                "participants_count": len(pending),
                "status": "open",
                "opened_at": _safe_datetime(pending[0].get("voted_at")).isoformat(),
            }
            if enquete_fornecedor:
                payload["fornecedor"] = enquete_fornecedor
            self.client.insert(
                "pacotes", payload, upsert=True,
                on_conflict="enquete_id,sequence_no", returning="minimal",
            )
        else:
            self.client.delete(
                "pacotes",
                filters=[("enquete_id", "eq", enquete_id), ("sequence_no", "eq", 0)],
            )
        return {"mode": "accumulate", "open_qty": open_qty,
                "participants": len(pending), "closed_count": 0}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_accumulate.py tests/unit/test_whatsapp_domain_package_rebuild.py -v`
Expected: PASS (todos — inclusive os legados de rebuild).

- [ ] **Step 5: Commit**

```bash
git add app/services/whatsapp_domain_service.py tests/unit/test_whatsapp_domain_accumulate.py
git commit -m "feat: ramo de acúmulo no rebuild para sessões tipo Bernardo

Por quê: enquetes da sessão Bernardo devem acumular votos num único pacote
open sem nunca fechar em 24, e sem deletar pacotes já fechados. Branch fica
no topo do rebuild para todo caller herdar a proteção.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Fechamento manual (`PackageService.close_accumulated`)

**Files:**
- Modify: `app/services/whatsapp_domain_service.py` (novo método `close_accumulated` em `PackageService`)
- Test: `tests/unit/test_whatsapp_domain_accumulate.py` (adicionar casos)

**Interfaces:**
- Consumes: `_accumulate_pending`, `accumulate_session_for_title`, RPC `close_package`, `assign_friendly_id`, `_safe_datetime`, `settings.COMMISSION_PER_PIECE`.
- Produces: `PackageService.close_accumulated(self, enquete_id: str) -> dict` — congela os pendentes num pacote `closed`. Retornos: `{"status":"ok","pacote_id":str,"total_qty":int,"participants":int}`, `{"status":"no_votes"}`, `{"status":"not_session"}`, `{"status":"not_found"}`, ou `{"status":"no_product"}`.

- [ ] **Step 1: Write the failing test**

```python
# adicionar em tests/unit/test_whatsapp_domain_accumulate.py

def test_close_accumulated_freezes_current_votes():
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 16), _vote("v2", "c2", 16)])
    client = FakeClient(tables)
    svc = PackageService(client)
    svc.rebuild_for_poll("e1")  # cria open com 32
    res = svc.close_accumulated("e1")
    assert res["status"] == "ok"
    assert res["total_qty"] == 32
    assert res["participants"] == 2
    closed = [p for p in client.tables["pacotes"] if p["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["total_qty"] == 32
    assert closed[0]["capacidade_total"] == 32
    assert closed[0]["sequence_no"] == 1
    pcs = [pc for pc in client.tables["pacote_clientes"] if pc["pacote_id"] == closed[0]["id"]]
    assert {pc["cliente_id"] for pc in pcs} == {"c1", "c2"}
    # open some (votos consumidos)
    assert not [p for p in client.tables["pacotes"] if p["status"] == "open"]


def test_close_accumulated_empty_returns_no_votes():
    tables = _base_tables("Bernardo", [])
    client = FakeClient(tables)
    assert PackageService(client).close_accumulated("e1")["status"] == "no_votes"


def test_close_then_new_vote_starts_second_package():
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 9)])
    client = FakeClient(tables)
    svc = PackageService(client)
    svc.rebuild_for_poll("e1")
    svc.close_accumulated("e1")          # pacote 1 (seq 1) com 9
    client.tables["votos"].append(_vote("v2", "c2", 12))  # voto novo
    svc.rebuild_for_poll("e1")           # reabre acúmulo
    opens = [p for p in client.tables["pacotes"] if p["status"] == "open"]
    assert len(opens) == 1
    assert opens[0]["total_qty"] == 12   # só o voto novo
    res = svc.close_accumulated("e1")    # pacote 2 (seq 2)
    seqs = sorted(p["sequence_no"] for p in client.tables["pacotes"] if p["status"] == "closed")
    assert seqs == [1, 2]
    assert res["total_qty"] == 12


def test_close_rejects_non_session_enquete():
    tables = _base_tables("Camisa lisa", [_vote("v1", "c1", 6)])
    client = FakeClient(tables)
    assert PackageService(client).close_accumulated("e1")["status"] == "not_session"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_accumulate.py -k close -v`
Expected: FAIL (`AttributeError: 'PackageService' object has no attribute 'close_accumulated'`)

- [ ] **Step 3: Implement `close_accumulated`**

Adicionar na classe `PackageService` (após `_rebuild_accumulate`):

```python
    def close_accumulated(self, enquete_id: str) -> Dict[str, Any]:
        """Congela todos os votos pendentes da enquete num pacote closed,
        reusando a RPC transacional close_package com total = soma real."""
        poll = self.client.select(
            "enquetes",
            columns="id,titulo,produto_id,fornecedor,produtos(id,valor_unitario)",
            filters=[("id", "eq", enquete_id)], single=True,
        )
        if not isinstance(poll, dict):
            return {"status": "not_found"}
        if not accumulate_session_for_title(poll.get("titulo")):
            return {"status": "not_session"}

        produto_id = poll.get("produto_id")
        unit_price = 0.0
        produto = poll.get("produtos")
        if isinstance(produto, dict):
            unit_price = float(produto.get("valor_unitario") or 0.0)
            if not produto_id:
                produto_id = produto.get("id")
        enquete_fornecedor = (poll.get("fornecedor") or "").strip() or None

        votes = self.client.select(
            "votos", columns="id,cliente_id,alternativa_id,qty,voted_at,status",
            filters=[("enquete_id", "eq", enquete_id), ("status", "neq", "out")],
        )
        active = [
            v for v in (votes if isinstance(votes, list) else [])
            if str(v.get("status") or "").strip().lower() != "out" and int(v.get("qty") or 0) > 0
        ]
        active.sort(key=lambda v: (-int(v.get("qty") or 0), _safe_datetime(v.get("voted_at"))))

        pending = self._accumulate_pending(enquete_id, active)
        if not pending:
            return {"status": "no_votes"}
        if not produto_id:
            return {"status": "no_product"}

        commission_per_piece = float(settings.COMMISSION_PER_PIECE)
        total_qty = sum(int(v["qty"]) for v in pending)
        votes_payload: List[Dict[str, Any]] = []
        for vote in pending:
            qty = int(vote["qty"])
            subtotal = round(unit_price * qty, 2)
            commission_amount = round(qty * commission_per_piece, 2)
            votes_payload.append({
                "vote_id": vote["id"], "cliente_id": vote["cliente_id"], "qty": qty,
                "unit_price": unit_price, "subtotal": subtotal, "commission_percent": 0,
                "commission_amount": commission_amount,
                "total_amount": round(subtotal + commission_amount, 2),
            })
        opened_at = _safe_datetime(pending[0].get("voted_at")).isoformat()
        closed_at = max(_safe_datetime(v.get("voted_at")) for v in pending).isoformat()

        rpc_result = self.client.rpc("close_package", {
            "p_enquete_id": enquete_id, "p_produto_id": produto_id,
            "p_votes": votes_payload, "p_opened_at": opened_at, "p_closed_at": closed_at,
            "p_capacidade_total": total_qty, "p_total_qty": total_qty,
        })
        if not isinstance(rpc_result, dict) or rpc_result.get("status") not in ("ok", None):
            if isinstance(rpc_result, dict) and rpc_result.get("status") == "no_votes":
                return {"status": "no_votes"}
        new_pkg_id = rpc_result.get("pacote_id") if isinstance(rpc_result, dict) else None

        if new_pkg_id:
            if enquete_fornecedor:
                try:
                    self.client.update("pacotes", {"fornecedor": enquete_fornecedor},
                                       filters=[("id", "eq", str(new_pkg_id))])
                except Exception:
                    logger.warning("falha propagando fornecedor pro pacote %s", new_pkg_id)
            try:
                from app.services.friendly_id_service import assign_friendly_id
                assign_friendly_id(self.client, str(new_pkg_id))
            except Exception:
                logger.exception("falha ao atribuir friendly_id pacote=%s", new_pkg_id)

        # Votos consumidos -> remove o open summary (próximo voto reabre acúmulo).
        self.client.delete(
            "pacotes",
            filters=[("enquete_id", "eq", enquete_id), ("sequence_no", "eq", 0)],
        )
        return {"status": "ok", "pacote_id": new_pkg_id,
                "total_qty": total_qty, "participants": len(pending)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_whatsapp_domain_accumulate.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add app/services/whatsapp_domain_service.py tests/unit/test_whatsapp_domain_accumulate.py
git commit -m "feat: close_accumulated congela votos do momento num pacote

Por quê: o botão 'fechar pacote' da aba Bernardo precisa criar um pacote
closed com a soma corrente (≠24) reusando a RPC transacional close_package,
sem mexer no fluxo legado. Próximo voto reabre o acúmulo.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Router dedicado `app/routers/bernardo.py` + página `/bernardo`

**Contexto:** o protótipo (commit `d010d3c`) pôs os endpoints dentro de
`app/routers/dashboard.py`. Esta task **move** tudo pra um router dedicado e
**reverte** `dashboard.py`, pra o dashboard normal ficar sem diff.

**Files:**
- Create: `app/routers/bernardo.py` (rota da página + 2 endpoints de API, sem prefixo — paths completos)
- Modify: `main.py` (1 linha: `app.include_router(bernardo.router)`)
- Revert: `app/routers/dashboard.py` (remover o import de `SESSIONS`/`accumulate_session_for_title`/`PackageService` e os 2 endpoints + `_session_by_name` do protótipo → volta a ficar sem diff vs. antes do Bernardo)
- Test: `tests/unit/test_bernardo_api.py` (renomeia o antigo `test_dashboard_sessions.py`)

**Interfaces:**
- Consumes: `SupabaseRestClient.from_settings`, `PackageService`, `app.sessions.{SESSIONS, accumulate_session_for_title}`, `Jinja2Templates`.
- Produces:
  - `GET /bernardo` → HTML (`templates/bernardo.html`).
  - `GET /api/bernardo/sessions/{session_name}` → `{"session": str, "enquetes": [{"enquete_id","titulo","total_qty","participants_count","participants":[{"nome","qty"}]}]}`.
  - `POST /api/bernardo/sessions/{session_name}/close` body `{"enquete_id": str}` → repassa o dict de `PackageService.close_accumulated`. 404 se sessão/enquete inexistente; 400 se enquete não pertence à sessão ou sem `enquete_id`.

- [ ] **Step 1: Reverter os endpoints do protótipo em `dashboard.py`**

Remover de `app/routers/dashboard.py` o que o commit `d010d3c` adicionou: o import
`from app.sessions import SESSIONS, accumulate_session_for_title`, o
`from app.services.whatsapp_domain_service import PackageService`, o helper
`_session_by_name` e os dois endpoints `get_session` / `close_session_package`.
Conferir: `git diff 35345d4 -- app/routers/dashboard.py` deve ficar **vazio**.

- [ ] **Step 2: Escrever o teste (falhando) em `tests/unit/test_bernardo_api.py`**

```python
# tests/unit/test_bernardo_api.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables, install_fake


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeSupabaseClient(empty_tables())
    install_fake(monkeypatch, fake)
    import main as main_module
    return TestClient(main_module.app), fake


def test_get_session_lists_matching_enquetes_with_accumulation(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Lote Bernardo", "status": "open",
                                    "produto_id": "p1", "fornecedor": None})
    fake.tables["enquetes"].append({"id": "e2", "titulo": "Camisa lisa", "status": "open",
                                    "produto_id": "p2", "fornecedor": None})
    fake.tables["clientes"].append({"id": "c1", "nome": "Ana"})
    fake.tables["votos"].append({"id": "v1", "enquete_id": "e1", "cliente_id": "c1",
                                 "qty": 16, "voted_at": "2026-06-24T10:00:00Z", "status": "in"})
    res = client.get("/api/bernardo/sessions/Bernardo")
    assert res.status_code == 200
    body = res.json()
    assert body["session"] == "Bernardo"
    assert len(body["enquetes"]) == 1            # só a e1 casa
    item = body["enquetes"][0]
    assert item["enquete_id"] == "e1"
    assert item["total_qty"] == 16
    assert item["participants"] == [{"nome": "Ana", "qty": 16}]


def test_get_session_404_when_unknown(fake_client):
    client, _ = fake_client
    assert client.get("/api/bernardo/sessions/Inexistente").status_code == 404


def test_post_close_rejects_non_session_enquete(fake_client):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e2", "titulo": "Camisa lisa", "status": "open"})
    res = client.post("/api/bernardo/sessions/Bernardo/close", json={"enquete_id": "e2"})
    assert res.status_code == 400


def test_post_close_calls_service(fake_client, monkeypatch):
    client, fake = fake_client
    fake.tables["enquetes"].append({"id": "e1", "titulo": "Bernardo", "status": "open"})
    import app.routers.bernardo as bern
    monkeypatch.setattr(bern.PackageService, "close_accumulated",
                        lambda self, eid: {"status": "ok", "pacote_id": "x", "total_qty": 16, "participants": 1})
    res = client.post("/api/bernardo/sessions/Bernardo/close", json={"enquete_id": "e1"})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "pacote_id": "x", "total_qty": 16, "participants": 1}


def test_post_close_400_without_enquete_id(fake_client):
    client, _ = fake_client
    assert client.post("/api/bernardo/sessions/Bernardo/close", json={}).status_code == 400


def test_page_route_serves_html(fake_client):
    client, _ = fake_client
    res = client.get("/bernardo")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
```

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_bernardo_api.py -v`
Expected: FAIL (rotas não existem).

- [ ] **Step 3: Implementar `app/routers/bernardo.py` + registrar no `main.py`**

```python
# app/routers/bernardo.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.sessions import SESSIONS, accumulate_session_for_title
from app.services.supabase_service import SupabaseRestClient
from app.services.whatsapp_domain_service import PackageService

router = APIRouter(tags=["bernardo"])
templates = Jinja2Templates(directory="templates")


def _session_by_name(session_name: str) -> Optional[Dict[str, Any]]:
    for s in SESSIONS:
        if s["name"].casefold() == session_name.casefold():
            return s
    return None


@router.get("/bernardo", response_class=HTMLResponse)
def bernardo_page(request: Request):
    """Página standalone da sessão Bernardo."""
    return templates.TemplateResponse(request, "bernardo.html", {"settings": settings})


@router.get("/api/bernardo/sessions/{session_name}")
def get_session(session_name: str) -> Dict[str, Any]:
    """Enquetes da sessão (modo acúmulo) + o acúmulo corrente de cada uma."""
    session = _session_by_name(session_name)
    if not session:
        raise HTTPException(404, "sessão não encontrada")
    client = SupabaseRestClient.from_settings()
    svc = PackageService(client)
    enquetes = client.select(
        "enquetes", columns="id,titulo,status,produto_id,fornecedor",
        filters=[("status", "eq", "open")],
    )
    items: List[Dict[str, Any]] = []
    for e in (enquetes if isinstance(enquetes, list) else []):
        match = accumulate_session_for_title(e.get("titulo"))
        if not match or match["name"] != session["name"]:
            continue
        votos = client.select(
            "votos", columns="id,cliente_id,qty,voted_at,status",
            filters=[("enquete_id", "eq", e["id"]), ("status", "neq", "out")],
        )
        active = [
            v for v in (votos if isinstance(votos, list) else [])
            if str(v.get("status") or "").strip().lower() != "out" and int(v.get("qty") or 0) > 0
        ]
        active.sort(key=lambda v: (-int(v.get("qty") or 0), str(v.get("voted_at") or "")))
        pending = svc._accumulate_pending(e["id"], active)
        cids = list({str(v["cliente_id"]) for v in pending})
        nomes: Dict[str, Any] = {}
        if cids:
            crows = client.select("clientes", columns="id,nome", filters=[("id", "in", cids)])
            nomes = {str(c["id"]): c.get("nome") for c in (crows if isinstance(crows, list) else [])}
        participants = [{"nome": nomes.get(str(v["cliente_id"]), "—"), "qty": int(v["qty"])} for v in pending]
        items.append({
            "enquete_id": e["id"], "titulo": e.get("titulo"),
            "total_qty": sum(int(v["qty"]) for v in pending),
            "participants_count": len(pending), "participants": participants,
        })
    return {"session": session["name"], "enquetes": items}


@router.post("/api/bernardo/sessions/{session_name}/close")
async def close_session_package(session_name: str, request: Request) -> Dict[str, Any]:
    """Fecha o pacote acumulado de UMA enquete da sessão (botão 'fechar pacote')."""
    session = _session_by_name(session_name)
    if not session:
        raise HTTPException(404, "sessão não encontrada")
    body = await request.json()
    enquete_id = (body or {}).get("enquete_id")
    if not enquete_id:
        raise HTTPException(400, "enquete_id obrigatório")
    client = SupabaseRestClient.from_settings()
    rows = client.select("enquetes", columns="id,titulo", filters=[("id", "eq", enquete_id)], limit=1)
    enq = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(enq, dict):
        raise HTTPException(404, "enquete não encontrada")
    match = accumulate_session_for_title(enq.get("titulo"))
    if not match or match["name"] != session["name"]:
        raise HTTPException(400, "enquete não pertence a esta sessão")
    return PackageService(client).close_accumulated(enquete_id)
```

Registrar no `main.py` (junto aos demais `app.include_router(...)`):

```python
from app.routers import bernardo as bernardo_router
app.include_router(bernardo_router.router)
```

> **Auth:** as rotas admin são protegidas por middleware (ver `DASHBOARD_AUTH_DISABLED`).
> Confirmar que `/bernardo` e `/api/bernardo/*` caem na MESMA proteção que `/` e
> `/api/dashboard/*`; se o middleware usa allowlist de paths, garantir que sejam
> tratadas como protegidas (admin). Verificar lendo o middleware de auth no `main.py`.

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/test_bernardo_api.py -v`
Expected: PASS (6 passed).

- [ ] **Step 4: Commit**

```bash
git rm tests/unit/test_dashboard_sessions.py 2>/dev/null || true
git add app/routers/bernardo.py app/routers/dashboard.py main.py tests/unit/test_bernardo_api.py
git commit -m "refactor(bernardo): endpoints num router dedicado /api/bernardo + rota da página

Por quê: entregar a feature numa página standalone /bernardo sem tocar no router
do dashboard normal (dashboard.py volta a ficar sem diff). Inclui GET /bernardo
que serve a página.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Página standalone `/bernardo` (reverter a integração-aba)

**Contexto:** o protótipo (commit `2dde48c`) integrou a feature como aba dentro do
dashboard, tocando 5 arquivos compartilhados. Esta task **reverte** essa integração
e entrega a UI numa página própria.

**Files:**
- Revert (estado pré-Bernardo): `templates/dashboard_v2.html`, `static/js/dashboard_v2.js`, `static/js/clientes.js`, `static/js/enquetes.js`, `static/js/finance-toggle.js`
- Delete: `static/js/sessao_bernardo.js` (era a versão-aba)
- Create: `templates/bernardo.html`, `static/js/bernardo_page.js`

- [ ] **Step 1: Reverter a integração-aba**

```bash
git checkout 35345d4 -- templates/dashboard_v2.html static/js/dashboard_v2.js \
  static/js/clientes.js static/js/enquetes.js static/js/finance-toggle.js
git rm static/js/sessao_bernardo.js
```
Conferir: `git diff 35345d4 -- templates/dashboard_v2.html static/js/dashboard_v2.js static/js/clientes.js static/js/enquetes.js static/js/finance-toggle.js` deve ficar **vazio** (dashboard normal intocado).

- [ ] **Step 2: Criar `templates/bernardo.html`**

Página própria, reusando o tema do dashboard. Antes de escrever, ler o `<head>` de
`templates/dashboard_v2.html` pra herdar a mesma fonte e o mesmo CSS-base (variáveis
`--accent`, `--text-primary`, `--text-muted`, etc.). Esqueleto:

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Raylook · Bernardo</title>
  <!-- reusar a MESMA fonte/estilos-base que o dashboard_v2.html referencia -->
  <style>
    body { background: var(--bg, #0f0f12); color: var(--text-primary, #eee);
           font-family: 'Outfit', sans-serif; margin: 0; }
    .bernardo-wrap { max-width: 760px; margin: 0 auto; padding: 24px; }
    .bernardo-title { font-size: 20px; font-weight: 700; margin-bottom: 16px; }
    .bn-card { border: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
               padding: 16px; margin-bottom: 12px; }
    .bn-card-title { font-weight: 600; }
    .bn-card-meta { color: var(--text-muted, #999); font-size: 13px; margin-top: 6px; }
    .bn-btn { margin-top: 12px; padding: 8px 14px; border-radius: 8px; cursor: pointer; }
    .bn-btn[disabled] { opacity: .5; cursor: not-allowed; }
  </style>
</head>
<body>
  <div class="bernardo-wrap">
    <div class="bernardo-title">Sessão Bernardo</div>
    <div id="bernardo-cards">Carregando…</div>
  </div>
  <script src="/static/js/bernardo_page.js"></script>
</body>
</html>
```

- [ ] **Step 3: Criar `static/js/bernardo_page.js`**

Página inteira (sem view-toggling). Strings de usuário **escapadas** antes do DOM.

```javascript
(function () {
  const SESSION = "Bernardo";
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
  async function load() {
    const wrap = document.getElementById("bernardo-cards");
    wrap.textContent = "Carregando…";
    let data;
    try {
      const res = await fetch(`/api/bernardo/sessions/${SESSION}`, { credentials: "same-origin" });
      data = await res.json();
    } catch (e) { wrap.textContent = "Erro ao carregar."; return; }
    if (!data.enquetes || !data.enquetes.length) {
      wrap.textContent = "Nenhuma enquete Bernardo ativa."; return;
    }
    wrap.innerHTML = "";
    for (const enq of data.enquetes) {
      const parts = (enq.participants || [])
        .map(p => `${escapeHtml(p.nome)}: ${escapeHtml(String(p.qty))}`).join(" · ") || "—";
      const card = document.createElement("div");
      card.className = "bn-card";
      card.innerHTML =
        `<div class="bn-card-title">${escapeHtml(enq.titulo)}</div>` +
        `<div class="bn-card-meta">Acúmulo: <b>${escapeHtml(String(enq.total_qty))}</b> peças · ${escapeHtml(String(enq.participants_count))} cliente(s)</div>` +
        `<div class="bn-card-meta">${parts}</div>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bn-btn";
      btn.textContent = "Fechar pacote";
      btn.disabled = (enq.total_qty || 0) <= 0;
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const r = await fetch(`/api/bernardo/sessions/${SESSION}/close`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            credentials: "same-origin", body: JSON.stringify({ enquete_id: enq.enquete_id }),
          });
          const out = await r.json();
          if (out.status === "ok") { load(); }
          else { alert("Não foi possível fechar: " + (out.status || "erro")); btn.disabled = false; }
        } catch (e) { alert("Erro: " + e.message); btn.disabled = false; }
      };
      card.appendChild(btn);
      wrap.appendChild(card);
    }
  }
  document.addEventListener("DOMContentLoaded", load);
})();
```

- [ ] **Step 4: Validar no browser (obrigatório para UI)**

Subir o server numa **porta livre** + SQLite scratch (NÃO usar a 8000 — é o container
de prod no host):
```bash
DASHBOARD_AUTH_DISABLED=true RAYLOOK_SANDBOX=true DATA_BACKEND=sqlite DATA_DIR=/tmp/bdb \
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8023
```
Seedar (via `SupabaseRestClient.from_settings()` com o mesmo `DATA_DIR`) uma enquete
com título contendo "Bernardo" + produto + alguns votos. Abrir
`http://127.0.0.1:8023/bernardo` (Playwright): conferir o acúmulo ao vivo, clicar
**Fechar pacote**, confirmar que o pacote vira `closed` (`GET /api/dashboard/packages`
mostra em "fechado" com `total_qty` = soma real ≠ 24), o acúmulo zera, e um voto novo
reabre. Screenshot. Parar o server. Confirmar que o Docker de prod segue intocado.

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard_v2.html static/js/dashboard_v2.js static/js/clientes.js \
  static/js/enquetes.js static/js/finance-toggle.js static/js/sessao_bernardo.js \
  templates/bernardo.html static/js/bernardo_page.js
git commit -m "feat(bernardo): página standalone /bernardo; reverte a integração-aba

Por quê: entregar a feature isolada numa página própria, sem tocar nos arquivos
do dashboard normal (revertidos ao estado pré-Bernardo).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Verificação final (suite completa + não-regressão)

**Files:** nenhum (só validação)

- [ ] **Step 1: Rodar a suite unit inteira**

Run: `DASHBOARD_AUTH_DISABLED=true pytest tests/unit/ -q`
Expected: tudo verde. Em especial, os testes legados de pacote/rebuild/advance continuam passando (não-regressão). Se algo quebrar, investigar a raiz (não mascarar).

- [ ] **Step 2: Type-check (se configurado)**

Run: `mypy app/sessions.py app/services/whatsapp_domain_service.py app/routers/bernardo.py 2>/dev/null || echo "mypy não configurado/sem alvo — ok"`
Expected: sem erros novos introduzidos (ou mensagem de skip).

- [ ] **Step 3: Conferir isolamento, diff e ausência de migration**

Run: `git diff --stat main..HEAD && git log --oneline main..HEAD`
Expected — arquivos **novos/tocados** da feature: `app/sessions.py`, `app/services/whatsapp_domain_service.py` (ramo guardado), `app/routers/bernardo.py`, `main.py` (1 linha de registro), `templates/bernardo.html`, `static/js/bernardo_page.js`, testes (`test_sessions.py`, `test_whatsapp_domain_accumulate.py`, `test_bernardo_api.py`) e docs.

**Gate de isolamento (crítico):** o dashboard normal deve ficar SEM diff —
```
git diff main..HEAD -- app/routers/dashboard.py templates/dashboard_v2.html \
  static/js/dashboard_v2.js static/js/clientes.js static/js/enquetes.js static/js/finance-toggle.js
```
deve sair **vazio**. **Nenhum** arquivo em `deploy/postgres/migrations/` ou alteração de schema.

- [ ] **Step 4: NÃO fazer push** — relatar ao usuário que está pronto para revisão e aguardar aprovação para `git push`.

## Self-Review (verificação do plano contra o spec)

- **Cobertura do spec:** config (T1) · ramo de rebuild + subtração por cliente (T2) · close_accumulated + reabertura (T3) · router dedicado `/api/bernardo` + rota da página + revert do dashboard.py (T4) · página standalone `/bernardo` + revert da integração-aba (T5) · downstream inalterado e sem migration (T2/T3/T6) · isolamento do dashboard normal (T4/T5/T6) · edge cases (T2/T3 testes). ✔
- **Não-regressão:** `test_non_bernardo_still_closes_at_24` (T2) + suite legada (T6) + gate de isolamento (T6 Step 3: dashboard normal sem diff). ✔
- **Sem placeholders:** todo passo traz código/comando concreto. T5 Step 2 tem leitura do `<head>` do `dashboard_v2.html` só pra herdar fonte/CSS-base na página nova. ✔
- **Consistência de tipos:** `_accumulate_pending`, `_rebuild_accumulate`, `close_accumulated`, `session_for_title`, `accumulate_session_for_title`, `_session_by_name` usados com as mesmas assinaturas em todas as tasks. ✔
- **Migração do protótipo:** T4 reverte os endpoints em `dashboard.py` e T5 reverte a integração-aba (5 arquivos) via `git checkout 35345d4 -- …`; o que se mantém são os commits de backend (T1–T3, intocados) + os novos T4/T5. ✔
