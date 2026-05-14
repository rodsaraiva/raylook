import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.config import settings
from app.locks import finance_lock
from app.services.routing_service import resolve_test_phone
from app.services.runtime_state_service import load_runtime_state, save_runtime_state

logger = logging.getLogger("raylook.payment_queue")

_worker_task: Optional[asyncio.Task] = None
_worker_guard = asyncio.Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue_unlocked() -> Dict[str, Any]:
    try:
        data = load_runtime_state("payment_queue")
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data
    except Exception as e:
        logger.error("Erro ao carregar fila de pagamentos: %s", e)
    return {"jobs": []}


def _save_queue_unlocked(data: Dict[str, Any]) -> None:
    save_runtime_state("payment_queue", data)


def _normalize_job_payload(job_payload: Dict[str, Any]) -> Dict[str, Any]:
    return dict(job_payload or {})


def enqueue_whatsapp_job(job_payload: Dict[str, Any]) -> str:
    normalized_payload = _normalize_job_payload(job_payload)
    data = _load_queue_unlocked()
    jobs = data.setdefault("jobs", [])
    job_id = str(uuid4())
    now_iso = _utcnow_iso()
    jobs.append(
        {
            "id": job_id,
            "kind": "whatsapp_send",
            "status": "queued",
            "attempts": 0,
            "max_attempts": 3,
            "next_attempt_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
            "payload": normalized_payload,
            "last_error": None,
        }
    )
    _save_queue_unlocked(data)
    return job_id


def has_open_job_for_charge(charge_id: str) -> bool:
    data = _load_queue_unlocked()
    for job in data.get("jobs", []):
        if job.get("status") in ("queued", "sending", "retry"):
            payload = job.get("payload") or {}
            if payload.get("charge_id") == charge_id:
                return True
    return False


def cancel_open_jobs_for_charge(charge_id: str, reason: str = "cancelled_by_manual_resend") -> int:
    """
    Marca como cancelados os jobs abertos de uma cobrança.
    Retorna a quantidade de jobs cancelados.
    """
    cancelled = 0
    data = _load_queue_unlocked()
    for job in data.get("jobs", []):
        if job.get("status") not in ("queued", "sending", "retry"):
            continue
        payload = job.get("payload") or {}
        if payload.get("charge_id") != charge_id:
            continue
        job["status"] = "cancelled"
        job["updated_at"] = _utcnow_iso()
        job["last_error"] = reason
        cancelled += 1
    if cancelled:
        _save_queue_unlocked(data)
    return cancelled


def remove_open_jobs_for_charge_ids(charge_ids: List[str]) -> int:
    target_ids = {str(cid) for cid in (charge_ids or []) if cid}
    if not target_ids:
        return 0

    removed = 0
    data = _load_queue_unlocked()
    jobs = data.get("jobs", [])
    kept_jobs = []
    for job in jobs:
        payload = job.get("payload") or {}
        charge_id = str(payload.get("charge_id") or "")
        status = str(job.get("status") or "")
        if charge_id in target_ids and status in ("queued", "sending", "retry"):
            removed += 1
            continue
        kept_jobs.append(job)
    data["jobs"] = kept_jobs
    if removed:
        _save_queue_unlocked(data)
    return removed


def get_queue_snapshot(limit: int = 300) -> Dict[str, Any]:
    data = _load_queue_unlocked()
    jobs = data.get("jobs", [])

    jobs_sorted = sorted(
        jobs,
        key=lambda j: str(j.get("created_at") or ""),
        reverse=True,
    )[: max(1, int(limit or 300))]

    summary = {"queued": 0, "sending": 0, "retry": 0, "error": 0, "sent": 0, "cancelled": 0}
    charge_jobs: Dict[str, Dict[str, Any]] = {}

    for job in jobs_sorted:
        status = str(job.get("status") or "queued")
        if status in summary:
            summary[status] += 1
        payload = job.get("payload") or {}
        charge_id = payload.get("charge_id")
        if charge_id:
            prev = charge_jobs.get(str(charge_id))
            if not prev or str(job.get("updated_at") or "") > str(prev.get("updated_at") or ""):
                charge_jobs[str(charge_id)] = job

    return {
        "summary": summary,
        "jobs": jobs_sorted,
        "charge_jobs": charge_jobs,
        "updated_at": _utcnow_iso(),
    }


def recover_stuck_jobs(stale_seconds: int = 1800) -> int:
    now = time.time()
    recovered = 0
    data = _load_queue_unlocked()
    for job in data.get("jobs", []):
        if job.get("status") != "sending":
            continue
        updated_at = job.get("updated_at")
        try:
            updated_ts = datetime.fromisoformat(updated_at).timestamp() if updated_at else now
        except Exception:
            updated_ts = now
        if (now - updated_ts) >= stale_seconds:
            job["status"] = "queued"
            job["updated_at"] = _utcnow_iso()
            recovered += 1
    if recovered:
        _save_queue_unlocked(data)
    return recovered


