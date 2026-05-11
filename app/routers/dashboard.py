"""Router principal do dashboard v2 (raylook.v4smc.com).

Serve a UI em `templates/dashboard_v2.html` + `static/js/dashboard_v2.js`.
Pacotes agrupados em 7 estados do fluxo:

    aberto      — pacotes.status = 'open'
    fechado     — pacotes.status = 'closed' (aguardando aprovação do gerente)
    confirmado  — pacotes.status = 'approved' com cobranças ainda em aberto
                  (gerente aprovou, aguardando clientes pagarem)
    pago        — todos pagaram, aguardando validação
    pendente    — pagamentos validados, aguardando estoque separar
    separado    — pdf_sent_at set, aguardando envio
    enviado     — shipped_at IS NOT NULL

Pacotes cancelled ficam em lista à parte (não entram no fluxo).

Prefixo de URL `/api/mockups/*` mantido por compatibilidade com o frontend
(origem histórica: era usado por protótipos em static/ui-mockups/, hoje já
removidos). Migração do prefixo fica para uma janela própria — exige sync
com o JS.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException

from app.services.supabase_service import SupabaseRestClient


router = APIRouter(prefix="/api/mockups", tags=["dashboard"])


FLOW_STATES = ["aberto", "fechado", "confirmado", "pago", "pendente", "separado", "enviado"]


def _age_str(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


def _derive_client_state(
    pkg: Dict[str, Any],
    pc: Dict[str, Any],
    pagamento: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Estado do cliente individualmente. Olha primeiro os campos do pacote_cliente
    (granularidade fina); se não setados, cai no campo do pacote-pai."""
    if not pagamento:
        return None
    pag_status = (pagamento.get("status") or "").lower()
    if pag_status != "paid":
        return None
    shipped = pc.get("shipped_at") or pkg.get("shipped_at")
    pdf = pc.get("pdf_sent_at") or pkg.get("pdf_sent_at")
    validated = pc.get("payment_validated_at") or pkg.get("payment_validated_at")
    if shipped:
        return "enviado"
    if pdf:
        return "separado"
    if validated:
        return "pendente"
    return "pago"


def _derive_state(pacote: Dict[str, Any], pagamentos: List[Dict[str, Any]]) -> str:
    status = (pacote.get("status") or "").lower()
    if status == "open":
        return "aberto"
    if status == "closed":
        return "fechado"
    if status == "cancelled":
        return "cancelled"
    # approved ou demais
    if pacote.get("shipped_at"):
        return "enviado"
    statuses = [(p.get("status") or "").lower() for p in pagamentos]
    all_paid = bool(statuses) and all(s == "paid" for s in statuses)
    any_pending = any(s in ("created", "sent") for s in statuses)
    if not pagamentos:
        # aprovado mas sem cobrança ainda: considera "confirmado" (aguardando ação)
        return "confirmado"
    if all_paid and pacote.get("pdf_sent_at"):
        return "separado"
    if all_paid and pacote.get("payment_validated_at"):
        # gerente validou os pagamentos → estoque pode separar
        return "pendente"
    if all_paid:
        # todos pagaram, aguardando validação do gerente
        return "pago"
    if any_pending:
        # aprovado com cobranças em aberto
        return "confirmado"
    return "confirmado"


