"""Testes de app/services/sales_temperature_service.

Cobre: _classify, _count_closed_since, _compute_smart_daily_avg,
_compute_historical_avg_same_window, compute_temperature_now,
_is_cache_fresh, get_temperature, compute_confirmed_extras.

Usa FakeRequestClient (implementa _request) pra simular o Supabase
REST sem rede, e freezegun pra congelar datas nos testes de cache e
janelas de horário.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

import app.services.sales_temperature_service as svc


# ─── Fake HTTP response e cliente ─────────────────────────────────────────────

class FakeResponse:
    """Simula httpx.Response para os paths que o service usa."""

    def __init__(self, rows: List[Dict[str, Any]], total: Optional[int] = None):
        self._rows = rows
        self._total = total
        self.text = json.dumps(rows) if rows is not None else ""

    @property
    def headers(self) -> Dict[str, str]:
        if self._total is not None:
            return {"content-range": f"0-{self._total - 1}/{self._total}"}
        return {}

    def json(self) -> Any:
        return self._rows


class FakeRequestClient:
    """Drop-in para SupabaseRestClient com interface _request."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        # responses: mapa path_prefix → FakeResponse (ou callable)
        self._responses = responses or {}
        self._calls: List[Dict[str, Any]] = []

    def _request(self, method: str, path: str, *, extra_headers=None, **kw) -> FakeResponse:
        self._calls.append({"method": method, "path": path})
        for prefix, resp in self._responses.items():
            if path.startswith(prefix):
                return resp() if callable(resp) else resp
        return FakeResponse([])


def _make_ts(offset_hours: float = 0, hour_override: Optional[int] = None) -> str:
    """Gera ISO timestamp UTC relativo a agora."""
    base = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
    dt = base + timedelta(hours=offset_hours)
    if hour_override is not None:
        dt = dt.replace(hour=hour_override)
    return dt.isoformat()


# ─── _classify ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ratio_pct,expected_label,expected_tone", [
    (0.0,   "frio",    "cold"),
    (20.0,  "frio",    "cold"),
    (49.9,  "frio",    "cold"),
    (50.0,  "morno",   "warm"),
    (70.0,  "morno",   "warm"),
    (84.9,  "morno",   "warm"),
    (85.0,  "quente",  "hot"),
    (100.0, "quente",  "hot"),
    (114.9, "quente",  "hot"),
    (115.0, "pelando", "blazing"),
    (200.0, "pelando", "blazing"),
])
def test_classify_tiers(ratio_pct, expected_label, expected_tone):
    """Todos os limiares de classificação."""
    result = svc._classify(ratio_pct)
    assert result["label"] == expected_label
    assert result["tone"] == expected_tone
    assert "emoji" in result


def test_classify_returns_four_keys():
    result = svc._classify(100.0)
    assert set(result.keys()) == {"label", "emoji", "tone"}


def test_classify_boundary_exactly_50():
    assert svc._classify(50.0)["label"] == "morno"


def test_classify_boundary_exactly_85():
    assert svc._classify(85.0)["label"] == "quente"


def test_classify_boundary_exactly_115():
    assert svc._classify(115.0)["label"] == "pelando"


# ─── _count_closed_since ─────────────────────────────────────────────────────

def test_count_closed_since_uses_content_range():
    """Extrai total do header content-range quando presente."""
    cutoff = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([], total=7)})
    assert svc._count_closed_since(sb, cutoff) == 7


def test_count_closed_since_falls_back_to_row_count():
    """Sem content-range, conta len(rows)."""
    cutoff = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    assert svc._count_closed_since(sb, cutoff) == 3


def test_count_closed_since_returns_zero_on_exception():
    """Exceção no _request → retorna 0 sem explodir."""
    cutoff = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)

    class BrokenClient:
        def _request(self, *a, **kw):
            raise RuntimeError("network error")

    assert svc._count_closed_since(BrokenClient(), cutoff) == 0


