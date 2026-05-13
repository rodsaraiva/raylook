from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled
from finance.utils import extract_price

logger = logging.getLogger("raylook.whatsapp_domain")

ALLOWED_QTY = {0, 3, 4, 6, 8, 9, 12, 16, 20, 24}


@dataclass
class WebhookEvent:
    kind: str
    provider: str
    event_key: str
    raw_event_id: str
    occurred_at: datetime
    payload: Dict[str, Any]
    external_poll_id: Optional[str] = None
    chat_id: Optional[str] = None
    title: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None
    voter_phone: Optional[str] = None
    voter_name: Optional[str] = None
    option_external_id: Optional[str] = None
    option_label: Optional[str] = None
    qty: Optional[int] = None
    drive_file_id: Optional[str] = None
    media_id: Optional[str] = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_datetime(value: Any) -> datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if raw.isdigit():
        iv = int(raw)
        if iv > 10_000_000_000:
            iv = int(iv / 1000)
        return datetime.fromtimestamp(iv, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return _utc_now()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


# Phone BR válido: 55 + DDD (2 dígitos) + 8 ou 9 dígitos = 12 ou 13 chars total.
_BR_PHONE_RE = re.compile(r"^55\d{10,11}$")


def _is_lid_or_invalid_phone(raw: Any) -> bool:
    """True se a string é um LID do WhatsApp (sufixo @lid) OU não casa com
    formato BR depois de extrair os dígitos.

    LIDs (Linked Identifiers) são IDs anônimos que o WhatsApp usa em grupos
    com privacidade alta. Quando aparecem nos webhooks/voters, NÃO são phones
    reais — usá-los criaria clientes fantasmas (incidente 2026-04-18).
    """
    if not raw:
        return True
    s = str(raw).lower()
    if "@lid" in s:
        return True
    canonical = _digits(s)
    if not canonical:
        return True
    return not _BR_PHONE_RE.match(canonical)


def _qty(value: Any) -> int:
    try:
        v = int(float(str(value or 0)))
    except Exception:
        v = 0
    return v if v in ALLOWED_QTY else 0


def _qty_from_text(value: Any) -> int:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return 0
    return _qty(int(digits))


def _event_key(provider: str, raw_event_id: str, kind: str, suffix: str = "") -> str:
    base = f"{provider}:{raw_event_id}:{kind}"
    return f"{base}:{suffix}" if suffix else base


def _sanitize_name(raw: Optional[str], fallback: str = "Cliente") -> str:
    """Remove quebras de linha, tabs e colapsa espaços múltiplos."""
    import re as _re
    if not raw:
        return fallback
    cleaned = _re.sub(r"[\r\n\t]+", " ", str(raw))
    cleaned = _re.sub(r" +", " ", cleaned).strip()
    return cleaned or fallback


def _unwrap(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload.get("body"), dict):
        return payload["body"]
    return payload


def _normalize_chat_id(value: Any) -> str:
    return str(value or "").strip()


def _allowed_group_chat_ids() -> Set[str]:
    configured = {
        _normalize_chat_id(getattr(settings, "OFFICIAL_GROUP_CHAT_ID", "")),
        _normalize_chat_id(getattr(settings, "TEST_GROUP_CHAT_ID", "")),
        _normalize_chat_id(getattr(settings, "AUTHORIZED_GROUP_1", "")),
        _normalize_chat_id(getattr(settings, "AUTHORIZED_GROUP_2", "")),
    }
    return {chat_id for chat_id in configured if chat_id}


def _normalize_options(options: Any) -> List[Dict[str, Any]]:
    if not isinstance(options, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for idx, option in enumerate(options):
        if isinstance(option, dict):
            label = str(option.get("name") or option.get("optionName") or "").strip()
            option_id = str(option.get("id") or option.get("optionId") or label or idx).strip()
        else:
            label = str(option).strip()
            option_id = label or str(idx)
        q = _qty_from_text(label)
        if q in ALLOWED_QTY and q > 0:
            normalized.append(
                {
                    "option_external_id": option_id,
                    "label": label or str(q),
                    "qty": q,
                    "position": idx,
                }
            )
    return normalized


def normalize_webhook_events(
    payload: Dict[str, Any],
    *,
    allowed_chat_ids: Optional[Set[str]] = None,
) -> List[WebhookEvent]:
    root = _unwrap(payload)
    events: List[WebhookEvent] = []
    allowed = allowed_chat_ids or set()

    messages = root.get("messages") if isinstance(root.get("messages"), list) else []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_type = str(msg.get("type") or "").lower()
        chat_id = _normalize_chat_id(msg.get("chat_id")) or None
        if allowed and chat_id and chat_id not in allowed:
            continue
        msg_id = str(msg.get("id") or "").strip()
        if not msg_id:
            continue
        if msg_type == "image":
            media_id = str(((msg.get("image") or {}).get("id")) or "").strip()
            if media_id:
                events.append(
                    WebhookEvent(
                        kind="image_received",
                        provider="whapi",
                        event_key=_event_key("whapi", msg_id, "image_received"),
                        raw_event_id=msg_id,
                        occurred_at=_safe_datetime(msg.get("timestamp")),
                        payload=msg,
                        chat_id=chat_id,
                        media_id=media_id,
                    )
                )
            continue
        if msg_type != "poll":
            continue
        poll = msg.get("poll") or {}
        events.append(
            WebhookEvent(
                kind="poll_created",
                provider="whapi",
                event_key=_event_key("whapi", msg_id, "poll_created"),
                raw_event_id=msg_id,
                occurred_at=_safe_datetime(msg.get("timestamp")),
                payload=msg,
                external_poll_id=msg_id,
                chat_id=chat_id,
                title=str(poll.get("title") or msg_id).strip(),
                options=_normalize_options(poll.get("options")),
            )
        )

    updates = root.get("messages_updates") if isinstance(root.get("messages_updates"), list) else []
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        trigger = upd.get("trigger") or {}
        action = trigger.get("action") or {}
        if str(action.get("type") or "").lower() != "vote":
            continue
        poll_id = str(upd.get("id") or action.get("target") or "").strip()
        if not poll_id:
            continue
        votes = action.get("votes") if isinstance(action.get("votes"), list) else []
        option_external_id = str(votes[0]).strip() if votes else None
        qty = 0
        option_label: Optional[str] = None
        results = (((upd.get("after_update") or {}).get("poll") or {}).get("results")) or []
        if isinstance(results, list) and option_external_id:
            for result in results:
                if str((result or {}).get("id")) == option_external_id:
                    option_label = str((result or {}).get("name") or "").strip() or None
                    qty = _qty_from_text(option_label)
                    break

        raw_from = trigger.get("from")
        if _is_lid_or_invalid_phone(raw_from):
            # LID ou formato fora do padrão BR — ignora (criaria cliente fantasma).
            continue
        voter_phone = _digits(raw_from)
        chat_id = _normalize_chat_id(trigger.get("chat_id")) or None
        if allowed and chat_id and chat_id not in allowed:
            continue
        events.append(
            WebhookEvent(
                kind="vote_updated",
                provider="whapi",
                event_key=_event_key("whapi", poll_id, "vote_updated", str(trigger.get("id") or (voter_phone or "unknown") + ":" + str(qty))),
                raw_event_id=str(upd.get("event_id") or upd.get("id") or poll_id),
                occurred_at=_safe_datetime(upd.get("timestamp")),
                payload=upd,
                external_poll_id=poll_id,
                chat_id=chat_id,
                voter_phone=voter_phone,
                voter_name=_sanitize_name(trigger.get("from_name")),
                option_external_id=option_external_id,
                option_label=option_label,
                qty=qty,
            )
        )

    if events:
        events.sort(key=lambda event: event.occurred_at)
        return events

    # Fallback for Evolution payloads: parse fields close to WhatsApp schema.
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    poll = message.get("pollCreationMessage")
    if isinstance(poll, dict):
        poll_id = str(key.get("id") or data.get("id") or "").strip()
        if poll_id:
            chat_id = _normalize_chat_id(key.get("remoteJid") or data.get("remoteJid")) or None
            if allowed and chat_id and chat_id not in allowed:
                return events
            events.append(
                WebhookEvent(
                    kind="poll_created",
                    provider="evolution",
                    event_key=_event_key("evolution", poll_id, "poll_created"),
                    raw_event_id=poll_id,
                    occurred_at=_safe_datetime(data.get("messageTimestamp") or data.get("timestamp")),
                    payload=data,
                    external_poll_id=poll_id,
                    chat_id=chat_id,
                    title=str(poll.get("name") or poll_id).strip(),
                    options=_normalize_options(poll.get("options")),
                )
            )

    # Evolution: pollUpdateMessage — a vote on an existing poll
    poll_update = message.get("pollUpdateMessage")
    if isinstance(poll_update, dict) and not poll:
        poll_msg_key = poll_update.get("pollCreationMessageKey") or {}
        poll_id = str(poll_msg_key.get("id") or "").strip()
        if poll_id:
            chat_id = _normalize_chat_id(key.get("remoteJid") or data.get("remoteJid")) or None
            if allowed and chat_id and chat_id not in allowed:
                return events
            voter_jid = str(key.get("participant") or key.get("remoteJid") or "").strip()
            if _is_lid_or_invalid_phone(voter_jid):
                # LID ou formato fora do padrão BR — ignora (criaria cliente fantasma).
                return events
            voter_phone = _digits(voter_jid.split("@")[0] if "@" in voter_jid else voter_jid)
            voter_name = _sanitize_name(data.get("pushName"))

            # Decode the selected options from the vote payload
            vote_msg = poll_update.get("vote") or {}
            selected_options = vote_msg.get("selectedOptions") if isinstance(vote_msg.get("selectedOptions"), list) else []

            # Try to get qty from selected option SHA256 matching or from message context
            # Evolution sends selectedOptions as [{"name": "3"}, ...] or raw SHA256 hashes
            qty = 0
            option_external_id = None
            for opt in selected_options:
                if isinstance(opt, dict):
                    opt_name = str(opt.get("name") or "").strip()
                    if opt_name:
                        q = _qty_from_text(opt_name)
                        if q > qty:
                            qty = q
                            option_external_id = opt_name

            # If we couldn't parse from selectedOptions, try pollCreationMessage context
            if qty == 0:
                context_msg = data.get("contextInfo", {}).get("quotedMessage", {}).get("pollCreationMessage") or {}
                context_options = context_msg.get("options") or []
                # Match by index if selectedOptions has raw hashes
                if selected_options and context_options:
                    for idx, ctx_opt in enumerate(context_options):
                        opt_name = str(ctx_opt.get("optionName") or ctx_opt.get("name") or "").strip()
                        q = _qty_from_text(opt_name)
                        if q > 0:
                            qty = q
                            option_external_id = opt_name
                            break

            events.append(
                WebhookEvent(
                    kind="vote_updated",
                    provider="evolution",
                    event_key=_event_key("evolution", poll_id, "vote_updated", voter_phone or "unknown"),
                    raw_event_id=str(key.get("id") or data.get("id") or poll_id),
                    occurred_at=_safe_datetime(data.get("messageTimestamp") or data.get("timestamp")),
                    payload=data,
                    external_poll_id=poll_id,
                    chat_id=chat_id,
                    voter_phone=voter_phone,
                    voter_name=voter_name,
                    option_external_id=option_external_id,
                    option_label=option_external_id,
                    qty=qty,
                )
            )

    return events


class PollService:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def upsert_poll(self, event: WebhookEvent) -> Dict[str, Any]:
        if not event.external_poll_id:
            raise RuntimeError("Missing poll id")
        title = (event.title or "").strip() or event.external_poll_id

        existing_products = self.client.select(
            "produtos",
            columns="id,nome,valor_unitario,drive_file_id",
            filters=[("nome", "eq", title)],
            order="created_at.desc",
            limit=1,
        )
        if isinstance(existing_products, list) and existing_products:
            # F-061: não sobrescreve mais `produtos.drive_file_id` — imagem
            # fica isolada na enquete, então produtos compartilhados não
            # perdem a imagem original nem poluem outras enquetes.
            produto = existing_products[0]
        else:
            produto = self.client.insert(
                "produtos",
                {
                    "nome": title,
                    "descricao": title,
                    "valor_unitario": round(float(extract_price(title) or 0.0), 2),
                    "drive_file_id": event.drive_file_id,
                },
            )[0]

        enquete_payload = {
            "external_poll_id": event.external_poll_id,
            "provider": event.provider,
            "chat_id": event.chat_id,
            "produto_id": produto["id"],
            "titulo": title,
            "status": "open",
            "created_at_provider": event.occurred_at.isoformat(),
        }
        # F-061: imagem vai direto na enquete — cada post do WhatsApp carrega
        # sua foto independentemente do produto.
        if event.drive_file_id:
            enquete_payload["drive_file_id"] = event.drive_file_id

        enquete = self.client.upsert_one(
            "enquetes",
            enquete_payload,
            on_conflict="external_poll_id",
        )

        for option in event.options or []:
            self.client.insert(
                "enquete_alternativas",
                {
                    "enquete_id": enquete["id"],
                    "option_external_id": option.get("option_external_id"),
                    "label": option.get("label"),
                    "qty": _qty(option.get("qty")),
                    "position": int(option.get("position") or 0),
                },
                upsert=True,
                on_conflict="enquete_id,qty",
                returning="minimal",
            )

        return enquete


class PackageService:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def _subset_sum(self, votes: List[Dict[str, Any]], target: int) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, Any]]]:
        def bt(i: int, current: int, acc: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
            if current == target:
                return acc
            if current > target or i >= len(votes):
                return None
            v = votes[i]
            found = bt(i + 1, current + int(v["qty"]), acc + [v])
            if found:
                return found
            return bt(i + 1, current, acc)

        subset = bt(0, 0, [])
        if not subset:
            return None, votes
        ids = {v["id"] for v in subset}
        return subset, [v for v in votes if v["id"] not in ids]

    def rebuild_for_poll(self, enquete_id: str) -> Dict[str, Any]:
        # Fetch current votes (source of truth = last user action)
        votes = self.client.select(
            "votos", columns="id,cliente_id,alternativa_id,qty,voted_at,status",
            filters=[("enquete_id", "eq", enquete_id)],
        )
        if not isinstance(votes, list):
            return {"closed_count": 0, "open_qty": 0}

        # Active votes: qty > 0 and status != out
        active_votes = [v for v in votes if int(v.get("qty") or 0) > 0 and str(v.get("status") or "").strip().lower() != "out"]
        active_votes.sort(key=lambda v: (-int(v.get("qty") or 0), _safe_datetime(v.get("voted_at"))))

        poll = self.client.select(
            "enquetes",
            columns="id,produto_id,fornecedor,produtos(id,valor_unitario)",
            filters=[("id", "eq", enquete_id)],
            single=True,
        )
        if not isinstance(poll, dict):
            return {"closed_count": 0, "open_qty": 0}

        produto_id = poll.get("produto_id")
        enquete_fornecedor = (poll.get("fornecedor") or "").strip() or None
        unit_price = 0.0
        produto = poll.get("produtos")
        if isinstance(produto, dict):
            unit_price = float(produto.get("valor_unitario") or 0.0)
            if not produto_id:
                produto_id = produto.get("id")
        if not produto_id:
            return {"closed_count": 0, "open_qty": sum(int(v.get("qty") or 0) for v in active_votes)}

        # Get IDs of packages that are finalized (approved/cancelled) — never touch these
        finalized_pkgs = self.client.select(
            "pacotes", columns="id,status",
            filters=[("enquete_id", "eq", enquete_id), ("status", "in", ["approved", "cancelled"])],
        )
        finalized_pkg_ids = {str(p["id"]) for p in (finalized_pkgs if isinstance(finalized_pkgs, list) else [])}

        # Get votes already consumed by approved packages (these are locked)
        approved_pkg_ids = {str(p["id"]) for p in (finalized_pkgs if isinstance(finalized_pkgs, list) else []) if p.get("status") == "approved"}
        approved_assignments = []
        if approved_pkg_ids:
            for pkg_id in approved_pkg_ids:
                rows = self.client.select(
                    "pacote_clientes", columns="voto_id,cliente_id,qty",
                    filters=[("pacote_id", "eq", pkg_id)],
                )
                if isinstance(rows, list):
                    approved_assignments.extend(rows)

        # Build map of qty already consumed per cliente_id in approved packages
        from collections import defaultdict
        approved_qty_by_client = defaultdict(int)
        for a in approved_assignments:
            approved_qty_by_client[str(a["cliente_id"])] += int(a.get("qty") or 0)

        # Antes de deletar pacotes fechados, salvar tag/custom_title indexado
        # pelo conjunto de vote_ids — pra restaurar depois nos pacotes equivalentes.
        # Isso previne que tags/títulos personalizados se percam quando o
        # rebuild é disparado por novos votos.
        non_finalized = self.client.select(
            "pacotes", columns="id,status,tag,custom_title,fornecedor",
            filters=[("enquete_id", "eq", enquete_id), ("status", "in", ["open", "closed"])],
        )
        non_finalized_list = non_finalized if isinstance(non_finalized, list) else []

        # Mapa: frozenset(vote_ids) -> {tag, custom_title, fornecedor}
        preserved_metadata: Dict[frozenset, Dict[str, Any]] = {}
        for pkg in non_finalized_list:
            pkg_id_to_check = pkg.get("id")
            tag_val = (pkg.get("tag") or "").strip() or None
            title_val = (pkg.get("custom_title") or "").strip() or None
            forn_val = (pkg.get("fornecedor") or "").strip() or None
            # Só vale preservar se há algum metadado custom
            if not (tag_val or title_val or forn_val):
                continue
            try:
                pcs = self.client.select(
                    "pacote_clientes",
                    columns="voto_id",
                    filters=[("pacote_id", "eq", pkg_id_to_check)],
                )
                vote_ids_set = frozenset(str(p["voto_id"]) for p in (pcs if isinstance(pcs, list) else []) if p.get("voto_id"))
                if vote_ids_set:
                    preserved_metadata[vote_ids_set] = {
                        "tag": tag_val,
                        "custom_title": title_val,
                        "fornecedor": forn_val,
                    }
            except Exception:
                pass

        # Delete all non-finalized packages (open + closed) and their pacote_clientes
        for pkg in non_finalized_list:
            self.client.delete("pacote_clientes", filters=[("pacote_id", "eq", pkg["id"])])
            self.client.delete("pacotes", filters=[("id", "eq", pkg["id"])])

        # Calculate remaining votes after subtracting approved assignments
        available_votes = []
        for v in active_votes:
            cid = str(v["cliente_id"])
            current_qty = int(v.get("qty") or 0)
            consumed = approved_qty_by_client.get(cid, 0)
            remaining = max(current_qty - consumed, 0)
            if remaining > 0:
                available_votes.append({**v, "qty": remaining})

        pending = available_votes[:]
        closed_count = 0
        commission_per_piece = float(settings.COMMISSION_PER_PIECE)

        while True:
            subset, remaining = self._subset_sum(pending, 24)
            if not subset:
                break
            if len(subset) < 1:
                break

            latest = max(_safe_datetime(v.get("voted_at")) for v in subset)
            opened_at = _safe_datetime(subset[0].get("voted_at")).isoformat()
            closed_at = latest.isoformat()

            # F-001 fix: substitui 2N+1 round-trips do PostgREST por 1 chamada
            # à RPC `close_package`, que roda tudo numa transação única com
            # pg_advisory_xact_lock(enquete_id). Elimina a possibilidade de
            # pacote órfão (closed sem pacote_clientes) e race condition de
            # sequence_no (F-004).
            votes_payload: List[Dict[str, Any]] = []
            for vote in subset:
                qty = int(vote.get("qty") or 0)
                subtotal = round(unit_price * qty, 2)
                commission_amount = round(qty * commission_per_piece, 2)
                total_amount = round(subtotal + commission_amount, 2)
                votes_payload.append(
                    {
                        "vote_id": vote["id"],
                        "cliente_id": vote["cliente_id"],
                        "qty": qty,
                        "unit_price": unit_price,
                        "subtotal": subtotal,
                        "commission_percent": 0,
                        "commission_amount": commission_amount,
                        "total_amount": total_amount,
                    }
                )

            try:
                rpc_result = self.client.rpc(
                    "close_package",
                    {
                        "p_enquete_id": enquete_id,
                        "p_produto_id": produto_id,
                        "p_votes": votes_payload,
                        "p_opened_at": opened_at,
                        "p_closed_at": closed_at,
                        "p_capacidade_total": 24,
                        "p_total_qty": 24,
                    },
                )
                if isinstance(rpc_result, dict) and rpc_result.get("status") != "ok":
                    logger.warning(
                        "close_package RPC retornou status inesperado: %s enquete=%s",
                        rpc_result,
                        enquete_id,
                    )
                    break
                # Propagar FORNECEDOR da enquete para o pacote recém-criado
                # (sem mexer no campo `tag`, que é tipo de peça)
                new_pkg_id = None
                if isinstance(rpc_result, dict):
                    new_pkg_id = rpc_result.get("pacote_id") or rpc_result.get("id")
                if new_pkg_id:
                    update_payload: Dict[str, Any] = {}
                    # Restaurar metadados preservados se o conjunto de votos bate
                    subset_vote_ids = frozenset(str(v["id"]) for v in subset)
                    preserved = preserved_metadata.get(subset_vote_ids)
                    if preserved:
                        if preserved.get("tag"):
                            update_payload["tag"] = preserved["tag"]
                        if preserved.get("custom_title"):
                            update_payload["custom_title"] = preserved["custom_title"]
                        # Se o pacote velho tinha fornecedor, prioriza esse
                        if preserved.get("fornecedor"):
                            update_payload["fornecedor"] = preserved["fornecedor"]
                    # Senão, herda fornecedor da enquete
                    if "fornecedor" not in update_payload and enquete_fornecedor:
                        update_payload["fornecedor"] = enquete_fornecedor

                    if update_payload:
                        try:
                            self.client.update(
                                "pacotes",
                                update_payload,
                                filters=[("id", "eq", str(new_pkg_id))],
                            )
                        except Exception:
                            logger.warning("falha propagando metadados pro pacote %s", new_pkg_id)
            except Exception:
                logger.exception(
                    "F-001: close_package RPC falhou para enquete=%s, abortando rebuild",
                    enquete_id,
                )
                break

            closed_count += 1
            pending = remaining

        open_qty = sum(int(v.get("qty") or 0) for v in pending)
        if pending and open_qty > 0:
            open_pkg_payload = {
                "enquete_id": enquete_id,
                "sequence_no": 0,
                "capacidade_total": 24,
                "total_qty": open_qty,
                "participants_count": len(pending),
                "status": "open",
                "opened_at": _safe_datetime(pending[0].get("voted_at")).isoformat(),
            }
            if enquete_fornecedor:
                open_pkg_payload["fornecedor"] = enquete_fornecedor
            self.client.insert(
                "pacotes",
                open_pkg_payload,
                upsert=True,
                on_conflict="enquete_id,sequence_no",
                returning="minimal",
            )
        return {"closed_count": closed_count, "open_qty": open_qty}


class VoteService:
    def __init__(self, client: SupabaseRestClient, poll_service: PollService, package_service: PackageService) -> None:
        self.client = client
        self.poll_service = poll_service
        self.package_service = package_service

    def process_vote(self, event: WebhookEvent) -> Dict[str, Any]:
        if not event.external_poll_id or not event.voter_phone:
            raise RuntimeError("Missing vote fields")
        poll = self.client.select(
            "enquetes",
            columns="id,chat_id,titulo",
            filters=[("external_poll_id", "eq", event.external_poll_id)],
            single=True,
        )
        if not isinstance(poll, dict):
            # F-026 fix: ao criar enquete sintética a partir de um vote_updated,
            # tentar extrair o título real do payload (after_update.poll.title)
            # ao invés de cair direto em `external_poll_id` (fallback silencioso
            # que gerou 89 enquetes bugadas no staging).
            synth_title = event.external_poll_id
            try:
                payload_dict = event.payload if isinstance(event.payload, dict) else {}
                after = payload_dict.get("after_update") or {}
                poll_data = after.get("poll") or {}
                extracted = (poll_data.get("title") or "").strip()
                if extracted:
                    synth_title = extracted
                else:
                    logger.error(
                        "F-026: synthetic poll_created sem título no payload "
                        "(fallback para external_poll_id). poll=%s event_key=%s",
                        event.external_poll_id,
                        event.event_key,
                    )
            except Exception as exc:
                logger.warning(
                    "F-026: falha extraindo título do payload vote_updated: %s (poll=%s)",
                    exc,
                    event.external_poll_id,
                )

            poll = self.poll_service.upsert_poll(
                WebhookEvent(
                    kind="poll_created",
                    provider=event.provider,
                    event_key=f"synthetic:{event.external_poll_id}",
                    raw_event_id=event.external_poll_id,
                    occurred_at=event.occurred_at,
                    payload={},
                    external_poll_id=event.external_poll_id,
                    chat_id=event.chat_id,
                    title=synth_title,
                    options=[
                        {"option_external_id": "3", "label": "3", "qty": 3, "position": 0},
                        {"option_external_id": "6", "label": "6", "qty": 6, "position": 1},
                        {"option_external_id": "9", "label": "9", "qty": 9, "position": 2},
                        {"option_external_id": "12", "label": "12", "qty": 12, "position": 3},
                    ],
                )
            )
        elif not event.chat_id and poll.get("chat_id"):
            event.chat_id = str(poll.get("chat_id")).strip() or None
        # Inserir cliente se novo. Se já existe, NÃO sobrescrever o nome
        # (admin pode ter renomeado manualmente no dash).
        existing = self.client.select(
            "clientes", columns="id,nome,celular",
            filters=[("celular", "eq", event.voter_phone)], limit=1,
        )
        if isinstance(existing, list) and existing:
            client_row = existing[0]
        else:
            client_row = self.client.upsert_one(
                "clientes",
                {"nome": _sanitize_name(event.voter_name), "celular": event.voter_phone},
                on_conflict="celular",
            )
        qty = _qty(event.qty)
        alternatives = self.client.select(
            "enquete_alternativas",
            columns="id,qty,label,option_external_id",
            filters=[("enquete_id", "eq", poll["id"]), ("qty", "eq", qty)],
            limit=1,
        )
        alternativa_id = alternatives[0]["id"] if isinstance(alternatives, list) and alternatives else None
        self.client.insert(
            "votos_eventos",
            {
                "enquete_id": poll["id"],
                "cliente_id": client_row["id"],
                "alternativa_id": alternativa_id,
                "qty": qty,
                "action": "vote" if qty > 0 else "remove",
                "occurred_at": event.occurred_at.isoformat(),
                "raw_event_id": event.raw_event_id,
                "payload_json": event.payload,
            },
            returning="minimal",
        )
        voto = self.client.upsert_one(
            "votos",
            {
                "enquete_id": poll["id"],
                "cliente_id": client_row["id"],
                "alternativa_id": alternativa_id,
                "qty": qty,
                "status": "in" if qty > 0 else "out",
                "voted_at": event.occurred_at.isoformat(),
            },
            on_conflict="enquete_id,cliente_id",
        )
        package_result = self.package_service.rebuild_for_poll(poll["id"])
        return {"voto_id": voto["id"], "package_result": package_result}


class SalesService:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def approve_package(self, pacote_id: str) -> Dict[str, Any]:
        pacote = self.client.select("pacotes", columns="id,status", filters=[("id", "eq", pacote_id)], single=True)
        if not isinstance(pacote, dict):
            raise KeyError("package_not_found")

        package_clients = self.client.select(
            "pacote_clientes",
            columns="id,pacote_id,cliente_id,produto_id,qty,unit_price,subtotal,commission_percent,commission_amount,total_amount",
            filters=[("pacote_id", "eq", pacote_id)],
        )
        if not isinstance(package_clients, list):
            package_clients = []
        if len(package_clients) < 1:
            raise RuntimeError("Pacote precisa ter no minimo 1 cliente para aprovacao.")

        vendas: List[Dict[str, Any]] = []
        pagamentos: List[Dict[str, Any]] = []
        for item in package_clients:
            venda = self.client.upsert_one(
                "vendas",
                {
                    "pacote_id": item["pacote_id"],
                    "cliente_id": item["cliente_id"],
                    "produto_id": item["produto_id"],
                    "pacote_cliente_id": item["id"],
                    "qty": int(item["qty"]),
                    "unit_price": float(item["unit_price"]),
                    "subtotal": float(item["subtotal"]),
                    "commission_percent": 0,
                    "commission_amount": float(item["commission_amount"]),
                    "total_amount": float(item["total_amount"]),
                    "status": "approved",
                    "sold_at": _utc_now().isoformat(),
                },
                on_conflict="pacote_id,cliente_id",
            )
            vendas.append(venda)

            pagamento = self.client.upsert_one(
                "pagamentos",
                {
                    "venda_id": venda["id"],
                    "provider": "asaas",
                    "status": "created",
                    "payload_json": {},
                },
                on_conflict="venda_id",
            )
            pagamentos.append(pagamento)

        self.client.update(
            "pacotes",
            {"status": "approved", "approved_at": _utc_now().isoformat()},
            filters=[("id", "eq", pacote_id)],
            returning="minimal",
        )
        return {"pacote_id": pacote_id, "status": "approved", "vendas": vendas, "pagamentos": pagamentos}


class PaymentService:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def upsert_payment_status(
        self,
        *,
        venda_id: str,
        provider_customer_id: Optional[str] = None,
        provider_payment_id: Optional[str] = None,
        payment_link: Optional[str] = None,
        pix_payload: Optional[str] = None,
        due_date: Optional[str] = None,
        paid_at: Optional[datetime] = None,
        status: str = "created",
        payload_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.client.upsert_one(
            "pagamentos",
            {
                "venda_id": venda_id,
                "provider": "asaas",
                "provider_customer_id": provider_customer_id,
                "provider_payment_id": provider_payment_id,
                "payment_link": payment_link,
                "pix_payload": pix_payload,
                "due_date": due_date,
                "paid_at": paid_at.isoformat() if paid_at else None,
                "status": status,
                "payload_json": payload_json or {},
            },
            on_conflict="venda_id",
        )


class WebhookIngestionService:
    def __init__(self, client: Optional[SupabaseRestClient] = None) -> None:
        self.client = client or SupabaseRestClient.from_settings()
        self.poll_service = PollService(self.client)
        self.package_service = PackageService(self.client)
        self.vote_service = VoteService(self.client, self.poll_service, self.package_service)

    def _resolve_poll_chat_id(self, external_poll_id: Optional[str]) -> Optional[str]:
        if not external_poll_id:
            return None
        try:
            row = self.client.select(
                "enquetes",
                columns="chat_id",
                filters=[("external_poll_id", "eq", external_poll_id)],
                single=True,
            )
        except Exception:
            return None
        if not isinstance(row, dict):
            return None
        chat_id = _normalize_chat_id(row.get("chat_id"))
        return chat_id or None

    def _ensure_event_chat_id(self, event: WebhookEvent) -> Optional[str]:
        chat_id = _normalize_chat_id(event.chat_id)
        if not chat_id:
            chat_id = _normalize_chat_id(self._resolve_poll_chat_id(event.external_poll_id))
        event.chat_id = chat_id or None
        return event.chat_id

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not supabase_domain_enabled():
            raise RuntimeError("SUPABASE_DOMAIN_ENABLED=false")
        from app.services.recent_image_cache import remember_recent_image

        allowed_chat_ids = _allowed_group_chat_ids()
        events = normalize_webhook_events(payload)
        if not events:
            return {"status": "ignored", "processed": 0, "duplicates": 0, "ignored": 0}

        processed = 0
        duplicates = 0
        ignored = 0
        errors: List[str] = []
        for event in events:
            event_chat_id = _normalize_chat_id(self._ensure_event_chat_id(event))
            if allowed_chat_ids and event_chat_id not in allowed_chat_ids:
                ignored += 1
                logger.info(
                    "Webhook ignored for unauthorized group kind=%s poll_id=%s chat_id=%s",
                    event.kind,
                    event.external_poll_id,
                    event_chat_id or "missing",
                )
                continue
            try:
                inbox = self.client.insert(
                    "webhook_inbox",
                    {
                        "provider": event.provider,
                        "event_kind": event.kind,
                        "event_key": event.event_key,
                        "payload_json": event.payload,
                        "status": "received",
                    },
                )[0]
            except Exception as exc:
                if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                    duplicates += 1
                    continue
                errors.append(str(exc))
                continue

            try:
                if event.kind == "poll_created":
                    self.poll_service.upsert_poll(event)
                elif event.kind == "vote_updated":
                    self.vote_service.process_vote(event)
                elif event.kind == "image_received" and event.chat_id and event.media_id:
                    remember_recent_image(
                        chat_id=event.chat_id,
                        message_id=event.raw_event_id,
                        media_id=event.media_id,
                        occurred_at=event.occurred_at,
                    )
                self.client.update(
                    "webhook_inbox",
                    {"status": "processed", "processed_at": _utc_now().isoformat(), "error": None},
                    filters=[("id", "eq", inbox["id"])],
                    returning="minimal",
                )
                processed += 1
            except Exception as exc:
                logger.exception("Webhook processing failed key=%s", event.event_key)
                errors.append(str(exc))
                self.client.update(
                    "webhook_inbox",
                    {"status": "failed", "processed_at": _utc_now().isoformat(), "error": str(exc)[:1000]},
                    filters=[("id", "eq", inbox["id"])],
                    returning="minimal",
                )

        status = "ok" if not errors else "partial"
        if processed == 0 and duplicates == 0 and ignored > 0 and not errors:
            status = "ignored"
        return {
            "status": status,
            "processed": processed,
            "duplicates": duplicates,
            "ignored": ignored,
            "errors": errors,
        }


def build_domain_services(client: Optional[SupabaseRestClient] = None) -> Dict[str, Any]:
    sb = client or SupabaseRestClient.from_settings()
    return {
        "client": sb,
        "poll_service": PollService(sb),
        "package_service": PackageService(sb),
        "sales_service": SalesService(sb),
        "payment_service": PaymentService(sb),
        "webhook_service": WebhookIngestionService(sb),
    }
