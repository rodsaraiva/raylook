"""Garantia de não-regressão: o fluxo manual com enquete (mode=poll) existente
continua intocado com a introdução do módulo adhoc."""
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


def test_manual_preview_with_adhoc_enabled_still_works(monkeypatch):
    app = _boot_app(monkeypatch)

    fake_preview = {
        "poll_title": "Teste",
        "valor_col": 45.0,
        "total_qty": 24,
        "image": None,
        "image_thumb": None,
        "votes": [{"phone": "5511999999999", "name": "Maria", "qty": 24}],
    }
    monkeypatch.setattr(
        "main.build_preview_payload",
        lambda poll_id, votes: fake_preview,
    )

    client = TestClient(app)
    body = {
        "pollId": "POLL-X",
        "votes": [{"phone": "5511999999999", "qty": 24}],
    }
    response = client.post("/api/packages/manual/preview", json=body)
    assert response.status_code == 200
    assert response.json()["preview"]["total_qty"] == 24


def test_manual_preview_rejects_invalid_qty_as_before(monkeypatch):
    """Regra MANUAL_ALLOWED_QTY ({3,6,9,12,24}) continua valendo no fluxo antigo."""
    app = _boot_app(monkeypatch)

    client = TestClient(app)
    body = {
        "pollId": "POLL-X",
        "votes": [{"phone": "5511999999999", "qty": 7}],  # 7 não é allowed
    }
    response = client.post("/api/packages/manual/preview", json=body)
    assert response.status_code == 422  # Pydantic rejeita antes


def test_adhoc_allows_arbitrary_qty_between_1_and_24(monkeypatch):
    """Confirma que o fluxo novo NÃO herda a restrição de qtys do fluxo antigo."""
    app = _boot_app(monkeypatch)

    fake_client = MagicMock()
    fake_client.select.return_value = []
    monkeypatch.setattr(
        "app.api.adhoc_packages.SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake_client)),
    )

    client = TestClient(app)
    body = {
        "product": {"name": "X novo", "unit_price": 10.0, "image": {"drive_file_id": "D"}},
        "votes": [
            {"phone": "5511999999999", "qty": 7},   # 7 — ilegal no antigo, OK no novo
            {"phone": "5511988887777", "qty": 17},
        ],
    }
    response = client.post("/api/packages/adhoc/preview", json=body)
    assert response.status_code == 200
    assert response.json()["total_qty"] == 24


def test_adhoc_router_not_registered_when_flag_off(monkeypatch):
    """Com flag off, nada do módulo adhoc é registrado — fluxo antigo intocado."""
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    client = TestClient(main_module.app)
    assert client.get("/api/packages/adhoc/health").status_code == 404
    assert client.post("/api/packages/adhoc/preview", json={}).status_code == 404
    assert client.post("/api/packages/adhoc/confirm", json={}).status_code == 404
    # Fluxo antigo continua registrado
    routes = {r.path for r in main_module.app.routes}
    assert "/api/packages/manual/preview" in routes
    assert "/api/packages/manual/confirm" in routes