@router.get("/packages")
def list_packages_by_state() -> Dict[str, Any]:
    client = SupabaseRestClient.from_settings()

    # Buscar tudo em uma passada só (DB local, sem preocupação de volume)
    pacotes = client.select("pacotes", order="updated_at.desc") or []
    enquetes = client.select("enquetes") or []
    produtos = client.select("produtos") or []
    pacote_clientes = client.select("pacote_clientes") or []
    vendas = client.select("vendas") or []
    pagamentos = client.select("pagamentos") or []
    clientes = client.select("clientes") or []
    votos = client.select(
        "votos",
        filters=[("status", "neq", "out")],
    ) or []

    enquete_map = {e["id"]: e for e in enquetes}
    produto_map = {p["id"]: p for p in produtos}
    cliente_map = {c["id"]: c for c in clientes}

    vendas_by_pacote: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in vendas:
        vendas_by_pacote[v["pacote_id"]].append(v)

    pagamentos_by_venda: Dict[str, Dict[str, Any]] = {p["venda_id"]: p for p in pagamentos}
    pagamentos_by_pacote: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for venda in vendas:
        p = pagamentos_by_venda.get(venda["id"])
        if p:
            pagamentos_by_pacote[venda["pacote_id"]].append(p)

    pc_by_pacote: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pc in pacote_clientes:
        pc_by_pacote[pc["pacote_id"]].append(pc)

    # Pros pacotes ABERTOS, precisamos saber quais clientes da enquete JÁ foram
    # consumidos por pacotes anteriores (closed/approved) — pra não contar 2×.
    pacote_status_by_id = {p["id"]: (p.get("status") or "") for p in pacotes}
    consumed_customers_by_enquete: Dict[str, set] = defaultdict(set)
    pacote_enquete_by_id = {p["id"]: p.get("enquete_id") for p in pacotes}
    for pc in pacote_clientes:
        status = pacote_status_by_id.get(pc["pacote_id"])
        if status in ("closed", "approved"):
            enq_id = pacote_enquete_by_id.get(pc["pacote_id"])
            if enq_id:
                consumed_customers_by_enquete[enq_id].add(pc["cliente_id"])

    # Votos por enquete (só status 'in') descontando os já consumidos.
    votos_by_enquete: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in votos:
        enq_id = v.get("enquete_id")
        if v.get("cliente_id") in consumed_customers_by_enquete.get(enq_id, set()):
            continue
        votos_by_enquete[enq_id].append(v)

    grouped: Dict[str, List[Dict[str, Any]]] = {s: [] for s in FLOW_STATES}
    cancelled: List[Dict[str, Any]] = []

    for pkg in pacotes:
        pags = pagamentos_by_pacote.get(pkg["id"], [])
        state = _derive_state(pkg, pags)

        enq = enquete_map.get(pkg["enquete_id"], {})
        prod = produto_map.get(enq.get("produto_id"))
        unit_price = float(prod["valor_unitario"]) if prod else None

        # Clientes do pacote (pacote_clientes) com nome resolvido
        pcs = pc_by_pacote.get(pkg["id"], [])
        clientes_out: List[Dict[str, Any]] = []
        for pc in pcs:
            c = cliente_map.get(pc["cliente_id"], {})
            pag = next(
                (p for p in pags if _venda_for_pc(p, vendas_by_pacote.get(pkg["id"], []), pc["id"])),
                None,
            )
            clientes_out.append(
                {
                    "name": c.get("nome"),
                    "phone": c.get("celular"),
                    "qty": pc["qty"],
                    "total_amount": pc["total_amount"],
                    "payment_status": pag["status"] if pag else None,
                }
            )

        # Para abertos: usar votos "in" da enquete (pacote ainda formando)
        if state == "aberto" and not clientes_out:
            for v in votos_by_enquete.get(pkg["enquete_id"], []):
                c = cliente_map.get(v["cliente_id"], {})
                clientes_out.append(
                    {
                        "name": c.get("nome"),
                        "phone": c.get("celular"),
                        "qty": v["qty"],
                        "total_amount": None,
                        "payment_status": None,
                    }
                )

        total_qty = sum(c["qty"] for c in clientes_out) or pkg.get("total_qty") or 0
        total_value = round(sum((c.get("total_amount") or 0.0) for c in clientes_out), 2) or None

        pags_summary = {
            "total": len(pags),
            "paid": sum(1 for p in pags if p.get("status") == "paid"),
            "sent": sum(1 for p in pags if p.get("status") == "sent"),
            "created": sum(1 for p in pags if p.get("status") == "created"),
        }

        # timestamp "do estado atual" = horário da última transição
        state_ts_field = {
            "aberto": "opened_at",
            "fechado": "closed_at",
            "confirmado": "approved_at",          # aprovado mas esperando pagamento
            "pago": "updated_at",                 # último pagamento marcado paid
            "pendente": "payment_validated_at",   # gerente validou
            "separado": "pdf_sent_at",
            "enviado": "shipped_at",
            "cancelled": "cancelled_at",
        }.get(state, "updated_at")

        drive_id = (enq.get("drive_file_id") or (prod.get("drive_file_id") if prod else None))
        item = {
            "id": pkg["id"],
            "state": state,
            "sequence_no": pkg.get("sequence_no"),
            "enquete_id": pkg.get("enquete_id"),
            "enquete_title": enq.get("titulo"),
            "external_poll_id": enq.get("external_poll_id"),
            "produto_name": prod.get("nome") if prod else None,
            "image": f"/files/{drive_id}" if drive_id else None,
            "unit_price": unit_price,
            "capacidade_total": pkg.get("capacidade_total") or 24,
            "total_qty": total_qty,
            "participants_count": len(clientes_out),
            "clientes": clientes_out,
            "total_value": total_value,
            "pagamentos": pags_summary,
            "pdf_sent_at": pkg.get("pdf_sent_at"),
            "shipped_at": pkg.get("shipped_at"),
            "state_since": pkg.get(state_ts_field) or pkg.get("updated_at"),
            "age": _age_str(pkg.get(state_ts_field) or pkg.get("updated_at")),
            "created_at": pkg.get("created_at"),
        }

        if state == "cancelled":
            cancelled.append(item)
        else:
            grouped[state].append(item)

    # Granularidade por cliente a partir de "pago": cada pacote_cliente vira
    # uma linha na fase correspondente. "pago" pode incluir clientes de
    # pacotes ainda em "confirmado" (1 cliente pagou, outros não).
    clients_grouped: Dict[str, List[Dict[str, Any]]] = {
        "pago": [], "pendente": [], "separado": [], "enviado": []
    }
    for pkg in pacotes:
        pags = pagamentos_by_pacote.get(pkg["id"], [])
        pkg_state = _derive_state(pkg, pags)
        if pkg_state in ("aberto", "fechado", "cancelled"):
            continue
        enq = enquete_map.get(pkg["enquete_id"], {})
        prod = produto_map.get(enq.get("produto_id"))
        drive_id = enq.get("drive_file_id") or (prod.get("drive_file_id") if prod else None)
        image = f"/files/{drive_id}" if drive_id else None
        produto_name = prod.get("nome") if prod else None

        for pc in pc_by_pacote.get(pkg["id"], []):
            c = cliente_map.get(pc["cliente_id"], {})
            venda = next(
                (v for v in vendas_by_pacote.get(pkg["id"], [])
                 if v.get("pacote_cliente_id") == pc["id"]),
                None,
            )
            pag = pagamentos_by_venda.get(venda["id"]) if venda else None
            pag_status = pag.get("status") if pag else None

            cli_state = _derive_client_state(pkg, pc, pag)
            if not cli_state:
                continue

            cli_state_ts = (
                pag.get("paid_at") if cli_state == "pago" and pag else
                (pc.get("payment_validated_at") or pkg.get("payment_validated_at")) if cli_state == "pendente" else
                (pc.get("pdf_sent_at") or pkg.get("pdf_sent_at")) if cli_state == "separado" else
                (pc.get("shipped_at") or pkg.get("shipped_at")) if cli_state == "enviado" else
                pkg.get("updated_at")
            )

            clients_grouped[cli_state].append({
                "cliente_id": pc["cliente_id"],
                "nome": c.get("nome"),
                "celular": c.get("celular"),
                "qty": pc["qty"],
                "valor": pc.get("total_amount"),
                "pagamento_status": pag_status,
                "paid_at": pag.get("paid_at") if pag else None,
                "pacote_id": pkg["id"],
                "pacote_state": pkg_state,
                "pacote_sequence_no": pkg.get("sequence_no"),
                "produto_name": produto_name,
                "image": image,
                "external_poll_id": enq.get("external_poll_id"),
                "state_since": cli_state_ts,
            })

    counts = {s: len(grouped[s]) for s in FLOW_STATES}
    # Counts dos estados per-cliente vêm do agrupamento de clientes
    for s in ("pago", "pendente", "separado", "enviado"):
        counts[s] = len(clients_grouped[s])
    counts["cancelled"] = len(cancelled)

    return {
        "states": FLOW_STATES,
        "counts": counts,
        "packages_by_state": grouped,
        "clients_by_state": clients_grouped,
        "cancelled": cancelled,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _venda_for_pc(pagamento: Dict[str, Any], vendas: List[Dict[str, Any]], pc_id: str) -> bool:
    """True se o pagamento pertence à venda associada a esse pacote_cliente."""
    for v in vendas:
        if v.get("pacote_cliente_id") == pc_id and v.get("id") == pagamento.get("venda_id"):
            return True
    return False


# ---------------------------------------------------------------------------
# Drill-down de um pacote (feature #3)
# ---------------------------------------------------------------------------

@router.get("/packages/{pacote_id}")
def get_package_detail(pacote_id: str) -> Dict[str, Any]:
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")

    enq = {}
    if pkg.get("enquete_id"):
        enq = client.select("enquetes", filters=[("id", "eq", pkg["enquete_id"])], single=True) or {}
    prod = {}
    if enq.get("produto_id"):
        prod = client.select("produtos", filters=[("id", "eq", enq["produto_id"])], single=True) or {}

    pcs = client.select("pacote_clientes", filters=[("pacote_id", "eq", pacote_id)]) or []
    cliente_ids = list({pc["cliente_id"] for pc in pcs})
    clientes = client.select("clientes", filters=[("id", "in", cliente_ids)]) if cliente_ids else []
    cliente_map = {c["id"]: c for c in clientes}

    vendas_all = client.select("vendas", filters=[("pacote_id", "eq", pacote_id)]) or []
    venda_by_pc = {v["pacote_cliente_id"]: v for v in vendas_all if v.get("pacote_cliente_id")}
    venda_ids = [v["id"] for v in vendas_all]
    pags = client.select("pagamentos", filters=[("venda_id", "in", venda_ids)]) if venda_ids else []
    pag_by_venda = {p["venda_id"]: p for p in pags}

    clientes_detail = []
    for pc in pcs:
        c = cliente_map.get(pc["cliente_id"], {})
        venda = venda_by_pc.get(pc["id"])
        pag = pag_by_venda.get(venda["id"]) if venda else None
        clientes_detail.append({
            "cliente_id": pc["cliente_id"],
            "nome": c.get("nome"),
            "celular": c.get("celular"),
            "qty": pc["qty"],
            "subtotal": pc["subtotal"],
            "total_amount": pc["total_amount"],
            "venda_status": venda.get("status") if venda else None,
            "pagamento_status": pag.get("status") if pag else None,
            "payment_link": pag.get("payment_link") if pag else None,
            "paid_at": pag.get("paid_at") if pag else None,
            "is_voter_only": False,
        })

    # Fallback: se não há pacote_clientes (pacote aberto, ou fechado via /advance),
    # pega os votos status='in' da enquete como voters atuais. São "candidatos",
    # não vendas. Marcados com is_voter_only=True pra a UI diferenciar.
    if not clientes_detail and pkg.get("enquete_id"):
        votos_in = client.select(
            "votos",
            filters=[
                ("enquete_id", "eq", pkg["enquete_id"]),
                ("status", "neq", "out"),
            ],
            order="voted_at.asc",
        ) or []
        # Excluir clientes já consumidos por outros pacotes closed/approved da mesma enquete
        other_pacotes = client.select(
            "pacotes",
            filters=[
                ("enquete_id", "eq", pkg["enquete_id"]),
                ("id", "neq", pacote_id),
            ],
        ) or []
        active_ids = [p["id"] for p in other_pacotes
                      if (p.get("status") or "") in ("closed", "approved")]
        consumed_ids: set = set()
        if active_ids:
            consumed_pcs = client.select(
                "pacote_clientes",
                filters=[("pacote_id", "in", active_ids)],
            ) or []
            consumed_ids = {pc["cliente_id"] for pc in consumed_pcs}
        votos_in = [v for v in votos_in if v.get("cliente_id") not in consumed_ids]

        voter_ids = list({v["cliente_id"] for v in votos_in if v.get("cliente_id")})
        voters = client.select("clientes", filters=[("id", "in", voter_ids)]) if voter_ids else []
        voter_map = {c["id"]: c for c in voters}
        unit_price = float(prod.get("valor_unitario") or 0) if prod else 0.0
        for v in votos_in:
            c = voter_map.get(v["cliente_id"], {})
            qty = int(v.get("qty") or 0)
            subtotal = round(unit_price * qty, 2)
            total = round(subtotal * 1.13, 2)  # 13% comissão (mesma regra do seed)
            clientes_detail.append({
                "cliente_id": v.get("cliente_id"),
                "nome": c.get("nome"),
                "celular": c.get("celular"),
                "qty": qty,
                "subtotal": subtotal,
                "total_amount": total,
                "venda_status": None,
                "pagamento_status": None,
                "payment_link": None,
                "paid_at": None,
                "is_voter_only": True,
            })

    state = _derive_state(pkg, pags)

    # Timeline de transições (a partir dos timestamps existentes)
    timeline: List[Dict[str, Any]] = []

    def add(state_name: str, at: Optional[str], note: str) -> None:
        if at:
            timeline.append({"state": state_name, "at": at, "note": note})

    add("aberto", pkg.get("opened_at") or pkg.get("created_at"), "pacote iniciado")
    add("fechado", pkg.get("closed_at"), f"atingiu capacidade ({pkg.get('capacidade_total') or 24} peças)")
    add("confirmado", pkg.get("approved_at"),
        f"aprovado por {pkg.get('confirmed_by') or 'gerente'} — cobranças criadas")
    # "pendente" = todos pagaram. Sem timestamp próprio: usamos a data do último
    # pagamento (paid_at mais recente).
    last_paid_at: Optional[str] = None
    if pags:
        paid_times = [p.get("paid_at") for p in pags if p.get("paid_at")]
        if paid_times and all(p.get("status") == "paid" for p in pags):
            last_paid_at = max(paid_times)
    add("pendente", last_paid_at, "todos os pagamentos realizados")
    add("separado", pkg.get("pdf_sent_at"), "PDF de etiqueta enviado ao estoque")
    add("enviado", pkg.get("shipped_at"),
        f"despachado por {pkg.get('shipped_by') or 'operador'}")
    add("cancelled", pkg.get("cancelled_at"),
        f"cancelado por {pkg.get('cancelled_by') or 'gerente'}")

    timeline.sort(key=lambda t: t["at"])

    return {
        "id": pkg["id"],
        "state": state,
        "sequence_no": pkg.get("sequence_no"),
        "capacidade_total": pkg.get("capacidade_total") or 24,
        "total_qty": pkg.get("total_qty") or 0,
        "produto": {k: prod.get(k) for k in ("nome", "descricao", "tamanho", "valor_unitario")} if prod else None,
        "enquete": {
            "id": enq.get("id"),
            "external_poll_id": enq.get("external_poll_id"),
            "titulo": enq.get("titulo"),
            "chat_id": enq.get("chat_id"),
            "status": enq.get("status"),
        } if enq else None,
        "clientes": clientes_detail,
        "timeline": timeline,
        "pdf_file_name": pkg.get("pdf_file_name"),
    }


# ---------------------------------------------------------------------------
# Ações (features #1 e #5 — advance usa fluxo linear)
# ---------------------------------------------------------------------------

def _load_pkg_and_pags(client: SupabaseRestClient, pacote_id: str):
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    vendas = client.select("vendas", filters=[("pacote_id", "eq", pacote_id)]) or []
    pags: List[Dict[str, Any]] = []
    if vendas:
        venda_ids = [v["id"] for v in vendas]
        pags = client.select("pagamentos", filters=[("venda_id", "in", venda_ids)]) or []
    return pkg, vendas, pags


@router.post("/packages/{pacote_id}/advance")
def advance_package(pacote_id: str, to: Optional[str] = None) -> Dict[str, Any]:
    """Avança o pacote para o próximo estado do fluxo linear (feature #5).

    aberto → fechado → confirmado → pago → pendente → separado → enviado.
    Cria vendas/pagamentos quando necessário. Em dev, simula transições
    sem tocar em APIs externas.

    Aceita query param `to=<estado>` pra pular várias etapas de uma vez
    (ex: `?to=separado` em pacote \"pago\" valida + gera pdf de uma vez).
    """
    client = SupabaseRestClient.from_settings()
    pkg, vendas, pags = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags)
    now = client.now_iso()

    if to:
        if to not in FLOW_STATES:
            raise HTTPException(400, f"Estado inválido: {to}")
        if state not in FLOW_STATES:
            raise HTTPException(400, f"Estado atual {state} não suporta pular pra {to}")
        target_idx = FLOW_STATES.index(to)
        cur_idx = FLOW_STATES.index(state)
        if target_idx <= cur_idx:
            raise HTTPException(400, f"Pacote já está em \"{state}\" — não pode pular pra trás")
        previous = state
        steps = 0
        while cur_idx < target_idx and steps < len(FLOW_STATES):
            advance_package(pacote_id, to=None)  # avança 1 step
            pkg, vendas, pags = _load_pkg_and_pags(client, pacote_id)
            state = _derive_state(pkg, pags)
            if state not in FLOW_STATES:
                break
            cur_idx = FLOW_STATES.index(state)
            steps += 1
        return {"status": "ok", "previous": previous, "new_state": state, "steps": steps}

    if state == "aberto":
        client.update("pacotes",
                      {"status": "closed", "closed_at": now},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "aberto", "new_state": "fechado"}

    if state == "fechado":
        # Aprova o pacote + cria vendas/pagamentos pros pacote_clientes existentes
        client.update("pacotes",
                      {"status": "approved", "approved_at": now, "confirmed_by": "simulated@dev"},
                      filters=[("id", "eq", pacote_id)])
        pcs = client.select("pacote_clientes", filters=[("pacote_id", "eq", pacote_id)]) or []
        produto_id = None
        if pkg.get("enquete_id"):
            enq = client.select("enquetes",
                                filters=[("id", "eq", pkg["enquete_id"])],
                                single=True) or {}
            produto_id = enq.get("produto_id")
        for pc in pcs:
            venda_row = client.insert("vendas", {
                "pacote_id": pacote_id,
                "cliente_id": pc["cliente_id"],
                "produto_id": produto_id or pc.get("produto_id"),
                "pacote_cliente_id": pc["id"],
                "qty": pc["qty"],
                "unit_price": pc["unit_price"],
                "subtotal": pc["subtotal"],
                "commission_percent": pc["commission_percent"],
                "commission_amount": pc["commission_amount"],
                "total_amount": pc["total_amount"],
                "status": "approved",
                "sold_at": now,
            })
            venda = venda_row[0] if isinstance(venda_row, list) else venda_row
            client.insert("pagamentos", {
                "venda_id": venda["id"],
                "provider": "asaas",
                "provider_customer_id": f"cus_sim_{venda['id'][:8]}",
                "payment_link": f"https://exemplo.dev/pay/{venda['id']}",
                "status": "created",
                "payload_json": {"simulated": True},
            })
        return {"status": "ok", "previous": "fechado", "new_state": "confirmado"}

    if state == "confirmado":
        # Marca TODOS os pagamentos como pagos → vira "pago" (aguardando gerente
        # validar antes de liberar pra estoque).
        for p in pags:
            if (p.get("status") or "") != "paid":
                client.update("pagamentos",
                              {"status": "paid", "paid_at": now},
                              filters=[("id", "eq", p["id"])])
        return {"status": "ok", "previous": "confirmado", "new_state": "pago"}

    if state == "pago":
        # Gerente valida os pagamentos → libera pra estoque separar.
        client.update("pacotes",
                      {"payment_validated_at": now},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "pago", "new_state": "pendente"}

    if state == "pendente":
        # Todos já pagos — só precisa gerar/enviar a etiqueta de separação.
        client.update("pacotes", {
            "pdf_sent_at": now,
            "pdf_status": "sent",
            "pdf_file_name": f"etiqueta-{pkg.get('sequence_no') or 'n'}.pdf",
        }, filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "pendente", "new_state": "separado"}

    if state == "separado":
        client.update("pacotes",
                      {"shipped_at": now, "shipped_by": "simulated@dev"},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "separado", "new_state": "enviado"}

    if state == "enviado":
        raise HTTPException(400, "Pacote já está no estado final (enviado)")
    if state == "cancelled":
        raise HTTPException(400, "Pacote cancelado não pode avançar")
    raise HTTPException(400, f"Estado desconhecido: {state}")


