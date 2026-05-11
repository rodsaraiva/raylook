"""Testes de app/services/asaas_sync_service.py.

Categorias cobertas:
  - Puras: _normalize_asaas_paid_at, ASAAS_PAID_STATUSES
  - DB+HTTP mockado: sync_asaas_payments, _sync_combined_pix
  - Pulado: start_asaas_sync_scheduler (loop infinito async)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import app.services.asaas_sync_service as svc
from tests._helpers.fake_supabase import FakeSupabaseClient


class FakeSupabaseClientExt(FakeSupabaseClient):
    """Extensão local que aceita kwargs extras no update (ex: returning='minimal')."""

    def update(self, table, values, *, filters=None, **kwargs):
        return super().update(table, values, filters=filters)


# ── helpers ──────────────────────────────────────────────────────────────────

def _fake_asaas(statuses: dict[str, str], payments: dict[str, dict] | None = None):
    """Cria um mock de AsaasClient para testes."""
    asaas = MagicMock()
    asaas.get_payment_status.side_effect = lambda asaas_id: statuses.get(asaas_id, "PENDING")
    asaas.get_payment.side_effect = lambda asaas_id: (payments or {}).get(asaas_id, {"status": statuses.get(asaas_id, "PENDING")})
    return asaas


def _tables(pagamentos=None, states=None):
    return {
        "pagamentos": pagamentos or [],
        "app_runtime_state": states or [],
    }


# ── _normalize_asaas_paid_at — pura ──────────────────────────────────────────

def test_normalize_data_pura_retorna_brt_como_utc():
    """Data YYYY-MM-DD do Asaas (BRT) deve virar 03:00 UTC no mesmo dia."""
    result = svc._normalize_asaas_paid_at({"confirmedDate": "2026-04-17"})
    assert result == "2026-04-17T03:00:00+00:00"


def test_normalize_data_pura_via_paymentDate():
    """paymentDate é fallback quando confirmedDate ausente."""
    result = svc._normalize_asaas_paid_at({"paymentDate": "2026-01-01"})
    assert result == "2026-01-01T03:00:00+00:00"


def test_normalize_confirmedDate_tem_prioridade_sobre_paymentDate():
    """confirmedDate prevalece quando ambas estão presentes."""
    result = svc._normalize_asaas_paid_at({
        "confirmedDate": "2026-03-10",
        "paymentDate": "2026-03-09",
    })
    assert result == "2026-03-10T03:00:00+00:00"


def test_normalize_iso_datetime_devolve_como_esta():
    """ISO datetime completo é devolvido sem conversão."""
    iso = "2026-04-17T10:30:00-03:00"
    result = svc._normalize_asaas_paid_at({"confirmedDate": iso})
    assert result == iso


def test_normalize_sem_data_retorna_now_utc():
    """Sem confirmedDate nem paymentDate, fallback é now() UTC."""
    result = svc._normalize_asaas_paid_at({})
    # Deve ser um ISO string contendo 'T' e não vazio
    assert "T" in result
    assert len(result) > 10


def test_normalize_data_invalida_retorna_raw():
    """Data malformada cai no except e devolve o raw string."""
    # Tem 10 chars e os hifens na posição certa, mas mês inválido.
    result = svc._normalize_asaas_paid_at({"confirmedDate": "2026-13-01"})
    assert result == "2026-13-01"


# ── ASAAS_PAID_STATUSES ───────────────────────────────────────────────────────

def test_paid_statuses_contem_os_tres_valores():
    assert svc.ASAAS_PAID_STATUSES == {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}


# ── sync_asaas_payments — sandbox retorna 0 ───────────────────────────────────

@pytest.mark.asyncio
async def test_sync_retorna_zero_em_sandbox(monkeypatch):
    """Em sandbox (RAYLOOK_SANDBOX=True), sync é no-op."""
    class FakeSettings:
        RAYLOOK_SANDBOX = True

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    result = await svc.sync_asaas_payments()
    assert result == 0


@pytest.mark.asyncio
async def test_sync_retorna_zero_quando_supabase_domain_disabled(monkeypatch):
    """supabase_domain_enabled() == False → retorna 0 sem tocar no BD."""
    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())
    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: False)

    result = await svc.sync_asaas_payments()
    assert result == 0


# ── sync_asaas_payments — caminho 1 (pagamentos individuais) ──────────────────

@pytest.mark.asyncio
async def test_sync_marca_pagamento_individual_como_paid(monkeypatch):
    """Pagamento individual com status RECEIVED é atualizado para 'paid'."""
    fake_sb = FakeSupabaseClientExt(_tables(
        pagamentos=[
            {"id": "pag-1", "provider_payment_id": "asaas-111", "status": "created"},
        ]
    ))

    asaas = _fake_asaas(
        statuses={"asaas-111": "RECEIVED"},
        payments={"asaas-111": {"confirmedDate": "2026-04-17"}},
    )

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    # Evita importar AsaasClient real
    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    # Stub de refresh snapshots
    import app.services.finance_service as fin
    import app.services.customer_service as cust
    monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: None)

    result = await svc.sync_asaas_payments()

    assert result == 1
    pag = fake_sb.tables["pagamentos"][0]
    assert pag["status"] == "paid"
    assert pag["paid_at"] == "2026-04-17T03:00:00+00:00"


@pytest.mark.asyncio
async def test_sync_nao_atualiza_pagamento_pendente(monkeypatch):
    """Asaas retorna PENDING → pagamento não é alterado."""
    fake_sb = FakeSupabaseClientExt(_tables(
        pagamentos=[
            {"id": "pag-2", "provider_payment_id": "asaas-222", "status": "sent"},
        ]
    ))

    asaas = _fake_asaas(statuses={"asaas-222": "PENDING"})

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    result = await svc.sync_asaas_payments()

    assert result == 0
    assert fake_sb.tables["pagamentos"][0]["status"] == "sent"


@pytest.mark.asyncio
async def test_sync_todos_paid_statuses_reconhecidos(monkeypatch):
    """RECEIVED, CONFIRMED e RECEIVED_IN_CASH marcam pagamento como paid."""
    for status in ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"):
        fake_sb = FakeSupabaseClientExt(_tables(
            pagamentos=[
                {"id": "pag-x", "provider_payment_id": "asaas-x", "status": "created"},
            ]
        ))

        asaas = _fake_asaas(
            statuses={"asaas-x": status},
            payments={"asaas-x": {"confirmedDate": "2026-05-01"}},
        )

        monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )

        class FakeSettings:
            RAYLOOK_SANDBOX = False

        import app.config as cfg_module
        monkeypatch.setattr(cfg_module, "settings", FakeSettings())

        import integrations.asaas.client as asaas_mod
        monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

        import app.services.finance_service as fin
        import app.services.customer_service as cust
        monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: None)
        monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: None)
        monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: None)

        result = await svc.sync_asaas_payments()
        assert result == 1, f"status={status} não marcou como paid"


@pytest.mark.asyncio
async def test_sync_ignora_pagamento_sem_provider_payment_id(monkeypatch):
    """Pagamentos sem provider_payment_id são filtrados antes de consultar Asaas."""
    fake_sb = FakeSupabaseClientExt(_tables(
        pagamentos=[
            {"id": "pag-3", "provider_payment_id": None, "status": "created"},
            {"id": "pag-4", "provider_payment_id": "", "status": "sent"},
        ]
    ))

    asaas = _fake_asaas(statuses={})

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    result = await svc.sync_asaas_payments()
    assert result == 0
    # get_payment_status não deve ter sido chamado
    asaas.get_payment_status.assert_not_called()


@pytest.mark.asyncio
async def test_sync_continua_apos_falha_em_um_pagamento(monkeypatch):
    """Erro ao consultar um pagamento individual não para o loop."""
    fake_sb = FakeSupabaseClientExt(_tables(
        pagamentos=[
            {"id": "pag-a", "provider_payment_id": "asaas-a", "status": "created"},
            {"id": "pag-b", "provider_payment_id": "asaas-b", "status": "created"},
        ]
    ))

    asaas = MagicMock()
    # Primeiro falha, segundo retorna RECEIVED
    asaas.get_payment_status.side_effect = [
        Exception("timeout"),
        "RECEIVED",
    ]
    asaas.get_payment.return_value = {"confirmedDate": "2026-05-02"}

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    import app.services.finance_service as fin
    import app.services.customer_service as cust
    monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: None)

    result = await svc.sync_asaas_payments()
    # Apenas pag-b foi atualizado
    assert result == 1
    assert fake_sb.tables["pagamentos"][0]["status"] == "created"  # pag-a intacto
    assert fake_sb.tables["pagamentos"][1]["status"] == "paid"     # pag-b atualizado


@pytest.mark.asyncio
async def test_sync_multiplos_pagamentos_todos_paid(monkeypatch):
    """Dois pagamentos pendentes, ambos confirmados → updated_count == 2."""
    fake_sb = FakeSupabaseClientExt(_tables(
        pagamentos=[
            {"id": "p1", "provider_payment_id": "a1", "status": "created"},
            {"id": "p2", "provider_payment_id": "a2", "status": "sent"},
        ]
    ))

    asaas = _fake_asaas(
        statuses={"a1": "CONFIRMED", "a2": "RECEIVED"},
        payments={
            "a1": {"confirmedDate": "2026-05-01"},
            "a2": {"confirmedDate": "2026-05-02"},
        },
    )

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    import app.services.finance_service as fin
    import app.services.customer_service as cust
    monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: None)

    result = await svc.sync_asaas_payments()
    assert result == 2


# ── _sync_combined_pix ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_combined_retorna_zero_sem_states():
    """Sem entradas combined_pix em app_runtime_state → 0."""
    fake_sb = FakeSupabaseClientExt(_tables())
    asaas = _fake_asaas({})
    result = await svc._sync_combined_pix(fake_sb, asaas)
    assert result == 0


@pytest.mark.asyncio
async def test_sync_combined_marca_pagamentos_individuais(monkeypatch):
    """PIX combinado pago no Asaas propaga paid pra todos os IDs do payload."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [
            {"id": "ind-1", "status": "created"},
            {"id": "ind-2", "status": "sent"},
        ],
        "app_runtime_state": [
            {
                "key": "combined_pix_asaas-combo-1",
                "payload_json": {"pagamento_ids": ["ind-1", "ind-2"]},
            }
        ],
    })

    asaas = _fake_asaas(
        statuses={},
        payments={"asaas-combo-1": {"status": "RECEIVED", "confirmedDate": "2026-05-05"}},
    )

    result = await svc._sync_combined_pix(fake_sb, asaas)
    assert result == 2
    for p in fake_sb.tables["pagamentos"]:
        assert p["status"] == "paid"
        assert p["paid_at"] == "2026-05-05T03:00:00+00:00"