def _claim_next_job() -> Optional[Dict[str, Any]]:
    now_iso = _utcnow_iso()
    now_dt = datetime.fromisoformat(now_iso)
    data = _load_queue_unlocked()
    jobs = data.get("jobs", [])
    candidate = None
    for job in jobs:
        if job.get("status") not in ("queued", "retry"):
            continue
        next_attempt_at = job.get("next_attempt_at")
        try:
            ready = datetime.fromisoformat(next_attempt_at) <= now_dt if next_attempt_at else True
        except Exception:
            ready = True
        if not ready:
            continue
        candidate = job
        break
    if not candidate:
        return None
    candidate["status"] = "sending"
    candidate["attempts"] = int(candidate.get("attempts", 0) or 0) + 1
    candidate["updated_at"] = now_iso
    _save_queue_unlocked(data)
    return candidate.copy()


def _mark_done(job_id: str) -> None:
    data = _load_queue_unlocked()
    for job in data.get("jobs", []):
        if job.get("id") == job_id:
            job["status"] = "sent"
            job["updated_at"] = _utcnow_iso()
            job["last_error"] = None
            break
    _save_queue_unlocked(data)


def _mark_failed_or_retry(job_id: str, error: str) -> None:
    data = _load_queue_unlocked()
    for job in data.get("jobs", []):
        if job.get("id") != job_id:
            continue
        attempts = int(job.get("attempts", 0) or 0)
        max_attempts = int(job.get("max_attempts", 3) or 3)
        job["last_error"] = error
        job["updated_at"] = _utcnow_iso()
        if attempts >= max_attempts:
            job["status"] = "error"
        else:
            backoff_seconds = min(300, 60 * attempts)
            retry_at = datetime.now(timezone.utc).timestamp() + backoff_seconds
            job["status"] = "retry"
            job["next_attempt_at"] = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()
        break
    _save_queue_unlocked(data)