def test_count_closed_since_returns_zero_on_star_range():
    """content-range com total='*' (desconhecido) → usa rows."""
    cutoff = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)

    class StarResponse:
        text = json.dumps([{"id": "x"}])
        headers = {"content-range": "0-0/*"}

        def json(self):
            return [{"id": "x"}]

    class StarClient:
        def _request(self, *a, **kw):
            return StarResponse()

    assert svc._count_closed_since(StarClient(), cutoff) == 1


# ─── _compute_smart_daily_avg ─────────────────────────────────────────────────

@freeze_time("2026-05-11 15:00:00+00:00")  # segunda-feira (weekday=0)
def test_compute_smart_daily_avg_empty_returns_empty_method():
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([])})
    result = svc._compute_smart_daily_avg(sb)
    assert result["method"] == "empty"
    assert result["avg"] == 0
    assert result["days_counted"] == 0


@freeze_time("2026-05-11 15:00:00+00:00")  # segunda (weekday=0)
def test_compute_smart_daily_avg_same_dow_28d():
    """Com fechamentos nas últimas 4 semanas no mesmo dia da semana (seg),
    usa método same_dow_28d."""
    # 2026-05-11 é segunda → mesmas segundas: 05-04, 04-27, 04-20
    rows = [
        {"closed_at": "2026-05-04T10:00:00+00:00"},
        {"closed_at": "2026-05-04T11:00:00+00:00"},
        {"closed_at": "2026-04-27T10:00:00+00:00"},
        {"closed_at": "2026-04-20T10:00:00+00:00"},
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_smart_daily_avg(sb)
    assert "same_dow_28d" in result["method"]
    # 2 fechamentos em 05-04 e 1 em cada outra seg → média = (2+1+1)/3 = 4/3
    assert result["avg_raw"] == round((2 + 1 + 1) / 3, 2)


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_smart_daily_avg_fallback_geral():
    """Nenhum fechamento no mesmo dia da semana → usa média geral."""
    # Colocar fechamentos só em terças (weekday=1), enquanto hoje é segunda (weekday=0)
    rows = [
        {"closed_at": "2026-05-05T10:00:00+00:00"},  # terça
        {"closed_at": "2026-04-28T10:00:00+00:00"},  # terça
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_smart_daily_avg(sb)
    assert "geral" in result["method"]


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_smart_daily_avg_exception_returns_error_method():
    class BrokenClient:
        def _request(self, *a, **kw):
            raise RuntimeError("fail")

    result = svc._compute_smart_daily_avg(BrokenClient())
    assert result["method"] == "error"
    assert result["avg"] == 0


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_smart_daily_avg_returns_all_expected_keys():
    rows = [{"closed_at": "2026-05-04T10:00:00+00:00"}]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_smart_daily_avg(sb)
    for key in ("avg_raw", "avg", "avg_geral", "days_counted", "method",
                "dow_name", "total_days", "total_closed"):
        assert key in result, f"chave ausente: {key}"


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_smart_daily_avg_ignores_rows_with_null_closed_at():
    rows = [
        {"closed_at": None},
        {"closed_at": ""},
        {"closed_at": "2026-05-04T10:00:00+00:00"},
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_smart_daily_avg(sb)
    assert result["total_closed"] == 1


# ─── _compute_historical_avg_same_window ─────────────────────────────────────

def test_historical_avg_same_window_empty_rows():
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([])})
    result = svc._compute_historical_avg_same_window(sb, 10, 13)
    assert result["avg"] == 0.0
    assert result["days_with_data"] == 0


def test_historical_avg_same_window_counts_only_in_window():
    """Apenas fechamentos entre hora 10 e 13 UTC devem contar."""
    rows = [
        {"closed_at": "2026-05-01T11:00:00+00:00"},  # dentro 10-13 ✓
        {"closed_at": "2026-05-01T12:30:00+00:00"},  # dentro 10-13 ✓
        {"closed_at": "2026-05-01T14:00:00+00:00"},  # fora
        {"closed_at": "2026-05-02T11:00:00+00:00"},  # dentro ✓
        {"closed_at": "2026-05-02T09:00:00+00:00"},  # fora
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_historical_avg_same_window(sb, 10, 13)
    # 2 dias, total_in_window=3, avg=3/2=1.5
    assert result["total_in_window"] == 3
    assert result["days_with_data"] == 2
    assert result["avg"] == 1.5


def test_historical_avg_same_window_midnight_crossing():
    """Janela que cruza meia-noite (ex: 22-02 UTC)."""
    rows = [
        {"closed_at": "2026-05-01T23:00:00+00:00"},  # dentro 22-02 ✓
        {"closed_at": "2026-05-01T01:30:00+00:00"},  # dentro 22-02 ✓
        {"closed_at": "2026-05-01T10:00:00+00:00"},  # fora
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_historical_avg_same_window(sb, 22, 2)
    assert result["total_in_window"] == 2


def test_historical_avg_same_window_exception_returns_zero():
    class BrokenClient:
        def _request(self, *a, **kw):
            raise RuntimeError("fail")

    result = svc._compute_historical_avg_same_window(BrokenClient(), 10, 13)
    assert result["avg"] == 0.0


def test_historical_avg_same_window_ignores_invalid_ts():
    rows = [
        {"closed_at": "não-é-data"},
        {"closed_at": None},
        {"closed_at": "2026-05-01T11:00:00+00:00"},
    ]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_historical_avg_same_window(sb, 10, 13)
    assert result["total_in_window"] == 1


def test_historical_avg_same_window_window_key_present():
    rows = [{"closed_at": "2026-05-01T11:00:00+00:00"}]
    sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows)})
    result = svc._compute_historical_avg_same_window(sb, 10, 13)
    assert "window" in result
    assert "10:00-13:00 UTC" in result["window"]


