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


# ---------------------------------------------------------------------------
# _utcnow_iso
# ---------------------------------------------------------------------------

def test_utcnow_iso_returns_iso_string():
    ts = qsvc._utcnow_iso()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# _normalize_job_payload
# ---------------------------------------------------------------------------

def test_normalize_job_payload_retorna_copia():
    original = {"charge_id": "x", "phone": "5511"}
    result = qsvc._normalize_job_payload(original)
    assert result == original
    assert result is not original  # cópia


def test_normalize_job_payload_none_retorna_dict_vazio():
    result = qsvc._normalize_job_payload(None)
    assert result == {}


# ---------------------------------------------------------------------------
# _load_queue_unlocked / _save_queue_unlocked
# ---------------------------------------------------------------------------

def test_load_queue_unlocked_sem_dados_retorna_vazio(monkeypatch):
    _mock_runtime_state(monkeypatch)
    data = qsvc._load_queue_unlocked()
    assert data == {"jobs": []}


def test_load_queue_unlocked_dado_invalido_retorna_vazio(monkeypatch):
    """Quando runtime_state retorna dado corrompido, fallback é fila vazia."""
    monkeypatch.setattr(qsvc, "load_runtime_state", lambda key: "string_invalida")
    data = qsvc._load_queue_unlocked()
    assert data == {"jobs": []}


def test_load_queue_unlocked_jobs_nao_lista_retorna_vazio(monkeypatch):
    monkeypatch.setattr(qsvc, "load_runtime_state", lambda key: {"jobs": "nao_e_lista"})
    data = qsvc._load_queue_unlocked()
    assert data == {"jobs": []}


def test_load_queue_unlocked_excecao_no_backend_retorna_vazio(monkeypatch):
    """Quando load_runtime_state levanta exceção, retorna fila vazia sem explodir."""
    def _raise(key):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(qsvc, "load_runtime_state", _raise)
    data = qsvc._load_queue_unlocked()
    assert data == {"jobs": []}


def test_save_and_load_round_trip(monkeypatch):
    _mock_runtime_state(monkeypatch)
    payload = {"jobs": [{"id": "j1"}]}
    qsvc._save_queue_unlocked(payload)
    recovered = qsvc._load_queue_unlocked()
    assert recovered["jobs"][0]["id"] == "j1"


# ---------------------------------------------------------------------------
# enqueue_whatsapp_job
# ---------------------------------------------------------------------------

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


def test_enqueue_whatsapp_job_preserves_phone_in_payload(monkeypatch):
    """F-051: phone override acontece em _process_job; enqueue guarda o original."""
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


def test_enqueue_gera_ids_unicos(monkeypatch):
    _mock_runtime_state(monkeypatch)
    id1 = qsvc.enqueue_whatsapp_job({"charge_id": "c1"})
    id2 = qsvc.enqueue_whatsapp_job({"charge_id": "c2"})
    assert id1 != id2


def test_enqueue_job_status_inicial_queued(monkeypatch):
    _mock_runtime_state(monkeypatch)
    qsvc.enqueue_whatsapp_job({"charge_id": "c1"})
    jobs = qsvc._load_queue_unlocked()["jobs"]
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["attempts"] == 0
    assert jobs[0]["max_attempts"] == 3


# ---------------------------------------------------------------------------
# has_open_job_for_charge
# ---------------------------------------------------------------------------

def test_has_open_job_retorna_false_quando_fila_vazia(monkeypatch):
    _mock_runtime_state(monkeypatch)
    assert qsvc.has_open_job_for_charge("nao-existe") is False


def test_has_open_job_ignora_status_terminal(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j1", "kind": "whatsapp_send",
                "status": "sent",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
            {
                "id": "j2", "kind": "whatsapp_send",
                "status": "error",
                "attempts": 3, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": "falha",
            },
            {
                "id": "j3", "kind": "whatsapp_send",
                "status": "cancelled",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
        ]
    }
    assert qsvc.has_open_job_for_charge("c1") is False


