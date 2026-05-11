"""Testes de app/services/poll_reconcile_service.

Cobre:
- _phone_variants (puro, parametrize)
- _now_iso (smoke)
- PollReconcileService._fetch_enquete
- PollReconcileService._fetch_db_votes
- PollReconcileService._fetch_whapi_state
- PollReconcileService._diff
- PollReconcileService._mark_vote_removed
- PollReconcileService._insert_missing_vote (via mock do VoteService)
- PollReconcileService.compare
- PollReconcileService.sync
- PollReconcileService.sync_all_open

Pula:
- _reconcile_loop: loop infinito async — custo > valor
- start_poll_reconcile_scheduler: agendador de event loop — custo > valor
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.services import poll_reconcile_service as prs
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables


# ── Fake estendido ────────────────────────────────────────────────────────────

class FakeSupabaseClientExt(FakeSupabaseClient):
    """Aceita kwargs extras (ex.: returning) e faz coerce numérico em filtros gt/lt.

    O FakeSupabaseClient base compara v <= value diretamente. Quando o campo é
    int no dict e o filtro usa string (ex.: qty gt "0"), a comparação estoura.
    Aqui convertemos o value para o tipo do campo quando possível.
    """

    def update(self, table, values, *, filters=None, returning="representation", **kwargs):
        return super().update(table, values, filters=filters)

    def select(self, table, *, columns="*", filters=None, limit=None,
               offset=None, order=None, single=False):
        # Normaliza filtros numéricos: tenta converter value para int ou float
        # para evitar TypeError quando o campo é int e o filtro usa string.
        normalized: list = []
        if filters:
            for field, op, value in filters:
                if op in ("gt", "lt", "gte", "lte") and isinstance(value, str):
                    for cast in (int, float):
                        try:
                            value = cast(value)
                            break
                        except (ValueError, TypeError):
                            pass
                normalized.append((field, op, value))
        else:
            normalized = filters
        return super().select(table, columns=columns, filters=normalized,
                              limit=limit, offset=offset, order=order, single=single)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_svc(tables: dict | None = None, mock_whapi=None, no_whapi: bool = False):
    """Instancia PollReconcileService com FakeSupabaseClientExt.

    Quando no_whapi=True, seta self.whapi=None após instanciar (evita que o
    construtor use o WHAPIClient real disponível no ambiente de teste).
    """
    fake = FakeSupabaseClientExt({**empty_tables(), **(tables or {})})
    svc = prs.PollReconcileService(sb=fake)
    if no_whapi:
        svc.whapi = None
    elif mock_whapi is not None:
        svc.whapi = mock_whapi
    return svc, fake


_UUID1 = "00000000-0000-0000-0000-000000000001"
_UUID2 = "00000000-0000-0000-0000-000000000002"


def _enquete(id=_UUID1, external_poll_id="poll-abc", chat_id="123@g.us",
             titulo="Enquete Teste", status="open",
             created_at_provider="2026-05-10T10:00:00Z"):
    return dict(id=id, external_poll_id=external_poll_id, chat_id=chat_id,
                titulo=titulo, status=status, created_at_provider=created_at_provider)


def _voto(id="v1", enquete_id=None, qty=3, status="in",
          celular="5511999999999", nome="Ana"):
    return dict(id=id, enquete_id=enquete_id or _UUID1, qty=qty, status=status,
                cliente={"id": "c1", "celular": celular, "nome": nome})


def _whapi_state(voters_by_option: Dict[str, List[str]], poll_id="poll-abc") -> Dict[str, Any]:
    """Constrói um whapi_state com votos agrupados por nome de opção."""
    results = [
        {"name": opt_name, "voters": voters}
        for opt_name, voters in voters_by_option.items()
    ]
    total = sum(len(v) for v in voters_by_option.values())
    return {"id": poll_id, "total": total, "results": results}


# ── _phone_variants ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("phone,expected_subset", [
    # Número com DDI 55 → gera versão sem DDI
    ("5511999999999", ["5511999999999", "11999999999"]),
    # Número sem DDI → gera versão com DDI
    ("11999999999", ["11999999999", "5511999999999"]),
    # Celular 11 dígitos (com 9) → gera versão sem 9
    ("11999999999", ["11999999999", "1199999999"]),
    # Fixo 10 dígitos → gera versão com 9
    ("1199999999", ["1199999999", "11999999999"]),
    # Com DDI e 9º dígito: 5562 9xxxx-xxxx → sem 9
    ("5562999999999", ["5562999999999", "62999999999"]),
])
def test_phone_variants_contains_expected(phone, expected_subset):
    result = prs._phone_variants(phone)
    for v in expected_subset:
        assert v in result, f"esperado {v!r} em _phone_variants({phone!r}), obteve {result}"


def test_phone_variants_returns_list():
    result = prs._phone_variants("5511999999999")
    assert isinstance(result, list)
    assert len(result) >= 2


def test_phone_variants_no_ddi_adds_55_prefix():
    result = prs._phone_variants("11999999999")
    assert "5511999999999" in result


def test_phone_variants_with_ddi_strips_55():
    result = prs._phone_variants("5511888888888")
    assert "11888888888" in result


def test_phone_variants_10digit_adds_ninth():
    """Número fixo 10 dígitos deve gerar variante com 9 inserido."""
    result = prs._phone_variants("1199999999")
    assert "11999999999" in result


def test_phone_variants_11digit_removes_ninth():
    """Celular 11 dígitos deve gerar variante sem 9."""
    result = prs._phone_variants("11999999999")
    assert "1199999999" in result


def test_phone_variants_deduplicates():
    """Não deve haver duplicatas no resultado."""
    result = prs._phone_variants("5511999999999")
    assert len(result) == len(set(result))


# ── _now_iso ──────────────────────────────────────────────────────────────────

def test_now_iso_returns_iso_string_with_utc():
    result = prs._now_iso()
    assert isinstance(result, str)
    assert "+" in result or result.endswith("+00:00")


def test_now_iso_contains_current_year():
    result = prs._now_iso()
    assert "2026" in result or "2025" in result


# ── PollReconcileService._fetch_enquete ───────────────────────────────────────

def test_fetch_enquete_by_uuid():
    enq = _enquete(id="00000000-0000-0000-0000-000000000001")
    svc, _ = _make_svc({"enquetes": [enq]}, no_whapi=True)
    result = svc._fetch_enquete("00000000-0000-0000-0000-000000000001")
    assert result is not None
    assert result["id"] == "00000000-0000-0000-0000-000000000001"


def test_fetch_enquete_by_external_poll_id():
    enq = _enquete(external_poll_id="poll-xyz-999")
    svc, _ = _make_svc({"enquetes": [enq]}, no_whapi=True)
    result = svc._fetch_enquete("poll-xyz-999")
    assert result is not None
    assert result["external_poll_id"] == "poll-xyz-999"


def test_fetch_enquete_not_found_returns_none():
    svc, _ = _make_svc(no_whapi=True)
    assert svc._fetch_enquete("nao-existe") is None


def test_fetch_enquete_not_found_uuid_returns_none():
    svc, _ = _make_svc(no_whapi=True)
    assert svc._fetch_enquete("00000000-0000-0000-0000-000000000099") is None


# ── PollReconcileService._fetch_db_votes ──────────────────────────────────────

def test_fetch_db_votes_returns_active_votes():
    voto = _voto(qty=3, status="in")
    svc, _ = _make_svc({"votos": [voto]}, no_whapi=True)
    result = svc._fetch_db_votes(_UUID1)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["qty"] == 3


def test_fetch_db_votes_excludes_out_status():
    """Votos com status=out não devem aparecer (filtro status=in)."""
    voto_out = _voto(id="v2", qty=3, status="out")
    svc, _ = _make_svc({"votos": [voto_out]}, no_whapi=True)
    result = svc._fetch_db_votes(_UUID1)
    assert result == []


def test_fetch_db_votes_empty_when_no_votes():
    svc, _ = _make_svc(no_whapi=True)
    result = svc._fetch_db_votes(_UUID1)
    assert result == []


def test_fetch_db_votes_returns_list_type():
    svc, _ = _make_svc(no_whapi=True)
    result = svc._fetch_db_votes(_UUID2)
    assert isinstance(result, list)


# ── PollReconcileService._fetch_whapi_state ───────────────────────────────────

def test_fetch_whapi_state_returns_none_when_no_whapi():
    enq = _enquete()
    svc, _ = _make_svc({"enquetes": [enq]}, no_whapi=True)
    result = svc._fetch_whapi_state(enq)
    assert result is None


def test_fetch_whapi_state_returns_none_when_chat_id_empty():
    enq = _enquete(chat_id="")
    mock_whapi = MagicMock()
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    result = svc._fetch_whapi_state(enq)
    assert result is None
    mock_whapi.get_poll_current_state.assert_not_called()


def test_fetch_whapi_state_returns_none_when_poll_id_empty():
    enq = _enquete(external_poll_id="")
    mock_whapi = MagicMock()
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    result = svc._fetch_whapi_state(enq)
    assert result is None


def test_fetch_whapi_state_calls_whapi_with_correct_args():
    enq = _enquete(chat_id="123@g.us", external_poll_id="poll-abc",
                   created_at_provider="2026-05-10T10:00:00Z")
    expected_state = {"id": "poll-abc", "total": 2, "results": []}
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = expected_state
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    result = svc._fetch_whapi_state(enq)
    assert result == expected_state
    mock_whapi.get_poll_current_state.assert_called_once()
    call_args = mock_whapi.get_poll_current_state.call_args
    assert call_args[0][0] == "123@g.us"
    assert call_args[0][1] == "poll-abc"


def test_fetch_whapi_state_returns_none_on_exception():
    enq = _enquete()
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.side_effect = RuntimeError("timeout")
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    result = svc._fetch_whapi_state(enq)
    assert result is None


def test_fetch_whapi_state_parses_created_at_z_suffix():
    """created_at_provider com sufixo Z deve ser convertido para unix timestamp."""
    enq = _enquete(created_at_provider="2026-05-10T10:00:00Z")
    expected = {"id": "poll-abc", "total": 0, "results": []}
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = expected
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    svc._fetch_whapi_state(enq)
    _, _, unix_ts = mock_whapi.get_poll_current_state.call_args[0]
    assert isinstance(unix_ts, int)
    assert unix_ts > 0


def test_fetch_whapi_state_handles_missing_created_at():
    """created_at_provider ausente → unix_ts=None passado para get_poll_current_state."""
    enq = _enquete(created_at_provider=None)
    expected = {"id": "poll-abc", "total": 0, "results": []}
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = expected
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    svc._fetch_whapi_state(enq)
    _, _, unix_ts = mock_whapi.get_poll_current_state.call_args[0]
    assert unix_ts is None


# ── PollReconcileService._diff ────────────────────────────────────────────────

def test_diff_empty_when_both_empty():
    svc, _ = _make_svc(no_whapi=True)
    missing, extra = svc._diff([], _whapi_state({}))
    assert missing == []
    assert extra == []


def test_diff_missing_when_in_whapi_not_in_db():
    """Voto na WHAPI mas não no DB → aparece em missing."""
    svc, _ = _make_svc(no_whapi=True)
    state = _whapi_state({"3 peças": ["5511999999999"]})
    missing, extra = svc._diff([], state)
    assert len(missing) == 1
    assert missing[0]["phone"] == "5511999999999"


def test_diff_extra_when_in_db_not_in_whapi():
    """Voto no DB mas não na WHAPI → aparece em extra."""
    svc, _ = _make_svc(no_whapi=True)
    db_votes = [_voto(celular="5511999999999")]
    missing, extra = svc._diff(db_votes, _whapi_state({}))
    assert len(extra) == 1
    assert extra[0]["phone"] == "5511999999999"


def test_diff_in_sync_returns_empty():
    """Mesmo voto nos dois lados → missing e extra vazios."""
    svc, _ = _make_svc(no_whapi=True)
    db_votes = [_voto(celular="5511999999999")]
    state = _whapi_state({"3 peças": ["5511999999999"]})
    missing, extra = svc._diff(db_votes, state)
    assert missing == []
    assert extra == []


def test_diff_phone_variants_bridge_ddi():
    """DB com número sem DDI; WHAPI envia com DDI → reconhece como mesmo voto."""
    svc, _ = _make_svc(no_whapi=True)
    # DB tem 11999999999 (sem DDI 55)
    db_votes = [_voto(celular="11999999999")]
    # WHAPI envia 5511999999999 (com DDI 55)
    state = _whapi_state({"3 peças": ["5511999999999"]})
    missing, extra = svc._diff(db_votes, state)
    assert missing == []
    assert extra == []


def test_diff_ignores_lid_voters():
    """Voters com @lid devem ser ignorados (filtro _is_lid_or_invalid_phone)."""
    svc, _ = _make_svc(no_whapi=True)
    state = _whapi_state({"3 peças": ["123456@lid"]})
    missing, extra = svc._diff([], state)
    assert missing == []


def test_diff_multiple_voters_multiple_options():
    svc, _ = _make_svc(no_whapi=True)
    db_votes = [
        _voto(id="v1", celular="5511111111111"),
        _voto(id="v2", celular="5511222222222"),
    ]
    state = _whapi_state({
        "3 peças": ["5511111111111"],
        "6 peças": ["5511222222222", "5511333333333"],  # 333 é novo
    })
    missing, extra = svc._diff(db_votes, state)
    assert len(missing) == 1
    assert missing[0]["phone"] == "5511333333333"
    assert extra == []


def test_diff_qty_extracted_from_option_name():
    svc, _ = _make_svc(no_whapi=True)
    state = _whapi_state({"6 peças": ["5511999999999"]})
    missing, _ = svc._diff([], state)
    assert missing[0]["qty"] == 6


# ── PollReconcileService._mark_vote_removed ───────────────────────────────────

def test_mark_vote_removed_updates_status_to_out():
    voto = {"id": "v1", "qty": 3, "status": "in"}
    svc, fake = _make_svc({"votos": [voto]}, no_whapi=True)
    svc._mark_vote_removed(_enquete(), {"voto_id": "v1", "phone": "5511999999999"})
    updated = fake.tables["votos"][0]
    assert updated["status"] == "out"
    assert updated["qty"] == 0


def test_mark_vote_removed_no_op_when_voto_id_missing():
    """Entry sem voto_id não deve provocar erro nem atualização."""
    voto = {"id": "v1", "qty": 3, "status": "in"}
    svc, fake = _make_svc({"votos": [voto]}, no_whapi=True)
    svc._mark_vote_removed(_enquete(), {"phone": "5511999999999"})  # sem voto_id
    assert fake.tables["votos"][0]["status"] == "in"


# ── PollReconcileService.compare ──────────────────────────────────────────────

def test_compare_enquete_not_found_returns_error():
    svc, _ = _make_svc(no_whapi=True)
    result = svc.compare("inexistente")
    assert "error" in result
    assert result["enquete_id"] == "inexistente"


def test_compare_no_whapi_returns_warning():
    enq = _enquete()
    svc, _ = _make_svc({"enquetes": [enq]}, no_whapi=True)
    # Busca por external_poll_id pois "enq-1" não é UUID válido
    result = svc.compare("poll-abc")
    assert result.get("whapi_total") is None
    assert "warning" in result


def test_compare_in_sync_returns_true():
    enq = _enquete()
    voto = _voto(celular="5511999999999")
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq], "votos": [voto]}, mock_whapi=mock_whapi)
    result = svc.compare("poll-abc")
    assert result["in_sync"] is True
    assert result["missing_in_db"] == []
    assert result["extra_in_db"] == []


def test_compare_returns_missing_and_extra():
    enq = _enquete()
    db_votes = [_voto(celular="5511111111111")]
    state = _whapi_state({"3 peças": ["5511222222222"]})  # outro número
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq], "votos": db_votes}, mock_whapi=mock_whapi)
    result = svc.compare("poll-abc")
    assert result["in_sync"] is False
    assert len(result["missing_in_db"]) == 1
    assert len(result["extra_in_db"]) == 1


def test_compare_includes_whapi_raw():
    enq = _enquete()
    state = _whapi_state({})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)
    result = svc.compare("poll-abc")
    assert result["whapi_raw"] == state


def test_compare_returns_db_votes_count():
    enq = _enquete()
    voto = _voto(celular="5511999999999")
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq], "votos": [voto]}, mock_whapi=mock_whapi)
    result = svc.compare("poll-abc")
    assert result["db_votes"] == 1


# ── PollReconcileService.sync ─────────────────────────────────────────────────

def test_sync_enquete_not_found_returns_error():
    svc, _ = _make_svc(no_whapi=True)
    result = svc.sync("inexistente")
    assert "error" in result


def test_sync_no_whapi_returns_error():
    enq = _enquete()
    svc, _ = _make_svc({"enquetes": [enq]}, no_whapi=True)
    result = svc.sync("poll-abc")
    assert "error" in result
    assert "WHAPI" in result["error"]


def test_sync_whapi_state_none_returns_warning():
    """WHAPI configurada mas enquete não encontrada lá → warning."""
    enq = _enquete()
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = None
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)
    result = svc.sync("poll-abc")
    assert result.get("applied") == 0
    assert "warning" in result


def test_sync_in_sync_returns_zero_applied():
    """Nenhuma diferença → applied=0, removed=0."""
    enq = _enquete()
    voto = _voto(celular="5511999999999")
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq], "votos": [voto]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        mock_vote_svc = MagicMock()
        MockIngestion.return_value.vote_service = mock_vote_svc
        result = svc.sync("poll-abc")

    assert result["applied"] == 0
    assert result["removed"] == 0
    mock_vote_svc.process_vote.assert_not_called()


def test_sync_inserts_missing_vote():
    """Voto na WHAPI mas não no DB → process_vote chamado uma vez."""
    enq = _enquete()
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            mock_vote_svc = MagicMock()
            MockIngestion.return_value.vote_service = mock_vote_svc
            result = svc.sync("poll-abc")

    assert result["applied"] == 1
    mock_vote_svc.process_vote.assert_called_once()


def test_sync_removes_extra_vote():
    """Voto no DB mas não na WHAPI → _mark_vote_removed aplicado."""
    enq = _enquete()
    voto = _voto(id="v1", celular="5511999999999", qty=3, status="in")
    state = _whapi_state({})  # WHAPI vazia
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, fake = _make_svc({"enquetes": [enq], "votos": [voto]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync("poll-abc")

    assert result["removed"] == 1
    assert fake.tables["votos"][0]["status"] == "out"


def test_sync_calls_rebuild_when_changes_applied():
    """rebuild_for_poll deve ser chamado quando há applied > 0."""
    enq = _enquete()
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService") as MockPkg:
            MockIngestion.return_value.vote_service = MagicMock()
            mock_pkg_instance = MockPkg.return_value
            svc.sync("poll-abc")
            mock_pkg_instance.rebuild_for_poll.assert_called_once_with(enq["id"])


def test_sync_no_rebuild_when_no_changes():
    """rebuild_for_poll NÃO deve ser chamado quando applied=0 e removed=0."""
    enq = _enquete()
    voto = _voto(celular="5511999999999")
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq], "votos": [voto]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService") as MockPkg:
            MockIngestion.return_value.vote_service = MagicMock()
            svc.sync("poll-abc")
            MockPkg.return_value.rebuild_for_poll.assert_not_called()


def test_sync_handles_insert_exception_gracefully():
    """Erro em process_vote é capturado; applied=0, errors populado."""
    enq = _enquete()
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            mock_vote_svc = MagicMock()
            mock_vote_svc.process_vote.side_effect = RuntimeError("DB error")
            MockIngestion.return_value.vote_service = mock_vote_svc
            result = svc.sync("poll-abc")

    assert result["applied"] == 0
    assert len(result["errors"]) == 1


def test_sync_returns_title_and_whapi_total():
    """sync deve retornar titulo e whapi_total no resumo."""
    enq = _enquete(titulo="Enquete Março")
    state = _whapi_state({"3 peças": ["5511999999999"]})
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = state
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync("poll-abc")

    assert result["title"] == "Enquete Março"
    assert result["whapi_total"] == 1


# ── PollReconcileService.sync_all_open ────────────────────────────────────────

def test_sync_all_open_skips_when_no_whapi():
    svc, _ = _make_svc(no_whapi=True)
    result = svc.sync_all_open()
    assert result["skipped"] is True
    assert "WHAPI" in result["reason"]


def test_sync_all_open_returns_summary_no_enquetes():
    mock_whapi = MagicMock()
    svc, _ = _make_svc(mock_whapi=mock_whapi)
    result = svc.sync_all_open()
    assert result["enquetes_checked"] == 0
    assert result["enquetes_changed"] == 0
    assert result["total_inserted"] == 0
    assert result["total_removed"] == 0


def test_sync_all_open_processes_open_enquetes():
    """Duas enquetes abertas → sync chamado para cada uma."""
    enq1 = _enquete(id=_UUID1, external_poll_id="poll-1")
    enq2 = _enquete(id=_UUID2, external_poll_id="poll-2")
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = {"id": "poll-x", "total": 0, "results": []}
    svc, _ = _make_svc({"enquetes": [enq1, enq2]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync_all_open()

    assert result["enquetes_checked"] == 2


def test_sync_all_open_counts_changes_in_details():
    """Enquetes com mudanças aparecem em details e contam nos totais."""
    enq = _enquete()
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = _whapi_state({"3 peças": ["5511999999999"]})
    svc, _ = _make_svc({"enquetes": [enq]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync_all_open()

    assert result["enquetes_changed"] == 1
    assert result["total_inserted"] == 1


def test_sync_all_open_handles_exception_per_enquete():
    """Erro em uma enquete não deve interromper as demais."""
    enq1 = _enquete(id=_UUID1, external_poll_id="poll-1")
    enq2 = _enquete(id=_UUID2, external_poll_id="poll-2")
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = {"id": "x", "total": 0, "results": []}
    svc, _ = _make_svc({"enquetes": [enq1, enq2]}, mock_whapi=mock_whapi)

    call_count = {"n": 0}
    original_sync = svc.sync

    def patched_sync(eid):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("erro simulado")
        return original_sync(eid)

    svc.sync = patched_sync

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync_all_open()

    assert result["enquetes_checked"] == 2


def test_sync_all_open_only_checks_open_status():
    """Apenas enquetes com status=open devem ser processadas."""
    enq_open = _enquete(id=_UUID1, status="open")
    enq_closed = _enquete(id=_UUID2, status="closed")
    mock_whapi = MagicMock()
    mock_whapi.get_poll_current_state.return_value = {"id": "x", "total": 0, "results": []}
    svc, _ = _make_svc({"enquetes": [enq_open, enq_closed]}, mock_whapi=mock_whapi)

    with patch("app.services.poll_reconcile_service.WebhookIngestionService") as MockIngestion:
        with patch("app.services.poll_reconcile_service.PackageService"):
            MockIngestion.return_value.vote_service = MagicMock()
            result = svc.sync_all_open()

    # FakeSupabase filtra status=open → só enq_open é retornada
    assert result["enquetes_checked"] == 1