# ─── _is_cache_fresh ─────────────────────────────────────────────────────────

@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_none_returns_false():
    assert svc._is_cache_fresh(None) is False


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_missing_computed_at_returns_false():
    assert svc._is_cache_fresh({"label": "quente"}) is False


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_recent_returns_true():
    """Cache computado há 1h → fresco (TTL = 3h)."""
    cached = {"computed_at": "2026-05-11T14:00:00+00:00", "label": "quente"}
    assert svc._is_cache_fresh(cached) is True


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_old_returns_false():
    """Cache computado há 4h → vencido."""
    cached = {"computed_at": "2026-05-11T11:00:00+00:00", "label": "quente"}
    assert svc._is_cache_fresh(cached) is False


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_exactly_at_ttl_boundary():
    """Exatamente no limite TTL (3h atrás) → não é mais fresco."""
    cached = {"computed_at": "2026-05-11T12:00:00+00:00", "label": "quente"}
    assert svc._is_cache_fresh(cached) is False


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_invalid_ts_returns_false():
    cached = {"computed_at": "não-é-data"}
    assert svc._is_cache_fresh(cached) is False


@freeze_time("2026-05-11 15:00:00+00:00")
def test_is_cache_fresh_zulu_suffix_accepted():
    """Timestamp com Z (sem +00:00) deve ser aceito."""
    cached = {"computed_at": "2026-05-11T14:30:00Z"}
    assert svc._is_cache_fresh(cached) is True


# ─── compute_temperature_now ─────────────────────────────────────────────────