@router.post("/packages/{pacote_id}/regress")
def regress_package(pacote_id: str) -> Dict[str, Any]:
    """Reverte o pacote pro estado anterior do fluxo. Operação só válida em
    sandbox — desfaz timestamps e, no caso confirmado→fechado, apaga as
    vendas/pagamentos criados na confirmação.
    """
    client = SupabaseRestClient.from_settings()
    pkg, vendas, pags = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags)
    now = client.now_iso()

    if state == "aberto":
        raise HTTPException(400, "Pacote em \"aberto\" não tem estado anterior")
    if state == "cancelled":
        raise HTTPException(400, "Cancelado não pode ser regredido — use uma nova enquete")
    if state == "fechado":
        raise HTTPException(400, "Fechado não pode voltar pra aberto — capacidade já atingida")

    if state == "confirmado":
        # Desfaz a confirmação: apaga vendas e pagamentos criados, devolve pra fechado.
        for venda in vendas:
            client.delete("pagamentos", filters=[("venda_id", "eq", venda["id"])])
        client.delete("vendas", filters=[("pacote_id", "eq", pacote_id)])
        client.update("pacotes",
                      {"status": "closed", "approved_at": None, "confirmed_by": None},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "confirmado", "new_state": "fechado"}

    if state == "pago":
        # Reverte pagamentos paid → sent (cliente "voltou a dever").
        for p in pags:
            if (p.get("status") or "") == "paid":
                client.update("pagamentos",
                              {"status": "sent", "paid_at": None, "updated_at": now},
                              filters=[("id", "eq", p["id"])])
        return {"status": "ok", "previous": "pago", "new_state": "confirmado"}

    if state == "pendente":
        client.update("pacotes",
                      {"payment_validated_at": None},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "pendente", "new_state": "pago"}

    if state == "separado":
        client.update("pacotes",
                      {"pdf_sent_at": None, "pdf_status": None, "pdf_file_name": None},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "separado", "new_state": "pendente"}

    if state == "enviado":
        client.update("pacotes",
                      {"shipped_at": None, "shipped_by": None},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "enviado", "new_state": "separado"}

    raise HTTPException(400, f"Estado desconhecido: {state}")


