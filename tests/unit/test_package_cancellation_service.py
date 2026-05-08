"""Testes do package_cancellation_service.

Cobre:
  - Cancelamento limpo (sem pagamentos pagos) — cascade completo.
  - Bloqueio quando há pagamento pago e force=False.
  - Cancelamento forçado preservando pagos.
  - Idempotência (já cancelado).
  - Erro quando pacote não existe.
"""
from unittest.mock import MagicMock

import pytest


def _make_fake_client(package_status="approved", sales=None):
    """Fake SupabaseRestClient com respostas canned."""
    sales = sales or []

    fake = MagicMock()

    def fake_select(table, columns=None, filters=None, limit=None):
        if table == "pacotes":
            if package_status is None:
                return []
            return [{"id": "PKG-1", "status": package_status, "enquete_id": "E-1"}]
        return []

    def fake_select_all(table, columns=None, filters=None, order=None):
        if table == "vendas":
            return sales
        return []

    fake.select.side_effect = fake_select
    fake.select_all.side_effect = fake_select_all

    patch_calls = []

    def fake_request(method, path, payload=None, prefer=None):
        resp = MagicMock()
        resp.status_code = 204
        if method == "PATCH":
            patch_calls.append({"path": path, "payload": payload})
        return resp

    fake._request.side_effect = fake_request
    fake.patch_calls = patch_calls
    return fake


def _install(monkeypatch, fake):
    from app.services import package_cancellation_service as pcs
    monkeypatch.setattr(pcs, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        pcs,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake)),
    )
    return pcs


def test_cancel_clean_no_paid(monkeypatch):
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 3,
            "total_amount": 60, "cliente_id": "C1",
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": {"id": "P1", "status": "sent", "paid_at": None},
        },
        {
            "id": "V2", "status": "approved", "qty": 6,
            "total_amount": 120, "cliente_id": "C2",
            "cliente": {"nome": "Bia", "celular": "5511888"},
            "pagamento": {"id": "P2", "status": "created", "paid_at": None},
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    result = pcs.cancel_package("PKG-1", force=False, cancelled_by="admin")

    assert result["cancelled_sales"] == 2
    assert result["cancelled_payments"] == 2
    assert result["preserved_paid"] == 0

    # 2 PATCH vendas + 2 PATCH pagamentos + 1 PATCH pacote
    assert len(fake.patch_calls) == 5
    pkg_patch = [c for c in fake.patch_calls if "/pacotes?" in c["path"]]
    assert len(pkg_patch) == 1
    assert pkg_patch[0]["payload"]["status"] == "cancelled"
    assert pkg_patch[0]["payload"]["cancelled_by"] == "admin"
    assert "cancelled_at" in pkg_patch[0]["payload"]


def test_cancel_blocked_when_paid_and_not_forced(monkeypatch):
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 3,
            "total_amount": 60, "cliente_id": "C1",
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": {"id": "P1", "status": "paid", "paid_at": "2026-04-19T10:00:00Z"},
        },
        {
            "id": "V2", "status": "approved", "qty": 6,
            "total_amount": 120, "cliente_id": "C2",
            "cliente": {"nome": "Bia", "celular": "5511888"},
            "pagamento": {"id": "P2", "status": "sent", "paid_at": None},
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    with pytest.raises(pcs.PackageCancelBlocked) as exc_info:
        pcs.cancel_package("PKG-1", force=False)

    assert len(exc_info.value.paid_info) == 1
    assert exc_info.value.paid_info[0]["cliente_nome"] == "Ana"
    assert exc_info.value.paid_info[0]["pagamento_id"] == "P1"
    # Nenhum PATCH deve ter acontecido
    assert fake.patch_calls == []


def test_cancel_forced_preserves_paid(monkeypatch):
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 3,
            "total_amount": 60, "cliente_id": "C1",
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": {"id": "P1", "status": "paid", "paid_at": "2026-04-19T10:00:00Z"},
        },
        {
            "id": "V2", "status": "approved", "qty": 6,
            "total_amount": 120, "cliente_id": "C2",
            "cliente": {"nome": "Bia", "celular": "5511888"},
            "pagamento": {"id": "P2", "status": "sent", "paid_at": None},
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    result = pcs.cancel_package("PKG-1", force=True)

    assert result["cancelled_sales"] == 1
    assert result["cancelled_payments"] == 1
    assert result["preserved_paid"] == 1

    # A venda V1/pagamento P1 não deve aparecer nos PATCHes (preservados)
    venda_paths = [c["path"] for c in fake.patch_calls if "/vendas?" in c["path"]]
    pagamento_paths = [c["path"] for c in fake.patch_calls if "/pagamentos?" in c["path"]]
    assert any("eq.V2" in p for p in venda_paths)
    assert not any("eq.V1" in p for p in venda_paths)
    assert any("eq.P2" in p for p in pagamento_paths)
    assert not any("eq.P1" in p for p in pagamento_paths)


def test_cancel_idempotent_when_already_cancelled(monkeypatch):
    fake = _make_fake_client(package_status="cancelled", sales=[])
    pcs = _install(monkeypatch, fake)

    result = pcs.cancel_package("PKG-1", force=False)

    assert result["already_cancelled"] is True
    assert fake.patch_calls == []


def test_cancel_raises_when_package_missing(monkeypatch):
    fake = _make_fake_client(package_status=None)
    pcs = _install(monkeypatch, fake)

    with pytest.raises(pcs.PackageNotFound):
        pcs.cancel_package("PKG-XXX", force=False)


def test_preview_returns_paid_summary(monkeypatch):
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 3,
            "total_amount": 60,
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": {"id": "P1", "status": "paid", "paid_at": "2026-04-19T10:00:00Z"},
        },
        {
            "id": "V2", "status": "approved", "qty": 6,
            "total_amount": 120,
            "cliente": {"nome": "Bia", "celular": "5511888"},
            "pagamento": {"id": "P2", "status": "sent", "paid_at": None},
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    info = pcs.preview_cancel("PKG-1")
    assert info["paid_count"] == 1
    assert info["pending_count"] == 1
    assert info["paid_clients"][0]["cliente_nome"] == "Ana"


def test_embedded_payment_as_list_is_normalized(monkeypatch):
    # PostgREST às vezes retorna o embed 1:1 como lista de 1 elemento
    sales = [
        {
            "id": "V1", "status": "approved", "qty": 3,
            "total_amount": 60, "cliente_id": "C1",
            "cliente": {"nome": "Ana", "celular": "5511999"},
            "pagamento": [{"id": "P1", "status": "sent", "paid_at": None}],
        },
    ]
    fake = _make_fake_client(sales=sales)
    pcs = _install(monkeypatch, fake)

    result = pcs.cancel_package("PKG-1", force=False)
    assert result["cancelled_payments"] == 1
