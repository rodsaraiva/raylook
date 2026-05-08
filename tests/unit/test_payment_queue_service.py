import asyncio
from datetime import datetime, timedelta, timezone

from app.services import payment_queue_service as qsvc


def _mock_runtime_state(monkeypatch):
    """Mock the runtime state backend with an in-memory dict so tests don't hit Postgres."""
    store = {}

    def _fake_load(key):
        return store.get(key)

    def _fake_save(key, payload):
        store[key] = payload
        return payload

    monkeypatch.setattr("app.services.runtime_state_service.runtime_state_enabled", lambda: True)
    monkeypatch.setattr(qsvc, "load_runtime_state", _fake_load)
    monkeypatch.setattr(qsvc, "save_runtime_state", _fake_save)
    return store


def test_enqueue_and_detect_open_job(monkeypatch):
    _mock_runtime_state(monkeypatch)

    job_id = qsvc.enqueue_whatsapp_job(
        {
            "charge_id": "charge-1",
            "payment": {"id": "mp-1"},
            "customer_name": "Cliente",
            "phone": "5562999999999",
        }
    )

    assert isinstance(job_id, str) and len(job_id) > 0
    assert qsvc.has_open_job_for_charge("charge-1") is True


def test_recover_stuck_jobs_moves_sending_to_queued(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-1",
                "kind": "whatsapp_send",
                "status": "sending",
                "attempts": 1,
                "max_attempts": 3,
                "next_attempt_at": stale_ts,
                "created_at": stale_ts,
                "updated_at": stale_ts,
                "payload": {"charge_id": "charge-x"},
                "last_error": None,
            }
        ]
    }

    recovered = qsvc.recover_stuck_jobs(stale_seconds=60)
    assert recovered == 1
    assert qsvc.has_open_job_for_charge("charge-x") is True


def test_get_queue_snapshot_has_summary_and_charge_map(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-queued",
                "kind": "whatsapp_send",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "next_attempt_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
                "payload": {"charge_id": "charge-1", "customer_name": "A"},
                "last_error": None,
            },
            {
                "id": "job-error",
                "kind": "whatsapp_send",
                "status": "error",
                "attempts": 3,
                "max_attempts": 3,
                "next_attempt_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
                "payload": {"charge_id": "charge-2", "customer_name": "B"},
                "last_error": "falha",
            },
        ]
    }

    snap = qsvc.get_queue_snapshot(limit=50)
    assert snap["summary"]["queued"] == 1
    assert snap["summary"]["error"] == 1
    assert "charge-1" in snap["charge_jobs"]
    assert "charge-2" in snap["charge_jobs"]


def test_cancel_open_jobs_for_charge_marks_jobs_as_cancelled(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-queued",
                "kind": "whatsapp_send",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "next_attempt_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"},
                "last_error": None,
            },
            {
                "id": "job-sending",
                "kind": "whatsapp_send",
                "status": "sending",
                "attempts": 1,
                "max_attempts": 3,
                "next_attempt_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"},
                "last_error": None,
            },
            {
                "id": "job-sent",
                "kind": "whatsapp_send",
                "status": "sent",
                "attempts": 1,
                "max_attempts": 3,
                "next_attempt_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"},
                "last_error": None,
            },
        ]
    }

    cancelled = qsvc.cancel_open_jobs_for_charge("charge-1")
    assert cancelled == 2

    snap = qsvc.get_queue_snapshot(limit=50)
    assert snap["summary"]["cancelled"] == 2
    assert snap["summary"]["sent"] == 1
    assert qsvc.has_open_job_for_charge("charge-1") is False


def test_enqueue_whatsapp_job_preserves_phone_in_payload(monkeypatch):
    """F-051: test phone override moved to _process_job (routing_service.resolve_test_phone).
    enqueue_whatsapp_job now stores the original phone; override happens at send time."""
    _mock_runtime_state(monkeypatch)

    qsvc.enqueue_whatsapp_job(
        {
            "charge_id": "charge-1",
            "payment": {"id": "mp-1"},
            "customer_name": "Cliente",
            "phone": "5511999999999",
        }
    )

    payload = qsvc._load_queue_unlocked()["jobs"][0]["payload"]
    assert payload["phone"] == "5511999999999"


def test_process_job_v2_uses_staging_fallback_when_mercadopago_returns_5xx(monkeypatch):
    """F-040: _process_job creates Asaas payment on-demand.
    This test is skipped because _process_job_v2 was replaced by _process_job
    which now uses AsaasClient instead of MercadoPagoClient with staging fallback."""
    import pytest
    pytest.skip("F-051: _process_job_v2 removed; _process_job now uses AsaasClient directly")