@router.post("/packages/{pacote_id}/cancel")
def cancel_package(pacote_id: str) -> Dict[str, Any]:
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    if (pkg.get("status") or "") == "cancelled":
        raise HTTPException(400, "Pacote já está cancelado")
    now = client.now_iso()
    client.update("pacotes", {
        "status": "cancelled",
        "cancelled_at": now,
        "cancelled_by": "simulated@dev",
    }, filters=[("id", "eq", pacote_id)])
    return {"status": "ok", "new_state": "cancelled"}


@router.post("/packages/{pacote_id}/restore")
def restore_package(pacote_id: str) -> Dict[str, Any]:
    """Restaura um pacote cancelado para 'fechado' (status=closed)."""
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    if (pkg.get("status") or "") != "cancelled":
        raise HTTPException(400, "Pacote não está cancelado")
    client.update("pacotes", {
        "status": "closed",
        "cancelled_at": None,
        "cancelled_by": None,
    }, filters=[("id", "eq", pacote_id)])
    return {"status": "ok", "new_state": "fechado"}


_CLIENT_FLOW = ["pago", "pendente", "separado", "enviado"]


@router.post("/packages/{pacote_id}/clients/{cliente_id}/advance")
def advance_client(pacote_id: str, cliente_id: str, to: Optional[str] = None) -> Dict[str, Any]:
    """Avança UM cliente individualmente nas fases finais (pago→pendente→separado→enviado).
    Aceita ?to=<estado> pra pular várias etapas. Não toca em outros clientes do pacote."""
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        raise HTTPException(404, "Cliente não está no pacote")
    venda = client.select("vendas", filters=[("pacote_cliente_id", "eq", pc["id"])], single=True)
    pag = client.select("pagamentos", filters=[("venda_id", "eq", venda["id"])], single=True) if venda else None

    state = _derive_client_state(pkg, pc, pag)
    if not state:
        raise HTTPException(400, "Cliente ainda não pagou — marque como pago primeiro")
    if state not in _CLIENT_FLOW:
        raise HTTPException(400, f"Estado inválido: {state}")

    cur_idx = _CLIENT_FLOW.index(state)
    if to is None:
        target_idx = cur_idx + 1
    else:
        if to not in _CLIENT_FLOW:
            raise HTTPException(400, f"Destino inválido: {to}")
        target_idx = _CLIENT_FLOW.index(to)
    if target_idx <= cur_idx:
        raise HTTPException(400, f"Cliente já está em \"{state}\" — não pode pular pra trás")
    if target_idx >= len(_CLIENT_FLOW):
        raise HTTPException(400, "Cliente já está no estado final")

    now = client.now_iso()
    update_payload: Dict[str, Any] = {}
    # Marca todos os timestamps até o target inclusive (idempotente nos já setados).
    if target_idx >= 1 and not pc.get("payment_validated_at"):
        update_payload["payment_validated_at"] = now
    if target_idx >= 2 and not pc.get("pdf_sent_at"):
        update_payload["pdf_sent_at"] = now
    if target_idx >= 3 and not pc.get("shipped_at"):
        update_payload["shipped_at"] = now
    if update_payload:
        client.update("pacote_clientes", update_payload, filters=[("id", "eq", pc["id"])])

    return {
        "status": "ok",
        "previous": state,
        "new_state": _CLIENT_FLOW[target_idx],
        "cliente_id": cliente_id,
    }