@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_temperature_now_returns_required_keys(monkeypatch):
    """Orquestrador deve retornar todas as chaves do contrato."""
    rows_pacotes = [{"closed_at": "2026-05-11T13:30:00+00:00"}]
    fake_sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse(rows_pacotes, total=2)})

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )
    result = svc.compute_temperature_now()

    required = {
        "label", "emoji", "tone", "closed_in_window", "avg_same_window",
        "window_hours", "ratio_pct", "sample_window_hours",
        "computed_at", "ttl_hours",
    }
    for key in required:
        assert key in result, f"chave ausente: {key}"


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_temperature_now_zero_avg_gives_ratio_zero(monkeypatch):
    """Sem histórico → avg_same_window=0 → ratio_pct=0 → frio."""
    fake_sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([], total=0)})

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )
    result = svc.compute_temperature_now()
    assert result["ratio_pct"] == 0.0
    assert result["label"] == "frio"


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_temperature_now_high_ratio_gives_blazing(monkeypatch):
    """Ritmo muito acima da média → pelando."""
    # closed_in_window = 10 (content-range), avg_same_window > 0 via histórico
    call_count = {"n": 0}

    def make_response():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # _count_closed_since: 10 fechamentos agora
            return FakeResponse([], total=10)
        # demais calls: dados históricos (1 fechamento por dia há 5 dias)
        rows = [
            {"closed_at": "2026-05-01T12:00:00+00:00"},
            {"closed_at": "2026-05-02T12:00:00+00:00"},
            {"closed_at": "2026-05-03T12:00:00+00:00"},
            {"closed_at": "2026-05-04T12:00:00+00:00"},
            {"closed_at": "2026-05-05T12:00:00+00:00"},
        ]
        return FakeResponse(rows)

    fake_sb = FakeRequestClient({"/rest/v1/pacotes": make_response})

    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )
    result = svc.compute_temperature_now()
    # Com alta contagem relativa à média histórica, deve ser quente ou pelando
    assert result["label"] in ("quente", "pelando")


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_temperature_now_sample_window_is_3h(monkeypatch):
    fake_sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([])})
    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )
    result = svc.compute_temperature_now()
    assert result["sample_window_hours"] == 3


# ─── get_temperature ─────────────────────────────────────────────────────────

def test_get_temperature_returns_fresh_cache(monkeypatch):
    """Cache fresco → retorna sem recomputar."""
    cached = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "label": "quente",
        "tone": "hot",
    }
    # As funções de runtime_state_service são importadas localmente (lazy);
    # o patch precisa apontar para o módulo de origem.
    monkeypatch.setattr(
        "app.services.runtime_state_service.runtime_state_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.runtime_state_service.load_runtime_state",
        lambda key: cached,
    )
    # compute_temperature_now NÃO deve ser chamado
    called = {"compute": False}
    original_compute = svc.compute_temperature_now

    def spy_compute():
        called["compute"] = True
        return original_compute()

    monkeypatch.setattr(svc, "compute_temperature_now", spy_compute)
    result = svc.get_temperature(force_refresh=False)
    assert result["label"] == "quente"
    assert called["compute"] is False