def test_has_open_job_retorna_false_quando_charge_nao_bate_mas_status_aberto(monkeypatch):
    """Job aberto de outra charge não deve contar como hit para a charge buscada."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "outra-charge"}, "last_error": None,
        }]
    }
    assert qsvc.has_open_job_for_charge("charge-buscada") is False


def test_has_open_job_detecta_status_retry(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send",
            "status": "retry",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c-retry"}, "last_error": None,
        }]
    }
    assert qsvc.has_open_job_for_charge("c-retry") is True


# ---------------------------------------------------------------------------
# cancel_open_jobs_for_charge
# ---------------------------------------------------------------------------

def test_cancel_open_jobs_for_charge_marks_jobs_as_cancelled(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-queued",
                "kind": "whatsapp_send",
                "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"}, "last_error": None,
            },
            {
                "id": "job-sending",
                "kind": "whatsapp_send",
                "status": "sending",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"}, "last_error": None,
            },
            {
                "id": "job-sent",
                "kind": "whatsapp_send",
                "status": "sent",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-1"}, "last_error": None,
            },
        ]
    }

    cancelled = qsvc.cancel_open_jobs_for_charge("charge-1")
    assert cancelled == 2

    snap = qsvc.get_queue_snapshot(limit=50)
    assert snap["summary"]["cancelled"] == 2
    assert snap["summary"]["sent"] == 1
    assert qsvc.has_open_job_for_charge("charge-1") is False


def test_cancel_nao_afeta_outra_charge(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j1", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-A"}, "last_error": None,
            },
            {
                "id": "j2", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-B"}, "last_error": None,
            },
        ]
    }
    assert qsvc.cancel_open_jobs_for_charge("charge-A") == 1
    assert qsvc.has_open_job_for_charge("charge-B") is True


def test_cancel_fila_vazia_retorna_zero(monkeypatch):
    _mock_runtime_state(monkeypatch)
    assert qsvc.cancel_open_jobs_for_charge("qualquer") == 0


def test_cancel_com_reason_customizado(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc.cancel_open_jobs_for_charge("c1", reason="motivo_teste")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["last_error"] == "motivo_teste"


# ---------------------------------------------------------------------------
# remove_open_jobs_for_charge_ids
# ---------------------------------------------------------------------------

def test_remove_open_jobs_remove_somente_abertos(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j-queued", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
            {
                "id": "j-sent", "kind": "whatsapp_send", "status": "sent",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
            {
                "id": "j-retry", "kind": "whatsapp_send", "status": "retry",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
        ]
    }
    removed = qsvc.remove_open_jobs_for_charge_ids(["c1"])
    assert removed == 2  # queued + retry removidos; sent fica
    jobs = qsvc._load_queue_unlocked()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == "j-sent"


def test_remove_open_jobs_lista_vazia_retorna_zero(monkeypatch):
    _mock_runtime_state(monkeypatch)
    assert qsvc.remove_open_jobs_for_charge_ids([]) == 0


def test_remove_open_jobs_lista_none_retorna_zero(monkeypatch):
    _mock_runtime_state(monkeypatch)
    assert qsvc.remove_open_jobs_for_charge_ids(None) == 0


def test_remove_open_jobs_charge_inexistente_retorna_zero(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c-existe"}, "last_error": None,
        }]
    }
    assert qsvc.remove_open_jobs_for_charge_ids(["c-nao-existe"]) == 0
    assert len(qsvc._load_queue_unlocked()["jobs"]) == 1


def test_remove_open_jobs_multiplas_charges(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()

    def _job(jid, charge_id, status):
        return {
            "id": jid, "kind": "whatsapp_send", "status": status,
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": charge_id}, "last_error": None,
        }

    store["payment_queue"] = {
        "jobs": [
            _job("j1", "cA", "queued"),
            _job("j2", "cB", "sending"),
            _job("j3", "cC", "sent"),
        ]
    }
    removed = qsvc.remove_open_jobs_for_charge_ids(["cA", "cB"])
    assert removed == 2
    remaining = qsvc._load_queue_unlocked()["jobs"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == "j3"


# ---------------------------------------------------------------------------
# get_queue_snapshot
# ---------------------------------------------------------------------------

def test_get_queue_snapshot_has_summary_and_charge_map(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-queued",
                "kind": "whatsapp_send",
                "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "charge-1", "customer_name": "A"},
                "last_error": None,
            },
            {
                "id": "job-error",
                "kind": "whatsapp_send",
                "status": "error",
                "attempts": 3, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
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


def test_get_queue_snapshot_fila_vazia_retorna_zerado(monkeypatch):
    _mock_runtime_state(monkeypatch)
    snap = qsvc.get_queue_snapshot()
    assert snap["summary"] == {"queued": 0, "sending": 0, "retry": 0, "error": 0, "sent": 0, "cancelled": 0}
    assert snap["jobs"] == []
    assert snap["charge_jobs"] == {}


def test_get_queue_snapshot_limit_aplica_corte(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": f"j{i}", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": f"c{i}"}, "last_error": None,
            }
            for i in range(10)
        ]
    }
    snap = qsvc.get_queue_snapshot(limit=3)
    assert len(snap["jobs"]) == 3


def test_get_queue_snapshot_status_desconhecido_nao_conta_no_summary(monkeypatch):
    """Job com status fora do dict summary não deve ser contado, mas não explode."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "status_desconhecido",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    snap = qsvc.get_queue_snapshot()
    assert sum(snap["summary"].values()) == 0