@router.post("/packages/{pacote_id}/clients/{cliente_id}/mark-paid")
def mark_client_paid(pacote_id: str, cliente_id: str) -> Dict[str, Any]:
    """Marca o pagamento de UM cliente como paid. Granularidade fina:
    em \"confirmado\" cada cliente paga independente; quando o último vira
    paid, _derive_state coloca o pacote em \"pago\" automaticamente."""
    client = SupabaseRestClient.from_settings()
    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        raise HTTPException(404, "Cliente não está no pacote")
    venda = client.select(
        "vendas", filters=[("pacote_cliente_id", "eq", pc["id"])], single=True,
    )
    if not venda:
        raise HTTPException(404, "Venda não encontrada — confirmação ainda não foi feita")
    pag = client.select(
        "pagamentos", filters=[("venda_id", "eq", venda["id"])], single=True,
    )
    if not pag:
        raise HTTPException(404, "Pagamento não encontrado")
    if (pag.get("status") or "") == "paid":
        raise HTTPException(400, "Pagamento já está marcado como pago")
    now = client.now_iso()
    client.update("pagamentos",
                  {"status": "paid", "paid_at": now},
                  filters=[("id", "eq", pag["id"])])
    return {"status": "ok", "action": "client_marked_paid", "cliente_id": cliente_id}


