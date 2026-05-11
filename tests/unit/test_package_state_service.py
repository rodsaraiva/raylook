"""Testes de app/services/package_state_service.

O service já migrou para Postgres; várias funções viraram no-op
de compatibilidade. Cobrimos:
- _resolve_package_uuid em fast path (UUID válido)
- _resolve_package_uuid em legacy ID inválido (sem _, sem int)
- load/save: no-op
- update_package_state e update_vote_state: skip quando UUID não resolve
- merge_states_into_metrics: pass-through
"""
import pytest

from app.services import package_state_service as svc


def test_resolve_uuid_fast_path_returns_input():
    """Quando o input já é UUID válido, retorna ele mesmo sem chamar DB."""
    uuid_str = "12345678-1234-1234-1234-123456789abc"
    assert svc._resolve_package_uuid(uuid_str) == uuid_str


def test_resolve_uuid_legacy_no_underscore_returns_none():
    """ID sem underscore não é UUID nem formato legado válido."""
    assert svc._resolve_package_uuid("not-uuid-and-no-underscore") is None


def test_resolve_uuid_legacy_with_non_int_seq_returns_none():
    """poll_id_seqno com seq não-numérico não resolve."""
    assert svc._resolve_package_uuid("poll-id_NOT_A_NUMBER") is None


def test_load_package_states_is_noop_empty_dict():
    assert svc.load_package_states() == {}


def test_save_package_states_is_noop():
    """save_package_states não levanta nem retorna nada útil."""
    assert svc.save_package_states({"pkg-1": {"x": 1}}) is None


def test_merge_states_into_metrics_returns_metrics_unchanged():
    metrics = {"votos": {"packages": {"open": [{"id": "p1"}]}}}
    result = svc.merge_states_into_metrics(metrics)
    assert result is metrics


def test_update_package_state_skips_when_uuid_unresolvable(caplog):
    """Quando _resolve_package_uuid retorna None, função sai cedo (sem
    bater no DB) e loga warning."""
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.package_state_service")
    svc.update_package_state("bad-id-no-underscore", {"pdf_status": "sent"})
    assert any("could not resolve" in r.message for r in caplog.records)


