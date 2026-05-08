"""Serviço de reconciliação de votos via WHAPI.

Garante que votos recebidos pelo WhatsApp mas não entregues via webhook
sejam inseridos no banco, comparando o estado real da WHAPI com o DB.

Roda periodicamente (a cada 10 min) e também pode ser acionado manualmente
por enquete via endpoint /api/admin/polls/{id}/resync.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.supabase_service import SupabaseRestClient
from app.services.whatsapp_domain_service import (
    PackageService,
    PollService,
    VoteService,
    WebhookEvent,
    WebhookIngestionService,
    _digits,
    _is_lid_or_invalid_phone,
    _qty_from_text,
    _sanitize_name,
)
from integrations.whapi import WHAPIClient

logger = logging.getLogger("raylook.poll_reconcile")

# Intervalo entre rodadas do job periódico
RECONCILE_INTERVAL_SECONDS = 600  # 10 minutos


def _phone_variants(phone: str) -> List[str]:
    """Gera variantes do número para comparar com o banco."""
    d = _digits(phone)
    variants = {d}
    # Remove DDI 55
    if d.startswith("55") and len(d) >= 12:
        variants.add(d[2:])
    # Adiciona DDI 55
    if not d.startswith("55"):
        variants.add("55" + d)
    # 9º dígito: celular com/sem
    for p in list(variants):
        if len(p) == 11 and p[2] == "9":   # 62 9xxxx-xxxx → sem 9
            variants.add(p[:2] + p[3:])
        if len(p) == 10:                    # 62 xxxx-xxxx → com 9
            variants.add(p[:2] + "9" + p[2:])
    return list(variants)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PollReconcileService:
    def __init__(self, sb: Optional[SupabaseRestClient] = None, whapi: Optional[WHAPIClient] = None) -> None:
        self.sb = sb or SupabaseRestClient.from_settings()
        try:
            self.whapi = whapi or WHAPIClient()
        except Exception:
            self.whapi = None

    # ─────────────────────────────────────────────────────────────────────────
    # Diagnóstico (somente leitura — não altera banco)
    # ─────────────────────────────────────────────────────────────────────────

    def compare(self, enquete_id: str) -> Dict[str, Any]:
        """Compara estado WHAPI vs banco para uma enquete.

        Retorna:
          {
            "enquete_id": str,
            "poll_id": str,
            "whapi_total": int | None,
            "db_votes": int,
            "missing_in_db": [ {phone, option_name, qty} ],
            "extra_in_db":   [ {phone, qty} ],   # em DB mas não na WHAPI
            "in_sync": bool,
            "whapi_raw": dict | None,
          }
        """
        enquete = self._fetch_enquete(enquete_id)
        if not enquete:
            return {"error": "enquete não encontrada", "enquete_id": enquete_id}

        db_votes = self._fetch_db_votes(enquete["id"])
        whapi_state = self._fetch_whapi_state(enquete)

        if whapi_state is None:
            return {
                "enquete_id": enquete["id"],
                "poll_id": enquete.get("external_poll_id"),
                "whapi_total": None,
                "db_votes": len(db_votes),
                "missing_in_db": [],
                "extra_in_db": [],
                "in_sync": None,
                "whapi_raw": None,
                "warning": "Poll não encontrado na WHAPI (verifique chat_id e external_poll_id)",
            }

        missing, extra = self._diff(db_votes, whapi_state)
        return {
            "enquete_id": enquete["id"],
            "poll_id": enquete.get("external_poll_id"),
            "whapi_total": whapi_state.get("total"),
            "db_votes": len(db_votes),
            "missing_in_db": missing,
            "extra_in_db": extra,
            "in_sync": len(missing) == 0 and len(extra) == 0,
            "whapi_raw": whapi_state,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Sincronização (aplica diff no banco)
    # ─────────────────────────────────────────────────────────────────────────

    def sync(self, enquete_id: str) -> Dict[str, Any]:
        """Aplica diff WHAPI→DB para uma enquete. Retorna resumo das mudanças."""
        enquete = self._fetch_enquete(enquete_id)
        if not enquete:
            return {"error": "enquete não encontrada", "enquete_id": enquete_id}

        if not self.whapi:
            return {"error": "WHAPI não configurada", "enquete_id": enquete_id}

        db_votes = self._fetch_db_votes(enquete["id"])
        whapi_state = self._fetch_whapi_state(enquete)

        if whapi_state is None:
            return {
                "enquete_id": enquete["id"],
                "applied": 0,
                "removed": 0,
                "warning": "Poll não encontrado na WHAPI",
            }

        missing, extra = self._diff(db_votes, whapi_state)

        ingestion = WebhookIngestionService(self.sb)
        applied = 0
        errors_insert: List[str] = []
        for entry in missing:
            try:
                self._insert_missing_vote(ingestion, enquete, entry)
                applied += 1
            except Exception as exc:
                logger.warning("reconcile insert error enquete=%s phone=%s: %s", enquete["id"], entry.get("phone"), exc)
                errors_insert.append(str(exc))

        removed = 0
        errors_remove: List[str] = []
        for entry in extra:
            try:
                self._mark_vote_removed(enquete, entry)
                removed += 1
            except Exception as exc:
                logger.warning("reconcile remove error enquete=%s phone=%s: %s", enquete["id"], entry.get("phone"), exc)
                errors_remove.append(str(exc))

        # Rebuild somente se houve mudanças
        if applied > 0 or removed > 0:
            try:
                pkg_svc = PackageService(self.sb)
                pkg_svc.rebuild_for_poll(enquete["id"])
            except Exception as exc:
                logger.warning("reconcile rebuild error enquete=%s: %s", enquete["id"], exc)

        return {
            "enquete_id": enquete["id"],
            "title": enquete.get("titulo"),
            "whapi_total": whapi_state.get("total"),
            "db_before": len(db_votes),
            "applied": applied,
            "removed": removed,
            "errors": errors_insert + errors_remove,
        }

    def sync_all_open(self) -> Dict[str, Any]:
        """Reconcilia todas as enquetes abertas. Chamado pelo job periódico."""
        if not self.whapi:
            logger.warning("poll_reconcile: WHAPI não configurada, abortando")
            return {"skipped": True, "reason": "WHAPI não configurada"}

        enquetes = self.sb.select(
            "enquetes",
            columns="id,external_poll_id,chat_id,titulo,created_at_provider",
            filters=[("status", "eq", "open")],
            order="created_at_provider.desc",
            limit=50,
        ) or []

        results = []
        for enq in enquetes:
            try:
                result = self.sync(enq["id"])
                if result.get("applied", 0) + result.get("removed", 0) > 0:
                    logger.info(
                        "poll_reconcile: enquete=%s title=%r applied=%d removed=%d",
                        enq["id"], enq.get("titulo"), result.get("applied", 0), result.get("removed", 0),
                    )
                    results.append(result)
            except Exception as exc:
                logger.warning("poll_reconcile: erro na enquete %s: %s", enq["id"], exc)

        total_applied = sum(r.get("applied", 0) for r in results)
        total_removed = sum(r.get("removed", 0) for r in results)
        logger.info(
            "poll_reconcile: rodada completa. enquetes=%d alteradas=%d inserted=%d removed=%d",
            len(enquetes), len(results), total_applied, total_removed,
        )
        return {
            "enquetes_checked": len(enquetes),
            "enquetes_changed": len(results),
            "total_inserted": total_applied,
            "total_removed": total_removed,
            "details": results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Internos
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_enquete(self, enquete_id: str) -> Optional[Dict[str, Any]]:
        """Busca enquete por UUID interno ou por external_poll_id do WhatsApp."""
        import uuid as _uuid_mod
        columns = "id,external_poll_id,chat_id,titulo,status,created_at_provider"
        # Tenta primeiro como UUID
        try:
            _uuid_mod.UUID(enquete_id)
            row = self.sb.select("enquetes", columns=columns, filters=[("id", "eq", enquete_id)], single=True)
            if isinstance(row, dict):
                return row
        except ValueError:
            pass
        # Fallback: tenta como external_poll_id
        row = self.sb.select(
            "enquetes",
            columns=columns,
            filters=[("external_poll_id", "eq", enquete_id)],
            single=True,
        )
        return row if isinstance(row, dict) else None

    def _fetch_db_votes(self, enquete_id: str) -> List[Dict[str, Any]]:
        """Retorna votos ativos (qty > 0) da enquete com celular do cliente."""
        rows = self.sb.select(
            "votos",
            columns="id,qty,status,voted_at,cliente:cliente_id(id,celular,nome)",
            filters=[
                ("enquete_id", "eq", enquete_id),
                ("status", "eq", "in"),
                ("qty", "gt", "0"),
            ],
        ) or []
        return rows if isinstance(rows, list) else []

    def _fetch_whapi_state(self, enquete: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.whapi:
            return None
        chat_id = (enquete.get("chat_id") or "").strip()
        poll_id = (enquete.get("external_poll_id") or "").strip()
        if not chat_id or not poll_id:
            logger.warning("poll_reconcile: chat_id ou external_poll_id vazio para enquete %s", enquete.get("id"))
            return None

        created_at_str = enquete.get("created_at_provider") or ""
        created_at_unix: Optional[int] = None
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                created_at_unix = int(dt.timestamp())
            except Exception:
                pass

        try:
            return self.whapi.get_poll_current_state(chat_id, poll_id, created_at_unix)
        except Exception as exc:
            logger.warning("poll_reconcile: erro WHAPI enquete=%s: %s", enquete.get("id"), exc)
            return None

    def _diff(
        self,
        db_votes: List[Dict[str, Any]],
        whapi_state: Dict[str, Any],
    ):
        """Retorna (missing_in_db, extra_in_db)."""
        # Mapeia phones DB → vote info
        db_phones: Dict[str, Dict[str, Any]] = {}
        for v in db_votes:
            cliente = v.get("cliente") or {}
            phone = _digits(cliente.get("celular") or "")
            if phone:
                db_phones[phone] = v

        # Mapeia phones WHAPI (todas as variantes) → option info.
        # Filtra LIDs (Linked Identifiers do WhatsApp) e phones fora do padrão BR
        # — esses não são telefones reais e criariam clientes fantasmas.
        whapi_phones: Dict[str, Dict[str, Any]] = {}
        for result in whapi_state.get("results") or []:
            opt_name = str(result.get("name") or "").strip()
            qty = _qty_from_text(opt_name)
            for voter_phone in result.get("voters") or []:
                if _is_lid_or_invalid_phone(voter_phone):
                    logger.warning(
                        "lid_voter_ignored poll=%s voter=%s",
                        whapi_state.get("id") or "?", voter_phone,
                    )
                    continue
                canonical = _digits(voter_phone)
                if canonical:
                    whapi_phones[canonical] = {
                        "phone": canonical,
                        "option_name": opt_name,
                        "qty": qty,
                    }

        # Missing: está na WHAPI mas não no DB (verificando variantes)
        missing = []
        for w_phone, info in whapi_phones.items():
            variants = set(_phone_variants(w_phone))
            if not variants.intersection(db_phones.keys()):
                missing.append(info)

        # Extra: está no DB mas não na WHAPI
        extra = []
        for d_phone, vote in db_phones.items():
            variants = set(_phone_variants(d_phone))
            if not variants.intersection(whapi_phones.keys()):
                extra.append({
                    "phone": d_phone,
                    "qty": vote.get("qty"),
                    "voto_id": vote.get("id"),
                })

        return missing, extra

    def _insert_missing_vote(
        self,
        ingestion: WebhookIngestionService,
        enquete: Dict[str, Any],
        entry: Dict[str, Any],
    ) -> None:
        """Insere um voto faltante via VoteService (mesmo caminho do webhook)."""
        phone = entry["phone"]
        qty = int(entry.get("qty") or 0)
        opt_name = entry.get("option_name") or str(qty)

        event = WebhookEvent(
            kind="vote_updated",
            provider="whapi_reconcile",
            event_key=f"reconcile:{enquete['external_poll_id']}:vote_updated:{phone}",
            raw_event_id=f"reconcile:{phone}",
            occurred_at=datetime.now(timezone.utc),
            payload={"source": "poll_reconcile"},
            external_poll_id=enquete["external_poll_id"],
            chat_id=enquete.get("chat_id"),
            voter_phone=phone,
            voter_name="",   # será ignorado se o cliente já existe
            option_external_id=opt_name,
            option_label=opt_name,
            qty=qty,
        )
        ingestion.vote_service.process_vote(event)

    def _mark_vote_removed(self, enquete: Dict[str, Any], entry: Dict[str, Any]) -> None:
        """Marca voto como removido (status=out, qty=0) no banco."""
        voto_id = entry.get("voto_id")
        if not voto_id:
            return
        self.sb.update(
            "votos",
            {"status": "out", "qty": 0, "updated_at": _now_iso()},
            filters=[("id", "eq", str(voto_id))],
            returning="minimal",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Job periódico
# ─────────────────────────────────────────────────────────────────────────────

_reconcile_task: Optional[asyncio.Task] = None


async def _reconcile_loop() -> None:
    logger.info("poll_reconcile: job iniciado (intervalo=%ds)", RECONCILE_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            svc = PollReconcileService()
            await asyncio.to_thread(svc.sync_all_open)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("poll_reconcile: erro não tratado no job periódico")


def start_poll_reconcile_scheduler(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    global _reconcile_task
    if _reconcile_task and not _reconcile_task.done():
        return
    try:
        lp = loop or asyncio.get_event_loop()
        _reconcile_task = lp.create_task(_reconcile_loop())
        logger.info("poll_reconcile: scheduler registrado")
    except RuntimeError:
        logger.warning("poll_reconcile: não foi possível registrar scheduler (sem event loop)")
