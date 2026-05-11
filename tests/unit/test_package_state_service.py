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