async def _process_job(job: Dict[str, Any]) -> None:
    """F-040: processar job da fila usando Asaas como provider de pagamento.

    Fluxo:
      1. Se o payload não tem payment (caso raro), cria no Asaas agora.
      2. Envia via WhatsApp (send_payment_whatsapp).
      3. Grava status em pagamentos via update_vote_state (que grava o
         asaas_payment_id em pagamentos.provider_payment_id).
    """
    from integrations.asaas.client import AsaasClient
    from app.services.package_state_service import update_vote_state
    from app.services.supabase_service import SupabaseRestClient
    import re
    from datetime import datetime, timedelta, timezone

    job_id = job.get("id")
    payload = job.get("payload") or {}
    charge_id = payload.get("charge_id")
    package_id = payload.get("package_id")
    vote_idx = payload.get("vote_idx")
    phone = payload.get("phone")
    payment = payload.get("payment") or {}
    logger.info("Iniciando processamento do job=%s (charge=%s, phone=%s)", job_id, charge_id, phone)
    try:
        asaas = AsaasClient()

        # Evolution removido — em sandbox, envio fica como log até o WHAPI
        # próprio do raylook ser provisionado (seção futura).
        _inst_name = "raylook-sandbox"
        _inst_token = ""

        # Se não há pagamento, criamos agora no Asaas
        if not payment or not payment.get("id"):
            logger.info("Job %s sem pagamento; criando no Asaas agora.", job_id)

            subtotal = float(payload.get("subtotal") or 0.0)
            qty = int(float(payload.get("qty") or 0))
            total_with_comm = round(subtotal + qty * settings.COMMISSION_PER_PIECE, 2)

            due_date_obj = datetime.utcnow() + timedelta(days=7)
            due = due_date_obj.strftime("%Y-%m-%d")  # Asaas usa yyyy-mm-dd

            customer_name = payload.get("customer_name") or "Cliente"
            customer_name_clean = re.sub(r"[^\w\s]", "", str(customer_name)).strip() or "Cliente"

            item_name = payload.get("item_name") or "Peça"
            pkg_id = package_id or "pacote"

            # Asaas exige CPF/CNPJ do cliente final. Se o cliente ainda não
            # cadastrou no portal, deixa o job na fila e tenta de novo — quando
            # ele se cadastrar, o re-processamento pega. Sem CPF a cobrança não
            # pode ser criada (default agruparia tudo no CPF da Raylook).
            cpf_cliente = ""
            try:
                from app.services.portal_service import _phone_variants, _normalize_phone
                sb_cli = SupabaseRestClient.from_settings()
                for variant in _phone_variants(_normalize_phone(phone or "")):
                    rows = sb_cli.select(
                        "clientes", columns="cpf_cnpj",
                        filters=[("celular", "eq", variant)], limit=1,
                    )
                    if rows and (rows[0].get("cpf_cnpj") or "").strip():
                        cpf_cliente = rows[0]["cpf_cnpj"].strip()
                        break
            except Exception:
                cpf_cliente = ""

            if not cpf_cliente:
                logger.warning(
                    "Job %s sem CPF do cliente phone=%s — cobrança será criada quando o cliente "
                    "pagar pelo portal (modal de contingência captura o CPF).",
                    job_id, phone,
                )
                if package_id is not None and vote_idx is not None:
                    update_vote_state(str(package_id), int(vote_idx), {
                        "asaas_payment_status": "pending_cpf",
                    })
                return

            customer = await asyncio.to_thread(
                asaas.create_customer,
                name=customer_name_clean,
                phone=phone or "",
                cpf_cnpj=cpf_cliente,
            )
            asaas_customer_id = customer.get("id")
            if not asaas_customer_id:
                raise Exception(f"Asaas create_customer returned no id: {customer}")

            payment = await asyncio.to_thread(
                asaas.create_payment_pix,
                customer_id=asaas_customer_id,
                amount=total_with_comm,
                due_date=due,
                description=f"Cobrança {item_name} - pacote {pkg_id}",
            )
            pid = payment.get("id")
            if not pid:
                raise Exception("Falha ao criar pagamento no Asaas durante processamento da fila.")

            logger.info("Pagamento Asaas criado para job=%s: %s", job_id, pid)

            if package_id is not None and vote_idx is not None:
                update_vote_state(str(package_id), int(vote_idx), {
                    "asaas_customer_id": asaas_customer_id,
                    "asaas_payment_id": pid,
                    "asaas_payment_status": "created",
                    "asaas_payment_attempts": 1,
                    "asaas_payment_payload": payment
                })

        # Evolution removido — em sandbox, apenas loga o que seria enviado.
        # Quando WHAPI próprio do raylook for provisionado, este bloco vira
        # uma chamada via WHAPI restrita ao grupo da raylook.
        logger.info(
            "[whatsapp-stub] job=%s phone=%s qty=%s item=%s charge=%s — envio desativado (Evolution removido)",
            job_id,
            phone,
            payload.get("qty"),
            payload.get("item_name"),
            charge_id,
        )
        status = "stubbed"

        # Atualiza pagamentos.status no banco via SQL direto (pagamentos.id = charge_id)
        # F-042: proteção contra reverter paid → sent. Se a cobrança já foi
        # paga (via webhook Asaas ou manual), não sobrescrever mesmo em caso
        # de resend posterior.
        if charge_id and charge_id != payment.get("id"):
            try:
                sb = SupabaseRestClient.from_settings()
                current = sb.select(
                    "pagamentos",
                    columns="status",
                    filters=[("id", "eq", charge_id)],
                    limit=1,
                )
                current_status = ""
                if isinstance(current, list) and current:
                    current_status = str(current[0].get("status") or "").lower()

                if current_status == "paid":
                    logger.info(
                        "Resend: charge %s já está paid; não sobrescrevendo status",
                        charge_id,
                    )
                else:
                    new_status = "sent" if status == "sent" else "created"
                    sb.update(
                        "pagamentos",
                        {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()},
                        filters=[("id", "eq", charge_id)],
                        returning="minimal",
                    )
            except Exception as exc:
                logger.warning("Erro ao atualizar pagamentos.status: %s", exc)

        # F-042: refresh dos snapshots pra dash refletir novo status sem F5
        try:
            from app.services.finance_service import refresh_charge_snapshot, refresh_dashboard_stats
            from app.services.customer_service import refresh_customer_rows_snapshot
            await asyncio.to_thread(refresh_charge_snapshot)
            await asyncio.to_thread(refresh_dashboard_stats)
            await asyncio.to_thread(refresh_customer_rows_snapshot)
        except Exception:
            pass

        if package_id is not None and vote_idx is not None:
            try:
                update_vote_state(
                    str(package_id),
                    int(vote_idx),
                    {
                        "asaas_payment_status": "sent" if status == "sent" else "failed",
                        "asaas_payment_send_result": res,
                    },
                )
            except Exception:
                pass

        if status == "sent":
            _mark_done(str(job_id))
        else:
            _mark_failed_or_retry(str(job_id), str(res.get("error") or "Falha no envio"))
    except Exception as e:
        logger.exception("Erro processando job da fila %s", job_id)
        _mark_failed_or_retry(str(job_id), str(e))


async def _worker_loop() -> None:
    recovered = recover_stuck_jobs()
    if recovered:
        logger.info("Fila de pagamentos: %s jobs recuperados", recovered)

    while True:
        try:
            job = await asyncio.to_thread(_claim_next_job)
            if not job:
                await asyncio.sleep(1.0)
                continue
            await _process_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erro no loop da fila de pagamentos")
            await asyncio.sleep(2.0)


async def start_payment_queue_worker() -> None:
    global _worker_task
    async with _worker_guard:
        if _worker_task and not _worker_task.done():
            return
        _worker_task = asyncio.create_task(_worker_loop())
        logger.info("Worker da fila de pagamentos iniciado")


async def stop_payment_queue_worker() -> None:
    global _worker_task
    async with _worker_guard:
        if _worker_task and not _worker_task.done():
            _worker_task.cancel()
            try:
                await _worker_task
            except asyncio.CancelledError:
                pass
        _worker_task = None