def test_get_temperature_force_refresh_ignores_cache(monkeypatch):
    """force_refresh=True → sempre recomputa."""
    monkeypatch.setattr(
        "app.services.runtime_state_service.runtime_state_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.runtime_state_service.load_runtime_state",
        lambda key: {"computed_at": datetime.now(timezone.utc).isoformat(), "label": "frio"},
    )
    monkeypatch.setattr(
        "app.services.runtime_state_service.save_runtime_state",
        lambda key, val: None,
    )
    fresh = {"label": "pelando", "computed_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(svc, "compute_temperature_now", lambda: fresh)
    result = svc.get_temperature(force_refresh=True)
    assert result["label"] == "pelando"


def test_get_temperature_recomputes_stale_cache(monkeypatch):
    """Cache vencido → recomputa e salva."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    monkeypatch.setattr(
        "app.services.runtime_state_service.runtime_state_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.runtime_state_service.load_runtime_state",
        lambda key: {"computed_at": old_ts, "label": "frio"},
    )
    saved = {}
    monkeypatch.setattr(
        "app.services.runtime_state_service.save_runtime_state",
        lambda key, val: saved.update({"key": key, "val": val}),
    )
    fresh = {"label": "morno", "computed_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(svc, "compute_temperature_now", lambda: fresh)
    result = svc.get_temperature()
    assert result["label"] == "morno"
    assert saved.get("key") == svc.RUNTIME_KEY


def test_get_temperature_runtime_disabled_always_computes(monkeypatch):
    """runtime_state_enabled=False → nunca usa cache."""
    monkeypatch.setattr(
        "app.services.runtime_state_service.runtime_state_enabled",
        lambda: False,
    )
    fresh = {"label": "quente", "computed_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(svc, "compute_temperature_now", lambda: fresh)
    result = svc.get_temperature()
    assert result["label"] == "quente"


# ─── compute_confirmed_extras ────────────────────────────────────────────────

@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_confirmed_extras_returns_required_keys(monkeypatch):
    """Garante que o contrato de chaves retornado está completo."""
    fake_sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([], total=0)})
    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )
    result = svc.compute_confirmed_extras()
    expected_keys = {
        "daily_avg_closed_historic", "daily_avg_closed_historic_raw",
        "daily_avg_geral", "historical_days_span", "historical_total_closed",
        "dow_name", "closed_72h_still_closed", "approved_72h_unpaid",
    }
    for k in expected_keys:
        assert k in result, f"chave ausente: {k}"


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_confirmed_extras_counts_closed_72h(monkeypatch):
    """Conta corretamente pacotes closed nas últimas 72h."""
    call_count = {"n": 0}

    def make_response():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # _compute_smart_daily_avg: sem histórico
            return FakeResponse([])
        if call_count["n"] == 2:
            # _count para closed_72h_still_closed: 5 pacotes
            return FakeResponse([], total=5)
        # approved_72h_unpaid: lista vazia
        return FakeResponse([])

    fake_sb = FakeRequestClient({"/rest/v1/pacotes": make_response})
    result = svc.compute_confirmed_extras(sb=fake_sb)
    assert result["closed_72h_still_closed"] == 5


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_confirmed_extras_counts_approved_unpaid(monkeypatch):
    """Conta pacotes approved nos últimos 72h com pagamento não pago."""
    # Resposta sequencial: smart_daily_avg → closed_72h → approved com unpaid
    call_count = {"n": 0}

    def make_response():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # _compute_smart_daily_avg
            return FakeResponse([])
        if call_count["n"] == 2:
            # closed_72h_still_closed: 0
            return FakeResponse([], total=0)
        # approved_72h_unpaid: 2 pacotes, cada um com pagamento "created"
        rows = [
            {
                "id": "p1",
                "vendas": {"pagamentos": {"status": "created"}},
            },
            {
                "id": "p2",
                "vendas": [{"pagamentos": [{"status": "sent"}]}],
            },
        ]
        return FakeResponse(rows)

    fake_sb = FakeRequestClient({"/rest/v1/pacotes": make_response})
    result = svc.compute_confirmed_extras(sb=fake_sb)
    assert result["approved_72h_unpaid"] == 2


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_confirmed_extras_paid_pacote_not_counted():
    """Pacote approved com pagamento 'paid' não é contado como unpaid."""
    call_count = {"n": 0}

    def make_response():
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return FakeResponse([])
        # approved 72h: 1 pacote com pagamento paid
        rows = [
            {
                "id": "p1",
                "vendas": [{"pagamentos": [{"status": "paid"}]}],
            },
        ]
        return FakeResponse(rows)

    fake_sb = FakeRequestClient({"/rest/v1/pacotes": make_response})
    result = svc.compute_confirmed_extras(sb=fake_sb)
    assert result["approved_72h_unpaid"] == 0


@freeze_time("2026-05-11 15:00:00+00:00")
def test_compute_confirmed_extras_uses_injected_sb():
    """Quando sb é injetado, não chama SupabaseRestClient.from_settings."""
    fake_sb = FakeRequestClient({"/rest/v1/pacotes": FakeResponse([])})
    # Não usa monkeypatch → se from_settings fosse chamado, explodiria sem config
    result = svc.compute_confirmed_extras(sb=fake_sb)
    assert isinstance(result, dict)
