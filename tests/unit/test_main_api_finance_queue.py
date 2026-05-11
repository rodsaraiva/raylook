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


def test_resend_charge_endpoint_is_gone():
    """F-035: envio de cobrança via WhatsApp foi desativado — clientes pagam
    pelo portal. O endpoint resend agora devolve 410 e o teste garante que
    ninguém reanima o fluxo sem revisão."""
    client = TestClient(main_module.app)
    res = client.post("/api/finance/charges/c-1/resend")

    assert res.status_code == 410
    detail = (res.json().get("detail") or "").lower()
    assert "desativado" in detail or "portal" in detail