@pytest.mark.asyncio
async def test_sync_combined_nao_marca_se_asaas_pending():
    """Combinado com status PENDING não propaga nada."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [{"id": "ind-1", "status": "created"}],
        "app_runtime_state": [
            {
                "key": "combined_pix_asaas-combo-2",
                "payload_json": {"pagamento_ids": ["ind-1"]},
            }
        ],
    })

    asaas = _fake_asaas(
        statuses={},
        payments={"asaas-combo-2": {"status": "PENDING"}},
    )

    result = await svc._sync_combined_pix(fake_sb, asaas)
    assert result == 0
    assert fake_sb.tables["pagamentos"][0]["status"] == "created"


@pytest.mark.asyncio
async def test_sync_combined_pula_pagamentos_ja_paid():
    """Pagamentos individuais já paid não são atualizados novamente."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [
            {"id": "ind-1", "status": "paid", "paid_at": "2026-04-01T03:00:00+00:00"},
            {"id": "ind-2", "status": "created"},
        ],
        "app_runtime_state": [
            {
                "key": "combined_pix_asaas-combo-3",
                "payload_json": {"pagamento_ids": ["ind-1", "ind-2"]},
            }
        ],
    })

    asaas = _fake_asaas(
        statuses={},
        payments={"asaas-combo-3": {"status": "CONFIRMED", "confirmedDate": "2026-05-06"}},
    )

    result = await svc._sync_combined_pix(fake_sb, asaas)
    # Apenas ind-2 atualizado
    assert result == 1
    ind1 = next(p for p in fake_sb.tables["pagamentos"] if p["id"] == "ind-1")
    assert ind1["paid_at"] == "2026-04-01T03:00:00+00:00"  # intacto


