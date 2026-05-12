"""Testes de build_aging_summary."""
from __future__ import annotations

import pytest
from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables
import app.services.finance_service as finance_service


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabaseClient(empty_tables())
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: f)
    f.tables["clientes"].append({"id": "c1", "nome": "X", "celular": "5511999990001"})
    f.tables["vendas"].extend([
        {"id": f"v{i}", "cliente_id": "c1", "pacote_id": "p1", "total_amount": 100.0}
        for i in range(1, 8)
    ])
    f.tables["pacotes"].append({"id": "p1", "enquete_id": "e1"})
    f.tables["enquetes"].append({"id": "e1", "titulo": "E"})
    return f


def _add_pag(fake, idx, status, created_at):
    fake.tables["pagamentos"].append({
        "id": f"pg{idx}", "venda_id": f"v{idx}", "status": status,
        "created_at": created_at,
    })


def test_buckets_boundaries(fake):
    # Cada pagamento R$100, datas escolhidas pra cair em cada bucket
    _add_pag(fake, 1, "sent", "2026-05-12T00:00:00+00:00")  # 0d → 0-7
    _add_pag(fake, 2, "sent", "2026-05-05T00:00:00+00:00")  # 7d → 0-7
    _add_pag(fake, 3, "sent", "2026-05-04T00:00:00+00:00")  # 8d → 8-15
    _add_pag(fake, 4, "sent", "2026-04-27T00:00:00+00:00")  # 15d → 8-15
    _add_pag(fake, 5, "sent", "2026-04-26T00:00:00+00:00")  # 16d → 16-30
    _add_pag(fake, 6, "sent", "2026-04-12T00:00:00+00:00")  # 30d → 16-30
    _add_pag(fake, 7, "sent", "2026-04-11T00:00:00+00:00")  # 31d → 30+

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")

    assert s["total_receivable"] == 700.0
    assert s["count"] == 7
    assert s["clients_count"] == 1
    assert s["buckets"]["0-7"]["amount"] == 200.0
    assert s["buckets"]["8-15"]["amount"] == 200.0
    assert s["buckets"]["16-30"]["amount"] == 200.0
    assert s["buckets"]["30+"]["amount"] == 100.0


def test_paid_rate_30d(fake):
    # 3 pagos + 1 pendente nos últimos 30d → paid_rate = 300/400 = 0.75
    _add_pag(fake, 1, "paid", "2026-05-01T00:00:00+00:00")
    _add_pag(fake, 2, "paid", "2026-04-28T00:00:00+00:00")
    _add_pag(fake, 3, "paid", "2026-04-20T00:00:00+00:00")
    _add_pag(fake, 4, "sent", "2026-05-05T00:00:00+00:00")
    # Fora da janela: deve ser ignorado
    _add_pag(fake, 5, "paid", "2026-01-01T00:00:00+00:00")

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["paid_rate_30d"] == pytest.approx(0.75)


def test_avg_age_weighted_by_value(fake):
    # v1=100/sent/10d, v2=300/sent/30d → avg = (100*10 + 300*30) / 400 = 25
    fake.tables["vendas"][0]["total_amount"] = 100.0
    fake.tables["vendas"][1]["total_amount"] = 300.0
    _add_pag(fake, 1, "sent", "2026-05-02T00:00:00+00:00")  # 10d
    _add_pag(fake, 2, "sent", "2026-04-12T00:00:00+00:00")  # 30d

    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["avg_age_days"] == pytest.approx(25.0)


def test_empty_returns_zeros(fake):
    from app.services.finance_service import build_aging_summary
    s = build_aging_summary(now_iso="2026-05-12T00:00:00+00:00")
    assert s["total_receivable"] == 0
    assert s["count"] == 0
    assert s["clients_count"] == 0
    assert s["avg_age_days"] == 0
    assert s["paid_rate_30d"] == 0
    for label in ("0-7", "8-15", "16-30", "30+"):
        assert s["buckets"][label] == {"amount": 0, "count": 0}