@router.post("/packages/{pacote_id}/resend-pix")
def resend_pix(pacote_id: str) -> Dict[str, Any]:
    client = SupabaseRestClient.from_settings()
    pkg, vendas, pags = _load_pkg_and_pags(client, pacote_id)
    pendentes = [p for p in pags if (p.get("status") or "") in ("created", "sent")]
    now = client.now_iso()
    for p in pendentes:
        client.update("pagamentos", {
            "status": "sent",
            "updated_at": now,
        }, filters=[("id", "eq", p["id"])])
    return {"status": "ok", "reminded": len(pendentes)}


# ---------------------------------------------------------------------------
# CRUD de clientes no pacote (feature editar modal)
# ---------------------------------------------------------------------------

@router.get("/clientes")
def list_clientes(q: Optional[str] = None, exclude_pacote: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna clientes pro seletor do modal. Filtra por query e opcionalmente
    exclui os já presentes em um pacote (útil pra add/swap)."""
    client = SupabaseRestClient.from_settings()
    rows = client.select("clientes", order="nome.asc") or []
    if q:
        ql = q.lower()
        rows = [c for c in rows
                if ql in (c.get("nome") or "").lower()
                or ql in (c.get("celular") or "")]
    excluded: set = set()
    if exclude_pacote:
        # tanto pacote_clientes quanto votos da enquete associada
        pkg = client.select("pacotes", filters=[("id", "eq", exclude_pacote)], single=True) or {}
        pcs = client.select("pacote_clientes", filters=[("pacote_id", "eq", exclude_pacote)]) or []
        excluded = {pc["cliente_id"] for pc in pcs}
        if pkg.get("enquete_id") and (pkg.get("status") or "") == "open":
            # Pra pacotes abertos, exclui também quem tem voto 'in' na enquete
            votos_in = client.select(
                "votos",
                filters=[
                    ("enquete_id", "eq", pkg["enquete_id"]),
                    ("status", "neq", "out"),
                ],
            ) or []
            excluded |= {v["cliente_id"] for v in votos_in}
    return [
        {"id": c["id"], "nome": c.get("nome"), "celular": c.get("celular")}
        for c in rows if c["id"] not in excluded
    ]


@router.post("/packages/{pacote_id}/clients")
def add_client_to_package(pacote_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Adiciona cliente. Aberto → cria voto status='in'. Fechado/confirmado →
    cria pacote_cliente (desbalanceia capacidade; aceito em dev)."""
    cliente_id = body.get("cliente_id")
    qty = int(body.get("qty") or 3)
    if not cliente_id:
        raise HTTPException(400, "cliente_id é obrigatório")
    if qty not in (3, 6, 9, 12):
        raise HTTPException(400, "qty deve ser 3, 6, 9 ou 12")

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")

    pags = _load_pkg_and_pags(client, pacote_id)[2]
    state = _derive_state(pkg, pags)
    now = client.now_iso()

    cli = client.select("clientes", filters=[("id", "eq", cliente_id)], single=True)
    if not cli:
        raise HTTPException(404, "Cliente não encontrado")

    if state == "aberto":
        # Busca alternativa correspondente à qty
        alt = client.select(
            "enquete_alternativas",
            filters=[("enquete_id", "eq", pkg["enquete_id"]), ("qty", "eq", qty)],
            single=True,
        )
        # Pode já existir voto (unique enquete_id+cliente_id); tenta upsert via check
        existing = client.select(
            "votos",
            filters=[("enquete_id", "eq", pkg["enquete_id"]), ("cliente_id", "eq", cliente_id)],
            single=True,
        )
        if existing:
            client.update("votos", {
                "status": "in", "qty": qty,
                "alternativa_id": alt["id"] if alt else None,
            }, filters=[("id", "eq", existing["id"])])
            voto_id = existing["id"]
        else:
            voto_row = client.insert("votos", {
                "enquete_id": pkg["enquete_id"],
                "cliente_id": cliente_id,
                "alternativa_id": alt["id"] if alt else None,
                "qty": qty,
                "status": "in",
                "voted_at": now,
            })
            voto = voto_row[0] if isinstance(voto_row, list) else voto_row
            voto_id = voto["id"]
        # Atualiza contadores do pacote
        client.update("pacotes", {
            "total_qty": (pkg.get("total_qty") or 0) + qty,
            "participants_count": (pkg.get("participants_count") or 0) + 1,
        }, filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "action": "voto_added", "voto_id": voto_id}

    raise HTTPException(400, f"Adicionar cliente só é suportado em pacotes abertos (estado atual: {state})")


@router.delete("/packages/{pacote_id}/clients/{cliente_id}")
def remove_client_from_package(pacote_id: str, cliente_id: str) -> Dict[str, Any]:
    """Remove cliente do pacote. Aberto → marca voto como 'out'. Fechado →
    deleta pacote_cliente (desbalanceia, aceito em dev)."""
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")

    pags = _load_pkg_and_pags(client, pacote_id)[2]
    state = _derive_state(pkg, pags)
    now = client.now_iso()

    if state == "aberto":
        voto = client.select(
            "votos",
            filters=[("enquete_id", "eq", pkg["enquete_id"]), ("cliente_id", "eq", cliente_id)],
            single=True,
        )
        if not voto:
            raise HTTPException(404, "Voto não encontrado pra esse cliente")
        qty_removed = int(voto.get("qty") or 0)
        client.update("votos", {"status": "out"}, filters=[("id", "eq", voto["id"])])
        client.update("pacotes", {
            "total_qty": max((pkg.get("total_qty") or 0) - qty_removed, 0),
            "participants_count": max((pkg.get("participants_count") or 0) - 1, 0),
        }, filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "action": "voto_removed"}

    # Estados com pacote_clientes (fechado/approved/etc)
    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        raise HTTPException(404, "Cliente não está no pacote")

    # Também apaga venda e pagamento se existirem (pacotes já aprovados)
    vendas = client.select(
        "vendas",
        filters=[("pacote_cliente_id", "eq", pc["id"])],
    ) or []
    for v in vendas:
        client.delete("pagamentos", filters=[("venda_id", "eq", v["id"])])
        client.delete("vendas", filters=[("id", "eq", v["id"])])

    client.delete("pacote_clientes", filters=[("id", "eq", pc["id"])])
    client.update("pacotes", {
        "total_qty": max((pkg.get("total_qty") or 0) - int(pc.get("qty") or 0), 0),
        "participants_count": max((pkg.get("participants_count") or 0) - 1, 0),
    }, filters=[("id", "eq", pacote_id)])
    return {"status": "ok", "action": "pacote_cliente_removed"}


def _swap_eligible_voters(client: SupabaseRestClient, pacote_id: str, cliente_id: str) -> List[Dict[str, Any]]:
    """Lista clientes elegíveis pra substituir alguém num pacote: precisam ter
    voto status='in'/'wait' na MESMA enquete, com a MESMA qty do voto saindo,
    não estar já no pacote, e não terem sido consumidos por outro pacote dessa
    enquete (closed/approved/etc)."""
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True) or {}
    pc_atual = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc_atual:
        return []
    qty_alvo = int(pc_atual.get("qty") or 0)
    enquete_id = pkg.get("enquete_id")
    if not enquete_id or qty_alvo <= 0:
        return []

    # Clientes já consumidos por pacotes não-cancelados da mesma enquete
    pcs_enq = client.select("pacote_clientes") or []
    pacotes_enq = {p["id"]: p for p in client.select("pacotes",
        filters=[("enquete_id", "eq", enquete_id)]) or []}
    consumed: Set[str] = set()
    for pc in pcs_enq:
        pkt = pacotes_enq.get(pc["pacote_id"])
        if not pkt:
            continue
        status = (pkt.get("status") or "").lower()
        if status == "cancelled":
            continue
        if pc["pacote_id"] == pacote_id and pc["cliente_id"] == cliente_id:
            # o que está saindo não bloqueia
            continue
        consumed.add(pc["cliente_id"])

    votos = client.select(
        "votos",
        filters=[
            ("enquete_id", "eq", enquete_id),
            ("qty", "eq", qty_alvo),
            ("status", "neq", "out"),
        ],
    ) or []
    candidate_ids = {v["cliente_id"] for v in votos
                     if v["cliente_id"] != cliente_id and v["cliente_id"] not in consumed}
    if not candidate_ids:
        return []
    rows = client.select("clientes",
                         filters=[("id", "in", list(candidate_ids))],
                         order="nome.asc") or []
    return [{"id": c["id"], "nome": c.get("nome"), "celular": c.get("celular"), "qty": qty_alvo}
            for c in rows]


