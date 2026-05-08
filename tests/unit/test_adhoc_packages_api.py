import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient


def _boot_app(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)
    return main_module.app


def test_health_returns_ok_when_flag_enabled(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/api/packages/adhoc/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_404_when_flag_disabled(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    client = TestClient(main_module.app)
    response = client.get("/api/packages/adhoc/health")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# helpers compartilhados entre preview e confirm
# ---------------------------------------------------------------------------

def _preview_body():
    return {
        "product": {"name": "Vestido Floral", "unit_price": 45.00, "image": {"drive_file_id": "D1"}},
        "votes": [
            {"phone": "5511999999999", "qty": 10, "customer_id": None},
            {"phone": "5511988887777", "qty": 14, "customer_id": None},
        ],
    }


# ---------------------------------------------------------------------------
# Task 7 — /preview
# ---------------------------------------------------------------------------

def test_preview_returns_totals(monkeypatch):
    app = _boot_app(monkeypatch)

    class FakeClient:
        def select(self, table, **kw):
            return [{"celular": "5511999999999", "nome": "Maria"}]

    monkeypatch.setattr(
        "app.api.adhoc_packages.SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=FakeClient())),
    )

    response = TestClient(app).post("/api/packages/adhoc/preview", json=_preview_body())
    assert response.status_code == 200
    body = response.json()
    assert body["total_qty"] == 24
    assert body["subtotal"] == 24 * 45.00
    assert body["commission_percent"] > 0
    assert body["total_final"] == round(body["subtotal"] + body["commission_amount"], 2)
    assert len(body["votes_resolved"]) == 2


def test_preview_rejects_sum_not_24(monkeypatch):
    app = _boot_app(monkeypatch)
    body = _preview_body()
    body["votes"][0]["qty"] = 1
    response = TestClient(app).post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 400
    assert "24" in response.json()["detail"]


def test_preview_rejects_bad_phone(monkeypatch):
    app = _boot_app(monkeypatch)
    body = _preview_body()
    body["votes"][0]["phone"] = "123"
    response = TestClient(app).post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Task 8 — /confirm
# ---------------------------------------------------------------------------

def test_confirm_persists_and_returns_package_id(monkeypatch):
    app = _boot_app(monkeypatch)

    fake_service = MagicMock(return_value={"package_id": "PKG-1", "legacy_package_id": "adhoc_PKG-1"})
    monkeypatch.setattr("app.api.adhoc_packages.create_adhoc_package", fake_service)

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
    body = _preview_body()
    body["votes"][0]["qty"] = 1
    response = TestClient(app).post("/api/packages/adhoc/confirm", json=body)
    assert response.status_code == 400
