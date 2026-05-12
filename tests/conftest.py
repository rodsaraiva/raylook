import pytest
import os
import shutil
from pathlib import Path


async def _noop_async(*args, **kwargs):
    return None


def _noop_sync(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """
    Ensure each test runs with a clean data directory.
    """
    # Create a temp data directory for the test
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir()

    # Create an empty customers.json to start clean
    customers_file = test_data_dir / "customers.json"
    customers_file.write_text("{}", encoding="utf-8")

    # Point the app to this temp directory
    monkeypatch.setenv("DATA_DIR", str(test_data_dir))

    # Mock startup.init_app to prevent background tasks from running during tests
    from app import startup
    monkeypatch.setattr(startup, "init_app", lambda app: None)

    # Stub schedulers registrados em @app.on_event("startup") de main.py.
    # Sem isso, cada TestClient(app) dispara 3 `while True` tasks que vazam
    # entre testes, contaminam event loops e travam a suite.
    from app.services import asaas_sync_service, poll_reconcile_service, payment_sync_service
    monkeypatch.setattr(asaas_sync_service, "start_asaas_sync_scheduler", _noop_async)
    monkeypatch.setattr(poll_reconcile_service, "start_poll_reconcile_scheduler", _noop_sync)
    monkeypatch.setattr(payment_sync_service, "start_payment_sync_scheduler", _noop_async)

    # Also update the Path objects in services if they've already been initialized
    # We'll use monkeypatch to update the CUSTOMERS_FILE in customer_service
    from app.services import customer_service
    monkeypatch.setattr(customer_service, "CUSTOMERS_FILE", customers_file)

    # And FinanceManager if needed
    from finance import manager
    monkeypatch.setattr(manager, "PAYMENTS_FILE", str(test_data_dir / "payments.json"))

    yield

    # Cleanup is handled by tmp_path fixture automatically
