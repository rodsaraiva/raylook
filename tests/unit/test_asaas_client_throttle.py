import time

import integrations.asaas.client as client_mod
from integrations.asaas.client import AsaasClient
from integrations.asaas.rate_limiter import RateLimiter


class _FakeResp:
    ok = True
    status_code = 200
    text = "{}"

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def test_real_path_throttled_across_instances(monkeypatch):
    # Throttle compartilhado: 4 instâncias diferentes, 1 request cada,
    # ainda assim serializadas pelo singleton de módulo.
    monkeypatch.setattr(client_mod, "_sandbox_enabled", lambda: False)
    monkeypatch.setattr(client_mod, "_LIMITER", RateLimiter(0.05))
    monkeypatch.setattr(client_mod.requests, "request", lambda *a, **k: _FakeResp())

    clients = [AsaasClient(api_key="x", base_url="https://e/v3/") for _ in range(4)]
    start = time.monotonic()
    for c in clients:
        c._request("GET", "payments/abc")
    assert time.monotonic() - start >= 3 * 0.05


def test_sandbox_path_not_throttled(monkeypatch):
    monkeypatch.setattr(client_mod, "_sandbox_enabled", lambda: True)
    monkeypatch.setattr(client_mod, "_LIMITER", RateLimiter(5.0))

    c = AsaasClient(api_key="x", base_url="https://e/v3/")
    start = time.monotonic()
    for _ in range(3):
        c._request("GET", "payments/abc")
    assert time.monotonic() - start < 1.0


def test_min_interval_from_env(monkeypatch):
    monkeypatch.setenv("ASAAS_MAX_RPS", "4")
    assert client_mod._min_interval_from_env() == 0.25
    monkeypatch.setenv("ASAAS_MAX_RPS", "0")
    assert client_mod._min_interval_from_env() == 0.0
    monkeypatch.setenv("ASAAS_MAX_RPS", "lixo")
    assert client_mod._min_interval_from_env() == 0.5  # fallback default 2 rps