def test_get_queue_snapshot_job_sem_charge_id_nao_entra_no_charge_jobs(monkeypatch):
    """Job sem charge_id no payload não deve aparecer em charge_jobs."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {}, "last_error": None,
        }]
    }
    snap = qsvc.get_queue_snapshot()
    assert snap["charge_jobs"] == {}


def test_get_queue_snapshot_charge_jobs_preserva_mais_recente(monkeypatch):
    """Quando há dois jobs pro mesmo charge_id, o com updated_at mais recente vence."""
    store = _mock_runtime_state(monkeypatch)
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    new_iso = datetime.now(timezone.utc).isoformat()

    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j-antigo", "kind": "whatsapp_send", "status": "error",
                "attempts": 3, "max_attempts": 3,
                "next_attempt_at": old_iso, "created_at": old_iso, "updated_at": old_iso,
                "payload": {"charge_id": "c1"}, "last_error": "timeout",
            },
            {
                "id": "j-novo", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": new_iso, "created_at": new_iso, "updated_at": new_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
        ]
    }
    snap = qsvc.get_queue_snapshot(limit=50)
    assert snap["charge_jobs"]["c1"]["id"] == "j-novo"


# ---------------------------------------------------------------------------
# recover_stuck_jobs
# ---------------------------------------------------------------------------

def test_recover_stuck_jobs_moves_sending_to_queued(monkeypatch):
    store = _mock_runtime_state(monkeypatch)

    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "job-1",
                "kind": "whatsapp_send",
                "status": "sending",
                "attempts": 1, "max_attempts": 3,
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


def test_recover_stuck_jobs_nao_toca_job_recente(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j-recente", "kind": "whatsapp_send", "status": "sending",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    recovered = qsvc.recover_stuck_jobs(stale_seconds=3600)
    assert recovered == 0
    assert qsvc._load_queue_unlocked()["jobs"][0]["status"] == "sending"


def test_recover_stuck_jobs_ignora_status_nao_sending(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j-q", "kind": "whatsapp_send", "status": "queued",
                "attempts": 0, "max_attempts": 3,
                "next_attempt_at": stale_ts, "created_at": stale_ts, "updated_at": stale_ts,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
            {
                "id": "j-e", "kind": "whatsapp_send", "status": "error",
                "attempts": 3, "max_attempts": 3,
                "next_attempt_at": stale_ts, "created_at": stale_ts, "updated_at": stale_ts,
                "payload": {"charge_id": "c2"}, "last_error": "falha",
            },
        ]
    }
    recovered = qsvc.recover_stuck_jobs(stale_seconds=60)
    assert recovered == 0


def test_recover_stuck_jobs_updated_at_invalido(monkeypatch):
    """updated_at corrompido não deve explodir — job recém-visto não é stuck."""
    store = _mock_runtime_state(monkeypatch)
    store["payment_queue"] = {
        "jobs": [{
            "id": "j-bad", "kind": "whatsapp_send", "status": "sending",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": None, "created_at": None, "updated_at": "invalido",
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    # Com updated_at inválido, o service usa `now` como fallback → diff = 0 → não stuck
    recovered = qsvc.recover_stuck_jobs(stale_seconds=60)
    assert recovered == 0


# ---------------------------------------------------------------------------
# _claim_next_job
# ---------------------------------------------------------------------------

def test_claim_next_job_retorna_none_fila_vazia(monkeypatch):
    _mock_runtime_state(monkeypatch)
    assert qsvc._claim_next_job() is None


def test_claim_next_job_retorna_primeiro_queued(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    job = qsvc._claim_next_job()
    assert job is not None
    assert job["id"] == "j1"
    assert job["status"] == "sending"
    assert job["attempts"] == 1


def test_claim_next_job_muda_status_para_sending_na_fila(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc._claim_next_job()
    job_na_fila = qsvc._load_queue_unlocked()["jobs"][0]
    assert job_na_fila["status"] == "sending"


def test_claim_next_job_next_attempt_at_invalido_trata_como_pronto(monkeypatch):
    """next_attempt_at corrompido não deve impedir o claim — trata como pronto."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j-bad-ts", "kind": "whatsapp_send", "status": "queued",
            "attempts": 0, "max_attempts": 3,
            "next_attempt_at": "nao-e-data", "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    job = qsvc._claim_next_job()
    assert job is not None
    assert job["status"] == "sending"


