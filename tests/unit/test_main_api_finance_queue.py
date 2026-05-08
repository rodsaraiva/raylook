from fastapi.testclient import TestClient

import main as main_module


def test_get_finance_queue_snapshot(monkeypatch):
    from app.services import payment_queue_service

    fake = {
        "summary": {"queued": 1, "sending": 0, "retry": 0, "error": 0, "sent": 0},
        "jobs": [{"id": "job-1", "status": "queued", "payload": {"charge_id": "c1"}}],
        "charge_jobs": {"c1": {"id": "job-1", "status": "queued", "payload": {"charge_id": "c1"}}},
        "updated_at": "2026-03-23T00:00:00+00:00",
    }
    monkeypatch.setattr(payment_queue_service, "get_queue_snapshot", lambda limit=300: fake)

    client = TestClient(main_module.app)
    res = client.get("/api/finance/queue")

    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["queued"] == 1
    assert body["jobs"][0]["id"] == "job-1"


def test_resend_charge_cancels_open_jobs_and_enqueues_new(monkeypatch):
    from app.services import payment_queue_service

    # The resend endpoint checks supabase_domain_enabled() first.
    # In staging, it uses list_finance_charges (not FinanceManager).
    monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(main_module, "supabase_domain_enabled", lambda: True)

    # Mock list_finance_charges to return a charge
    monkeypatch.setattr(
        main_module,
        "list_finance_charges",
        lambda: [
            {
                "id": "c-1",
                "package_id": "p-1",
                "customer_phone": "5562999999999",
                "customer_name": "Cliente",
                "poll_title": "Item X",
                "quantity": 2,
                "subtotal": 100.0,
                "commission_percent": 13.0,
                "image": None,
                "asaas_id": None,
            }
        ],
    )

    monkeypatch.setattr(payment_queue_service, "has_open_job_for_charge", lambda _charge_id: True)
    monkeypatch.setattr(payment_queue_service, "cancel_open_jobs_for_charge", lambda _charge_id: 2)
    monkeypatch.setattr(payment_queue_service, "enqueue_whatsapp_job", lambda _payload: "job-new-1")

    # Mock SupabaseRestClient.from_settings to avoid DB calls for image resolution
    monkeypatch.setattr(main_module.SupabaseRestClient, "from_settings", staticmethod(lambda: None))

    client = TestClient(main_module.app)
    res = client.post("/api/finance/charges/c-1/resend")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["queued"] is True
    assert body["charge_id"] == "c-1"
    assert body["job_id"] == "job-new-1"
    assert body["cancelled_jobs"] == 2