@router.get("/packages/{pacote_id}/swap-candidates/{cliente_id}")
def list_swap_candidates(pacote_id: str, cliente_id: str) -> List[Dict[str, Any]]:
    """Quem pode substituir o cliente atual: precisa ter votado na mesma enquete
    com mesma qty e não estar já consumido."""
    client = SupabaseRestClient.from_settings()
    return _swap_eligible_voters(client, pacote_id, cliente_id)


@router.patch("/packages/{pacote_id}/clients/{cliente_id}")
def swap_client_in_package(pacote_id: str, cliente_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Troca o cliente no pacote por outro que tenha votado na mesma enquete."""
    new_cliente_id = body.get("new_cliente_id")
    if not new_cliente_id:
        raise HTTPException(400, "new_cliente_id é obrigatório")
    if new_cliente_id == cliente_id:
        raise HTTPException(400, "novo cliente igual ao atual")

    eligible = {c["id"] for c in _swap_eligible_voters(
        SupabaseRestClient.from_settings(), pacote_id, cliente_id)}
    if new_cliente_id not in eligible:
        raise HTTPException(400, "Substituto precisa ter votado na mesma enquete com a mesma quantidade")

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")

    new_cli = client.select("clientes", filters=[("id", "eq", new_cliente_id)], single=True)
    if not new_cli:
        raise HTTPException(404, "Novo cliente não encontrado")

    pc = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc:
        raise HTTPException(404, "Cliente não está no pacote")

    # Evita colisão: se o novo cliente já está no pacote, bloqueia
    dupe = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", new_cliente_id)],
        single=True,
    )
    if dupe:
        raise HTTPException(400, "Novo cliente já está no pacote")

    client.update("pacote_clientes",
                  {"cliente_id": new_cliente_id},
                  filters=[("id", "eq", pc["id"])])
    # Propaga o swap pras vendas relacionadas (mantém consistência)
    vendas = client.select(
        "vendas",
        filters=[("pacote_cliente_id", "eq", pc["id"])],
    ) or []
    for v in vendas:
        client.update("vendas",
                      {"cliente_id": new_cliente_id},
                      filters=[("id", "eq", v["id"])])
    return {"status": "ok", "action": "swapped",
            "from_cliente_id": cliente_id, "to_cliente_id": new_cliente_id}