def test_claim_next_job_pula_job_agendado_para_futuro(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    futuro_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j-futuro", "kind": "whatsapp_send", "status": "retry",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": futuro_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": "timeout",
            },
        ]
    }
    assert qsvc._claim_next_job() is None


def test_claim_next_job_pula_status_terminal(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [
            {
                "id": "j-sent", "kind": "whatsapp_send", "status": "sent",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c1"}, "last_error": None,
            },
            {
                "id": "j-sending", "kind": "whatsapp_send", "status": "sending",
                "attempts": 1, "max_attempts": 3,
                "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
                "payload": {"charge_id": "c2"}, "last_error": None,
            },
        ]
    }
    assert qsvc._claim_next_job() is None


def test_claim_next_job_aceita_status_retry_pronto(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    passado_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j-retry", "kind": "whatsapp_send", "status": "retry",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": passado_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": "timeout",
        }]
    }
    job = qsvc._claim_next_job()
    assert job is not None
    assert job["status"] == "sending"
    assert job["attempts"] == 2


# ---------------------------------------------------------------------------
# _mark_done
# ---------------------------------------------------------------------------

def test_mark_done_muda_status_para_sent(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "sending",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc._mark_done("j1")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["status"] == "sent"
    assert job["last_error"] is None


def test_mark_done_job_inexistente_nao_explode(monkeypatch):
    _mock_runtime_state(monkeypatch)
    qsvc._mark_done("id-nao-existe")  # não deve levantar exceção


def test_mark_done_job_id_diferente_nao_afeta_outro_job(monkeypatch):
    """Loop percorre job com id diferente sem alterar nada."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j-outro", "kind": "whatsapp_send", "status": "sending",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc._mark_done("j-nao-existe")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["status"] == "sending"  # não alterou


# ---------------------------------------------------------------------------
# _mark_failed_or_retry
# ---------------------------------------------------------------------------

def test_mark_failed_or_retry_abaixo_do_limite_vira_retry(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "sending",
            "attempts": 1, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc._mark_failed_or_retry("j1", "timeout de rede")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["status"] == "retry"
    assert job["last_error"] == "timeout de rede"
    assert job["next_attempt_at"] is not None


def test_mark_failed_or_retry_no_limite_vira_error(monkeypatch):
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "sending",
            "attempts": 3, "max_attempts": 3,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    qsvc._mark_failed_or_retry("j1", "falha final")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["status"] == "error"
    assert job["last_error"] == "falha final"


def test_mark_failed_or_retry_job_inexistente_nao_explode(monkeypatch):
    _mock_runtime_state(monkeypatch)
    qsvc._mark_failed_or_retry("nao-existe", "erro")


def test_mark_failed_or_retry_backoff_cresce_com_attempts(monkeypatch):
    """Backoff é min(300, 60 * attempts) — maior número de tentativas → espera maior."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()

    def _job(jid, attempts):
        return {
            "id": jid, "kind": "whatsapp_send", "status": "sending",
            "attempts": attempts, "max_attempts": 10,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": jid}, "last_error": None,
        }

    store["payment_queue"] = {"jobs": [_job("j1", 1), _job("j2", 2)]}
    qsvc._mark_failed_or_retry("j1", "e")
    qsvc._mark_failed_or_retry("j2", "e")

    jobs = {j["id"]: j for j in qsvc._load_queue_unlocked()["jobs"]}
    t1 = datetime.fromisoformat(jobs["j1"]["next_attempt_at"]).timestamp()
    t2 = datetime.fromisoformat(jobs["j2"]["next_attempt_at"]).timestamp()
    assert t2 > t1  # j2 tem backoff maior


def test_mark_failed_or_retry_backoff_cap_300s(monkeypatch):
    """Backoff é limitado em 300 s independente do número de tentativas."""
    store = _mock_runtime_state(monkeypatch)
    now_iso = datetime.now(timezone.utc).isoformat()
    store["payment_queue"] = {
        "jobs": [{
            "id": "j1", "kind": "whatsapp_send", "status": "sending",
            "attempts": 100, "max_attempts": 200,
            "next_attempt_at": now_iso, "created_at": now_iso, "updated_at": now_iso,
            "payload": {"charge_id": "c1"}, "last_error": None,
        }]
    }
    import time as _time
    before = _time.time()
    qsvc._mark_failed_or_retry("j1", "erro grande")
    job = qsvc._load_queue_unlocked()["jobs"][0]
    assert job["status"] == "retry"
    next_ts = datetime.fromisoformat(job["next_attempt_at"]).timestamp()
    assert next_ts - before <= 301  # tolerância de 1s p/ execução


# ---------------------------------------------------------------------------
# Fluxo completo: enqueue → claim → mark_done
# ---------------------------------------------------------------------------

def test_fluxo_completo_enqueue_claim_done(monkeypatch):
    _mock_runtime_state(monkeypatch)
    job_id = qsvc.enqueue_whatsapp_job({"charge_id": "cZ", "phone": "5511"})

    claimed = qsvc._claim_next_job()
    assert claimed["id"] == job_id
    assert claimed["status"] == "sending"

    qsvc._mark_done(job_id)
    assert qsvc.has_open_job_for_charge("cZ") is False
    snap = qsvc.get_queue_snapshot()
    assert snap["summary"]["sent"] == 1


def test_fluxo_completo_enqueue_claim_retry_error(monkeypatch):
    _mock_runtime_state(monkeypatch)
    job_id = qsvc.enqueue_whatsapp_job({"charge_id": "cErr"})

    # 3 tentativas até esgotar
    for attempt in range(1, 4):
        claimed = qsvc._claim_next_job()
        if attempt < 3:
            # Simular próxima tentativa disponível agora
            data = qsvc._load_queue_unlocked()
            for j in data["jobs"]:
                if j["id"] == job_id:
                    j["status"] = "sending"  # recolocar como sending p/ mark_failed
            qsvc._save_queue_unlocked(data)
            qsvc._mark_failed_or_retry(job_id, f"erro {attempt}")
            # Forçar next_attempt_at para o passado
            data = qsvc._load_queue_unlocked()
            for j in data["jobs"]:
                if j["id"] == job_id:
                    j["next_attempt_at"] = (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat()
            qsvc._save_queue_unlocked(data)
        else:
            assert claimed is not None
            qsvc._mark_failed_or_retry(job_id, "erro final")

    snap = qsvc.get_queue_snapshot()
    assert snap["summary"]["error"] == 1


def test_process_job_v2_uses_staging_fallback_when_mercadopago_returns_5xx(monkeypatch):
    """F-040: _process_job_v2 removido; _process_job agora usa AsaasClient diretamente."""
    import pytest
    pytest.skip("F-051: _process_job_v2 removed; _process_job now uses AsaasClient directly")