@pytest.mark.asyncio
async def test_sync_combined_pula_state_sem_pag_ids():
    """State com payload_json vazio ou sem pagamento_ids é ignorado."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [],
        "app_runtime_state": [
            {"key": "combined_pix_asaas-x", "payload_json": {}},
            {"key": "combined_pix_asaas-y", "payload_json": {"pagamento_ids": []}},
        ],
    })
    asaas = _fake_asaas({})
    result = await svc._sync_combined_pix(fake_sb, asaas)
    assert result == 0
    asaas.get_payment.assert_not_called()


@pytest.mark.asyncio
async def test_sync_combined_continua_apos_falha_http():
    """Erro ao consultar um combinado não para o loop; outros são processados."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [
            {"id": "ind-ok", "status": "created"},
        ],
        "app_runtime_state": [
            {
                "key": "combined_pix_falha",
                "payload_json": {"pagamento_ids": ["qualquer"]},
            },
            {
                "key": "combined_pix_ok",
                "payload_json": {"pagamento_ids": ["ind-ok"]},
            },
        ],
    })

    asaas = MagicMock()
    asaas.get_payment.side_effect = [
        Exception("connection error"),
        {"status": "RECEIVED", "confirmedDate": "2026-05-07"},
    ]

    result = await svc._sync_combined_pix(fake_sb, asaas)
    assert result == 1
    assert fake_sb.tables["pagamentos"][0]["status"] == "paid"