def test_update_vote_state_skips_when_uuid_unresolvable(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.package_state_service")
    svc.update_vote_state("bad-id-no-underscore", 0, {"mercadopago_payment_id": "x"})
    assert any("could not resolve" in r.message for r in caplog.records)


def test_update_package_state_skips_when_no_mappable_fields(monkeypatch, caplog):
    """UUID resolve, mas update_data não tem nenhum campo em _PACOTES_COLUMN_MAP
    → sai cedo sem chamar DB."""
    import logging
    caplog.set_level(logging.DEBUG, logger="raylook.package_state_service")

    called = {"request": False}

    class FakeClient:
        def _request(self, *a, **kw):
            called["request"] = True
            raise AssertionError("should not have been called")

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    # UUID válido (fast path), mas update_data sem keys mapeáveis
    svc.update_package_state(
        "12345678-1234-1234-1234-123456789abc",
        {"unmapped_field": "value"},
    )
    assert called["request"] is False


# ---------------------------------------------------------------------------
# Helpers de resposta fake para _request
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Simula httpx.Response minimalista para testes de _request."""

    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(body)

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# _resolve_package_uuid — legacy format (com DB mock)
# ---------------------------------------------------------------------------

def test_resolve_uuid_legacy_enquete_found(monkeypatch):
    """ID no formato legacy poll_id_seqno resolve quando enquete + pacote existem."""
    responses = iter([
        _FakeResponse(200, [{"id": "eid-1111"}]),            # enquetes
        _FakeResponse(200, [{"id": "pkg-uuid-aaaa-bbbb"}]),  # pacotes
    ])

    class FakeClient:
        def _request(self, method, path, **kw):
            return next(responses)

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    result = svc._resolve_package_uuid("poll-abc_0")
    assert result == "pkg-uuid-aaaa-bbbb"


def test_resolve_uuid_legacy_enquete_not_found(monkeypatch):
    """ID legacy mas enquete não retorna linhas → None."""
    class FakeClient:
        def _request(self, method, path, **kw):
            return _FakeResponse(200, [])

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    result = svc._resolve_package_uuid("poll-xyz_2")
    assert result is None


def test_resolve_uuid_legacy_pacote_not_found(monkeypatch):
    """Enquete encontrada mas pacote na sequência não existe → None."""
    responses = iter([
        _FakeResponse(200, [{"id": "eid-2222"}]),  # enquetes
        _FakeResponse(200, []),                     # pacotes: vazio
    ])

    class FakeClient:
        def _request(self, method, path, **kw):
            return next(responses)

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    result = svc._resolve_package_uuid("poll-xyz_1")
    assert result is None


def test_resolve_uuid_legacy_enquete_request_error(monkeypatch):
    """Resposta não-200 na busca de enquete → lista vazia → None."""
    class FakeClient:
        def _request(self, method, path, **kw):
            return _FakeResponse(500, {"error": "server error"})

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    result = svc._resolve_package_uuid("poll-error_0")
    assert result is None


# ---------------------------------------------------------------------------
# update_package_state — happy path (PATCH supabase)
# ---------------------------------------------------------------------------

def test_update_package_state_patch_success(monkeypatch, caplog):
    """UUID válido + campo mapeável → faz PATCH e loga debug de sucesso."""
    import logging
    caplog.set_level(logging.DEBUG, logger="raylook.package_state_service")

    patched = []

    class FakeClient:
        def _request(self, method, path, payload=None, prefer=None, **kw):
            patched.append({"method": method, "path": path, "payload": payload})
            return _FakeResponse(204)

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    svc.update_package_state(
        "12345678-1234-1234-1234-123456789abc",
        {"pdf_status": "sent", "tag": "vip", "ignored": "x"},
    )
    assert len(patched) == 1
    assert patched[0]["method"] == "PATCH"
    assert "pacotes" in patched[0]["path"]
    assert patched[0]["payload"] == {"pdf_status": "sent", "tag": "vip"}
    assert any("updated pacotes" in r.message for r in caplog.records)


def test_update_package_state_patch_failure_logs_error(monkeypatch, caplog):
    """PATCH com status != 200/204 loga erro."""
    import logging
    caplog.set_level(logging.ERROR, logger="raylook.package_state_service")

    class FakeClient:
        def _request(self, method, path, **kw):
            return _FakeResponse(500, "internal server error")

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    svc.update_package_state(
        "12345678-1234-1234-1234-123456789abc",
        {"pdf_status": "failed"},
    )
    assert any("PATCH pacotes failed" in r.message for r in caplog.records)


def test_update_package_state_all_mapped_columns(monkeypatch):
    """Todos os campos de _PACOTES_COLUMN_MAP são enviados quando presentes."""
    patched = []

    class FakeClient:
        def _request(self, method, path, payload=None, **kw):
            patched.append(payload)
            return _FakeResponse(200)

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: FakeClient()),
    )
    all_fields = {
        "pdf_status": "sent",
        "pdf_file_name": "file.pdf",
        "pdf_sent_at": "2026-01-01T00:00:00Z",
        "pdf_attempts": 3,
        "tag": "promo",
        "custom_title": "Título Especial",
    }
    svc.update_package_state("12345678-1234-1234-1234-123456789abc", all_fields)
    assert len(patched) == 1
    for key in all_fields:
        assert key in patched[0]


# ---------------------------------------------------------------------------
# update_vote_state — happy path completo
# ---------------------------------------------------------------------------

def test_update_vote_state_patch_existing_pagamento(monkeypatch, caplog):
    """pacote_clientes encontrado, venda encontrada, pagamento existente → PATCH."""
    import logging
    caplog.set_level(logging.DEBUG, logger="raylook.package_state_service")

    pkg_uuid = "12345678-1234-1234-1234-123456789abc"
    calls = []

    def fake_request(self, method, path, payload=None, **kw):
        calls.append({"method": method, "path": path, "payload": payload})
        if "pacote_clientes" in path:
            return _FakeResponse(200, [{"id": "pc-id-001"}])
        if "vendas" in path:
            return _FakeResponse(200, [{"id": "venda-id-001"}])
        if "pagamentos" in path and method == "GET":
            return _FakeResponse(200, [{"id": "pag-id-001"}])
        if "pagamentos" in path and method == "PATCH":
            return _FakeResponse(204)
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    svc.update_vote_state(pkg_uuid, 0, {"mercadopago_payment_id": "mp-123"})

    patch_calls = [c for c in calls if c["method"] == "PATCH"]
    assert len(patch_calls) == 1
    assert "pagamentos" in patch_calls[0]["path"]
    payload = patch_calls[0]["payload"]
    assert payload["provider_payment_id"] == "mp-123"
    assert payload["provider"] == "mercadopago"
    assert "payload_json" in payload


def test_update_vote_state_post_new_pagamento(monkeypatch, caplog):
    """Sem pagamento existente → POST para criar novo."""
    import logging
    caplog.set_level(logging.DEBUG, logger="raylook.package_state_service")

    pkg_uuid = "12345678-1234-1234-1234-123456789abc"
    calls = []

    def fake_request(self, method, path, payload=None, **kw):
        calls.append({"method": method, "path": path, "payload": payload})
        if "pacote_clientes" in path:
            return _FakeResponse(200, [{"id": "pc-id-002"}])
        if "vendas" in path:
            return _FakeResponse(200, [{"id": "venda-id-002"}])
        if "pagamentos" in path and method == "GET":
            return _FakeResponse(200, [])  # sem pagamento existente
        if "pagamentos" in path and method == "POST":
            return _FakeResponse(201)
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    svc.update_vote_state(pkg_uuid, 0, {"asaas_payment_id": "asaas-456"})

    post_calls = [c for c in calls if c["method"] == "POST" and "pagamentos" in c["path"]]
    assert len(post_calls) == 1
    assert post_calls[0]["payload"]["provider_payment_id"] == "asaas-456"
    assert post_calls[0]["payload"]["provider"] == "asaas"
    assert post_calls[0]["payload"]["venda_id"] == "venda-id-002"


def test_update_vote_state_no_pacote_clientes_skips(monkeypatch, caplog):
    """Sem linha em pacote_clientes → sai cedo logando warning."""
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.package_state_service")

    def fake_request(self, method, path, **kw):
        if "pacote_clientes" in path:
            return _FakeResponse(200, [])
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    svc.update_vote_state("12345678-1234-1234-1234-123456789abc", 0, {"x": "y"})
    assert any("no pacote_clientes" in r.message for r in caplog.records)


def test_update_vote_state_no_venda_skips(monkeypatch, caplog):
    """pacote_clientes encontrado mas sem venda → loga warning e não cria pagamento."""
    import logging
    caplog.set_level(logging.WARNING, logger="raylook.package_state_service")

    def fake_request(self, method, path, **kw):
        if "pacote_clientes" in path:
            return _FakeResponse(200, [{"id": "pc-id-003"}])
        if "vendas" in path:
            return _FakeResponse(200, [])
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    svc.update_vote_state("12345678-1234-1234-1234-123456789abc", 1, {"mercadopago_payment_id": "mp-999"})
    assert any("no venda row" in r.message for r in caplog.records)


def test_update_vote_state_pagamento_write_failure_logs_error(monkeypatch, caplog):
    """Resposta de erro no POST/PATCH de pagamentos loga error."""
    import logging
    caplog.set_level(logging.ERROR, logger="raylook.package_state_service")

    def fake_request(self, method, path, **kw):
        if "pacote_clientes" in path:
            return _FakeResponse(200, [{"id": "pc-id-004"}])
        if "vendas" in path:
            return _FakeResponse(200, [{"id": "venda-id-004"}])
        if "pagamentos" in path and method == "GET":
            return _FakeResponse(200, [])
        if "pagamentos" in path and method == "POST":
            return _FakeResponse(500, "error")
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    svc.update_vote_state("12345678-1234-1234-1234-123456789abc", 0, {"mercadopago_payment_id": "mp-err"})
    assert any("write pagamentos failed" in r.message for r in caplog.records)


def test_update_vote_state_with_extra_payload(monkeypatch):
    """Campos extras além de payment_id são incluídos em payload_json."""
    pkg_uuid = "12345678-1234-1234-1234-123456789abc"
    posts = []

    def fake_request(self, method, path, payload=None, **kw):
        if "pacote_clientes" in path:
            return _FakeResponse(200, [{"id": "pc-id-005"}])
        if "vendas" in path:
            return _FakeResponse(200, [{"id": "venda-id-005"}])
        if "pagamentos" in path and method == "GET":
            return _FakeResponse(200, [])
        if "pagamentos" in path and method == "POST":
            posts.append(payload)
            return _FakeResponse(201)
        return _FakeResponse(200, [])

    import app.services.supabase_service as sb_mod
    monkeypatch.setattr(sb_mod.SupabaseRestClient, "from_settings", lambda: type("C", (), {"_request": fake_request})())

    extra = {"mercadopago_payment_id": "mp-extra", "custom_field": "abc", "amount": 99.9}
    svc.update_vote_state(pkg_uuid, 0, extra)

    assert len(posts) == 1
    import json
    blob = json.loads(posts[0]["payload_json"])
    assert blob["custom_field"] == "abc"
    assert blob["amount"] == 99.9
