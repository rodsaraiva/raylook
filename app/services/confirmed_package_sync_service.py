"""Sincroniza composição de pacote confirmado com vendas/pagamentos no Postgres.

Usado quando a admin edita membros de um pacote já confirmado no dashboard.
Faz diff cirúrgico: adiciona, remove ou atualiza apenas o que mudou.

Regra crítica: se um cliente pago é removido, mantém venda/pagamento intactos
(só desacopla de pacote_clientes setando pacote_cliente_id=NULL). A cobrança
permanece visível no portal do cliente com status 'paid' — admin resolve
manualmente com o cliente (reembolso) e depois cancela a cobrança no Financeiro
se quiser.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.confirmed_package_edit_service import (
    PAID_STATUSES,
    _clean_phone,
    diff_votes_by_phone,
)
from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.confirmed_package_sync")

COMMISSION_PER_PIECE = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc_financials(unit_price: float, qty: int) -> Dict[str, float]:
    subtotal = round(unit_price * qty, 2)
    commission_amount = round(qty * COMMISSION_PER_PIECE, 2)
    total_amount = round(subtotal + commission_amount, 2)
    return {
        "subtotal": subtotal,
        "commission_amount": commission_amount,
        "total_amount": total_amount,
    }


class ConfirmedPackageSyncService:
    """Sincroniza pacote_clientes + vendas + pagamentos a partir do diff de votos."""

    def __init__(self, sb: Optional[SupabaseRestClient] = None) -> None:
        self.sb = sb or SupabaseRestClient.from_settings()

    # ─────────────────────────────────────────────────────────────────────────
    # Check (pré-análise, não altera nada)
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(
        self,
        pacote_uuid: str,
        current_votes: List[Dict[str, Any]],
        new_votes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Retorna diff + quais removidos já pagaram (requer confirmação)."""
        diff = diff_votes_by_phone(current_votes, new_votes)
        paid_removals = self._detect_paid_removals(pacote_uuid, diff["removed"])
        return {
            "diff": diff,
            "paid_removals": paid_removals,
            "requires_confirmation": len(paid_removals) > 0,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Apply (faz todas as mudanças no banco)
    # ─────────────────────────────────────────────────────────────────────────

    def apply(
        self,
        pacote_uuid: str,
        current_votes: List[Dict[str, Any]],
        new_votes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Aplica o diff no banco. Assume que analyze() já foi consultado
        e, se havia paid_removals, o usuário já confirmou a ação."""
        diff = diff_votes_by_phone(current_votes, new_votes)

        pacote_ctx = self._load_pacote_context(pacote_uuid)
        if not pacote_ctx:
            raise RuntimeError(f"Pacote {pacote_uuid} não encontrado")

        summary = {
            "added": 0,
            "removed_unpaid": 0,
            "removed_paid_preserved": 0,
            "changed_qty": 0,
            "errors": [],
        }

        # REMOVED: trata primeiro pra liberar unique index (pacote_id, cliente_id)
        for vote in diff["removed"]:
            try:
                action = self._remove_member(pacote_uuid, vote["phone"])
                if action == "deleted":
                    summary["removed_unpaid"] += 1
                elif action == "preserved_paid":
                    summary["removed_paid_preserved"] += 1
            except Exception as exc:
                logger.exception("apply.remove error phone=%s: %s", vote.get("phone"), exc)
                summary["errors"].append(f"remove {vote.get('phone')}: {exc}")

        # CHANGED QTY: atualiza qty+subtotal em pacote_cliente e venda
        for vote in diff["changed"]:
            try:
                self._update_member_qty(pacote_ctx, vote["phone"], int(vote["new_qty"]))
                summary["changed_qty"] += 1
            except Exception as exc:
                logger.exception("apply.change_qty error phone=%s: %s", vote.get("phone"), exc)
                summary["errors"].append(f"change_qty {vote.get('phone')}: {exc}")

        # ADDED: insere pacote_cliente + venda + pagamento
        for vote in diff["added"]:
            try:
                self._add_member(pacote_ctx, vote["phone"], int(vote["qty"]), vote.get("name"))
                summary["added"] += 1
            except Exception as exc:
                logger.exception("apply.add error phone=%s: %s", vote.get("phone"), exc)
                summary["errors"].append(f"add {vote.get('phone')}: {exc}")

        return summary

    # ─────────────────────────────────────────────────────────────────────────
    # Internos
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_paid_removals(
        self,
        pacote_uuid: str,
        removed_votes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Dos votos removidos, quais clientes já pagaram (status em PAID_STATUSES)."""
        if not removed_votes:
            return []
        paid: List[Dict[str, Any]] = []
        for vote in removed_votes:
            phone = _clean_phone(vote.get("phone"))
            if not phone:
                continue
            cliente = self._find_client_by_phone(phone)
            if not cliente:
                continue
            venda = self.sb.select(
                "vendas",
                columns="id",
                filters=[("pacote_id", "eq", pacote_uuid), ("cliente_id", "eq", cliente["id"])],
                single=True,
            )
            if not isinstance(venda, dict):
                continue
            pag = self.sb.select(
                "pagamentos",
                columns="id,status,paid_at",
                filters=[("venda_id", "eq", venda["id"])],
                single=True,
            )
            pag_status = (pag.get("status") or "").lower() if isinstance(pag, dict) else ""
            if pag_status in PAID_STATUSES:
                paid.append({
                    "phone": phone,
                    "name": cliente.get("nome") or vote.get("name") or "Cliente",
                    "qty": int(vote.get("qty") or 0),
                    "venda_id": venda["id"],
                    "pagamento_id": pag.get("id"),
                    "paid_at": pag.get("paid_at"),
                })
        return paid

    def _load_pacote_context(self, pacote_uuid: str) -> Optional[Dict[str, Any]]:
        """Carrega pacote com enquete→produto pra saber unit_price/produto_id."""
        row = self.sb.select(
            "pacotes",
            columns=(
                "id,enquete_id,"
                "enquete:enquete_id(id,titulo,produto_id,"
                "produto:produto_id(id,valor_unitario,nome))"
            ),
            filters=[("id", "eq", pacote_uuid)],
            single=True,
        )
        if not isinstance(row, dict):
            return None
        enquete = row.get("enquete") or {}
        produto = enquete.get("produto") or {}
        return {
            "pacote_id": row.get("id"),
            "enquete_id": enquete.get("id") or row.get("enquete_id"),
            "produto_id": produto.get("id") or enquete.get("produto_id"),
            "unit_price": float(produto.get("valor_unitario") or 0.0),
        }

    def _find_client_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        phone = _clean_phone(phone)
        if not phone:
            return None
        rows = self.sb.select(
            "clientes",
            columns="id,nome,celular",
            filters=[("celular", "eq", phone)],
            limit=1,
        )
        return rows[0] if isinstance(rows, list) and rows else None

    def _remove_member(self, pacote_uuid: str, phone: str) -> str:
        """Remove cliente do pacote. Se pagou, mantém venda/pagamento.
        Retorna 'deleted' (não pago), 'preserved_paid' (pago), ou 'nothing' (nada a fazer)."""
        cliente = self._find_client_by_phone(phone)
        if not cliente:
            return "nothing"
        cliente_id = cliente["id"]

        # Busca pacote_cliente + venda + pagamento
        pc = self.sb.select(
            "pacote_clientes",
            columns="id",
            filters=[("pacote_id", "eq", pacote_uuid), ("cliente_id", "eq", cliente_id)],
            single=True,
        )
        venda = self.sb.select(
            "vendas",
            columns="id",
            filters=[("pacote_id", "eq", pacote_uuid), ("cliente_id", "eq", cliente_id)],
            single=True,
        )
        pagamento = None
        if isinstance(venda, dict):
            pagamento = self.sb.select(
                "pagamentos",
                columns="id,status",
                filters=[("venda_id", "eq", venda["id"])],
                single=True,
            )

        pag_status = (pagamento.get("status") or "").lower() if isinstance(pagamento, dict) else ""
        is_paid = pag_status in PAID_STATUSES

        if is_paid:
            # Preserva venda/pagamento — só desacopla do pacote_cliente
            if isinstance(venda, dict):
                self.sb.update(
                    "vendas",
                    {"pacote_cliente_id": None, "updated_at": _now_iso()},
                    filters=[("id", "eq", venda["id"])],
                    returning="minimal",
                )
            if isinstance(pc, dict):
                self.sb._request(
                    "DELETE",
                    f"/rest/v1/pacote_clientes?id=eq.{pc['id']}",
                )
            return "preserved_paid"

        # Não pago: deleta tudo
        if isinstance(pagamento, dict):
            self.sb._request(
                "DELETE",
                f"/rest/v1/pagamentos?id=eq.{pagamento['id']}",
            )
        if isinstance(venda, dict):
            self.sb._request(
                "DELETE",
                f"/rest/v1/vendas?id=eq.{venda['id']}",
            )
        if isinstance(pc, dict):
            self.sb._request(
                "DELETE",
                f"/rest/v1/pacote_clientes?id=eq.{pc['id']}",
            )
        return "deleted"

    def _update_member_qty(self, pacote_ctx: Dict[str, Any], phone: str, new_qty: int) -> None:
        cliente = self._find_client_by_phone(phone)
        if not cliente:
            raise RuntimeError(f"Cliente não encontrado para {phone}")
        unit_price = float(pacote_ctx["unit_price"] or 0)
        fin = _calc_financials(unit_price, new_qty)

        pc = self.sb.select(
            "pacote_clientes",
            columns="id",
            filters=[("pacote_id", "eq", pacote_ctx["pacote_id"]), ("cliente_id", "eq", cliente["id"])],
            single=True,
        )
        if isinstance(pc, dict):
            self.sb.update(
                "pacote_clientes",
                {
                    "qty": new_qty,
                    "subtotal": fin["subtotal"],
                    "commission_amount": fin["commission_amount"],
                    "total_amount": fin["total_amount"],
                    "updated_at": _now_iso(),
                },
                filters=[("id", "eq", pc["id"])],
                returning="minimal",
            )
        venda = self.sb.select(
            "vendas",
            columns="id,status",
            filters=[("pacote_id", "eq", pacote_ctx["pacote_id"]), ("cliente_id", "eq", cliente["id"])],
            single=True,
        )
        if isinstance(venda, dict):
            self.sb.update(
                "vendas",
                {
                    "qty": new_qty,
                    "subtotal": fin["subtotal"],
                    "commission_amount": fin["commission_amount"],
                    "total_amount": fin["total_amount"],
                    "updated_at": _now_iso(),
                },
                filters=[("id", "eq", venda["id"])],
                returning="minimal",
            )

    def _add_member(
        self,
        pacote_ctx: Dict[str, Any],
        phone: str,
        qty: int,
        name: Optional[str] = None,
    ) -> None:
        cliente = self._find_client_by_phone(phone)
        if not cliente:
            # Cria cliente novo
            cliente = self.sb.upsert_one(
                "clientes",
                {"nome": (name or "Cliente").strip() or "Cliente", "celular": _clean_phone(phone)},
                on_conflict="celular",
            )
        cliente_id = cliente["id"]
        unit_price = float(pacote_ctx["unit_price"] or 0)
        fin = _calc_financials(unit_price, qty)

        # voto_id: tenta achar voto do cliente na enquete (pacote_clientes exige NOT NULL)
        voto_row = self.sb.select(
            "votos",
            columns="id",
            filters=[("enquete_id", "eq", pacote_ctx["enquete_id"]), ("cliente_id", "eq", cliente_id)],
            order="voted_at.desc",
            limit=1,
        )
        voto_id = None
        if isinstance(voto_row, list) and voto_row:
            voto_id = voto_row[0].get("id")
        if not voto_id:
            # Cria voto sintético pra satisfazer FK
            voto = self.sb.upsert_one(
                "votos",
                {
                    "enquete_id": pacote_ctx["enquete_id"],
                    "cliente_id": cliente_id,
                    "qty": qty,
                    "status": "in",
                    "voted_at": _now_iso(),
                },
                on_conflict="enquete_id,cliente_id",
            )
            voto_id = voto["id"]

        pc = self.sb.insert(
            "pacote_clientes",
            {
                "pacote_id": pacote_ctx["pacote_id"],
                "cliente_id": cliente_id,
                "voto_id": voto_id,
                "produto_id": pacote_ctx["produto_id"],
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": fin["subtotal"],
                "commission_percent": 0,
                "commission_amount": fin["commission_amount"],
                "total_amount": fin["total_amount"],
                "status": "closed",
            },
            upsert=True,
            on_conflict="pacote_id,cliente_id",
        )
        pc_id = pc[0]["id"] if isinstance(pc, list) and pc else None

        # Se já existe venda pra esse (pacote, cliente) — pode ter ficado de uma
        # remoção+readição anterior. Se pago, preserva status; senão reativa.
        existing_venda = self.sb.select(
            "vendas",
            columns="id,status",
            filters=[("pacote_id", "eq", pacote_ctx["pacote_id"]), ("cliente_id", "eq", cliente_id)],
            single=True,
        )
        venda_base = {
            "pacote_id": pacote_ctx["pacote_id"],
            "cliente_id": cliente_id,
            "produto_id": pacote_ctx["produto_id"],
            "pacote_cliente_id": pc_id,
            "qty": qty,
            "unit_price": unit_price,
            "subtotal": fin["subtotal"],
            "commission_percent": 0,
            "commission_amount": fin["commission_amount"],
            "total_amount": fin["total_amount"],
        }
        if isinstance(existing_venda, dict):
            prev_status = (existing_venda.get("status") or "").lower()
            update_payload = {**venda_base, "updated_at": _now_iso()}
            # Só muda status se não estava pago (preserva histórico financeiro)
            if prev_status not in {"paid"}:
                update_payload["status"] = "approved"
            venda_rows = self.sb.update(
                "vendas",
                update_payload,
                filters=[("id", "eq", existing_venda["id"])],
            )
            venda = venda_rows[0] if isinstance(venda_rows, list) and venda_rows else {"id": existing_venda["id"]}
        else:
            venda = self.sb.insert(
                "vendas",
                {**venda_base, "status": "approved", "sold_at": _now_iso()},
            )[0]

        # Cria pagamento só se não existir (não sobrescreve pago)
        existing_pag = self.sb.select(
            "pagamentos",
            columns="id,status",
            filters=[("venda_id", "eq", venda["id"])],
            single=True,
        )
        if not isinstance(existing_pag, dict):
            self.sb.insert(
                "pagamentos",
                {
                    "venda_id": venda["id"],
                    "provider": "asaas",
                    "status": "created",
                    "payload_json": {"source": "confirmed_package_edit"},
                },
            )
