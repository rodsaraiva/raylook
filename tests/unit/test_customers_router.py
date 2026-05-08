from fastapi.testclient import TestClient

import main as main_module


def test_get_customers_returns_paginated_payload(monkeypatch):
    expected = {
        "items": [{"phone": "5511999999999", "name": "Maria", "qty": 3, "total_paid": 120.5}],
        "total": 121,
        "page": 2,
        "page_size": 25,
        "has_prev": True,
        "has_next": False,
    }

    def fake_list_customer_rows_page(*, page, page_size, search):
        assert page == 2
        assert page_size == 25
        assert search == "maria"
        return expected

    monkeypatch.setattr("app.routers.customers.list_customer_rows_page", fake_list_customer_rows_page)

    client = TestClient(main_module.app)
    response = client.get("/api/customers/?page=2&page_size=25&search=maria")

    assert response.status_code == 200
    assert response.json() == expected


def test_patch_customer_updates_name(monkeypatch):
    calls = {}

    def fake_update_customer(phone, name):
        calls["phone"] = phone
        calls["name"] = name

    monkeypatch.setattr("app.routers.customers.update_customer", fake_update_customer)

    client = TestClient(main_module.app)
    response = client.patch("/api/customers/5511999999999", json={"name": "Maria"})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert calls == {"phone": "5511999999999", "name": "Maria"}


def test_customers_search_returns_light_payload(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    def fake_search(q, limit):
        assert q == "mar"
        assert limit == 10
        return [
            {"phone": "5511999999999", "name": "Maria Silva"},
            {"phone": "5511988887777", "name": "Marcos"},
        ]

    monkeypatch.setattr("app.routers.customers.search_customers_light", fake_search)

    from fastapi.testclient import TestClient
    client = TestClient(main_module.app)
    response = client.get("/api/customers/search?q=mar")

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {"phone": "5511999999999", "name": "Maria Silva"},
            {"phone": "5511988887777", "name": "Marcos"},
        ]
    }


def test_customers_search_short_query_returns_empty(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    client = TestClient(main_module.app)
    # Query vazia ou com 1 char deve retornar vazio sem tocar o banco
    response = client.get("/api/customers/search?q=")
    assert response.status_code == 200
    assert response.json() == {"results": []}