# ── sync_asaas_payments integra _sync_combined_pix ───────────────────────────

@pytest.mark.asyncio
async def test_sync_conta_combinados_no_total(monkeypatch):
    """updated_count inclui combinados além dos individuais."""
    fake_sb = FakeSupabaseClientExt({
        "pagamentos": [
            {"id": "ind-combo", "status": "sent"},
        ],
        "app_runtime_state": [
            {
                "key": "combined_pix_asaas-c",
                "payload_json": {"pagamento_ids": ["ind-combo"]},
            }
        ],
    })

    asaas = _fake_asaas(
        statuses={},
        payments={"asaas-c": {"status": "RECEIVED_IN_CASH", "confirmedDate": "2026-05-08"}},
    )

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    import app.services.finance_service as fin
    import app.services.customer_service as cust
    monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: None)
    monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: None)
    monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: None)

    result = await svc.sync_asaas_payments()
    assert result == 1
    assert fake_sb.tables["pagamentos"][0]["status"] == "paid"


@pytest.mark.asyncio
async def test_sync_nao_chama_refresh_quando_zero_atualizacoes(monkeypatch):
    """refresh_* não são chamadas quando nenhum pagamento foi atualizado."""
    fake_sb = FakeSupabaseClientExt(_tables())

    asaas = _fake_asaas({})

    monkeypatch.setattr("app.services.asaas_sync_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.asaas_sync_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_sb),
    )

    class FakeSettings:
        RAYLOOK_SANDBOX = False

    import app.config as cfg_module
    monkeypatch.setattr(cfg_module, "settings", FakeSettings())

    import integrations.asaas.client as asaas_mod
    monkeypatch.setattr(asaas_mod, "AsaasClient", lambda: asaas)

    refresh_called = []

    import app.services.finance_service as fin
    import app.services.customer_service as cust
    monkeypatch.setattr(fin, "refresh_charge_snapshot", lambda: refresh_called.append("charge"))
    monkeypatch.setattr(fin, "refresh_dashboard_stats", lambda: refresh_called.append("stats"))
    monkeypatch.setattr(cust, "refresh_customer_rows_snapshot", lambda: refresh_called.append("cust"))

    result = await svc.sync_asaas_payments()
    assert result == 0
    assert refresh_called == [], "refresh não deve ser chamado se não houve atualizações"
