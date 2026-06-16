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

Prefixo: `/api/dashboard/*`. URL antiga (`/api/mockups/*`) vinha dos
protótipos iniciais e foi migrada quando o router virou produção.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

from app.config import settings
from app.services import auth_service as _auth
import asyncio

from fastapi.responses import JSONResponse
from app.services import credit_service
from app.services.supabase_service import SupabaseRestClient
from app.services.whatsapp_domain_service import ALLOWED_QTY


def _role_from(request: Request) -> str:
    return getattr(request.state, "role", None) or "admin"


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


FLOW_STATES = ["aberto", "fechado", "confirmado", "pago", "pendente", "separado", "enviado"]

# Brasília é UTC-3 fixo desde 2019 (sem horário de verão). Filtros do dashboard
# interpretam YYYY-MM-DD vindo do front nesse fuso.
_BR_TZ = timezone(timedelta(hours=-3))


def _parse_date_range(
    since: Optional[str], until: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Converte YYYY-MM-DD (BRT) em ISO UTC pros limites do filtro."""
    since_iso: Optional[str] = None
    until_iso: Optional[str] = None
    if since:
        try:
            d = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=_BR_TZ)
        except ValueError:
            raise HTTPException(400, "since inválido (use YYYY-MM-DD)")
        since_iso = d.astimezone(timezone.utc).isoformat()
    if until:
        try:
            d = datetime.strptime(until, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999000, tzinfo=_BR_TZ
            )
        except ValueError:
            raise HTTPException(400, "until inválido (use YYYY-MM-DD)")
        until_iso = d.astimezone(timezone.utc).isoformat()
    if since_iso and until_iso and since_iso > until_iso:
        raise HTTPException(400, "since não pode ser maior que until")
    return since_iso, until_iso


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


# Marcadores que delimitam o nome do produto no título cru das enquetes do
# WhatsApp: emojis tipicamente usados como bullet OU asterisco seguido de
# palavra-chave conhecida (VALOR, TECIDO, TAMANHO, CATEGORIA, ASSESSORIA,
# COR, CORES, MARCA, MODELO, OBS). Captura é gulosa até qualquer um deles.
_PRODUCT_NAME_STOP = re.compile(
    r"(?=\s*(?:\*\s*(?:VALOR|TECIDO|TAMANHOS?|CATEGORIA|ASSESSORIA|"
    r"COR(?:ES)?|MARCA|MODELO|OBS|PRE[ÇC]O)\s*=|"
    r"[💰🔖📏📍🏷️🎨🛒🛍️📦📌]))",
    re.IGNORECASE,
)
_REF_PREFIX = re.compile(r"^.*?\*?\s*REF\s*=\s*\*?\s*", re.IGNORECASE | re.DOTALL)


def _clean_product_name(raw: Optional[str]) -> Optional[str]:
    """Extrai o nome do produto do título cru da enquete.

    Os títulos do WhatsApp seguem o padrão:
        ➡️ *REF=* CAMISA + TOP 💰 *VALOR=$* 31 🔖 *TECIDO=* LINHO ...

    Devolve só o texto entre REF= e o próximo marcador (emoji/keyword).
    Se não houver REF=, retorna o título limpando emojis e asteriscos.
    """
    if not raw:
        return raw
    s = str(raw).strip()
    if not s:
        return s
    # Tem "REF=" explícito? Tira tudo antes e corta no próximo marcador.
    m = _REF_PREFIX.match(s)
    if m:
        rest = s[m.end():]
        stop = _PRODUCT_NAME_STOP.search(rest)
        nome = (rest[:stop.start()] if stop else rest).strip()
    else:
        nome = s
    # Limpa asteriscos, emojis-bullet e espaços duplicados sobrantes.
    nome = re.sub(r"[*_]", "", nome)
    nome = re.sub(r"^[\s➡️📌📦🛒🛍️]+", "", nome)
    nome = re.sub(r"\s+", " ", nome).strip(" -—:")
    return nome or raw


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


def _derive_state(
    pacote: Dict[str, Any],
    pagamentos: List[Dict[str, Any]],
    pacote_clientes: Optional[List[Dict[str, Any]]] = None,
) -> str:
    status = (pacote.get("status") or "").lower()
    if status == "open":
        return "aberto"
    if status == "closed":
        return "fechado"
    if status == "cancelled":
        return "cancelled"
    # approved ou demais. Estado 'enviado' agora é por cliente:
    # pkg vira 'enviado' só quando TODOS pacote_clientes têm shipped_at.
    # Se há pc.shipped_at parcial, pkg fica em 'separado' até o último sair.
    # Fallback: pkg.shipped_at sem pacote_clientes (legado/backfill) ainda vale.
    pcs = pacote_clientes or []
    if pcs:
        shipped_count = sum(1 for pc in pcs if pc.get("shipped_at"))
        if shipped_count == len(pcs):
            return "enviado"
        # algum cliente já saiu mas não todos → segue como "separado"
    elif pacote.get("shipped_at"):
        # legado: pacote marcado como enviado mas sem pacote_clientes carregados
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
def list_packages_by_state(
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista pacotes agrupados por estado.

    Filtros opcionais: ?since=YYYY-MM-DD&until=YYYY-MM-DD (BRT) — restringem
    pelo timestamp da TRANSIÇÃO pro estado em que o pacote está hoje
    (opened_at, closed_at, approved_at, payment_validated_at, pdf_sent_at,
    shipped_at, cancelled_at). 'Pacotes fechados hoje' = pacotes cujo
    closed_at é hoje, independente da data de criação.
    """
    client = SupabaseRestClient.from_settings()
    since_iso, until_iso = _parse_date_range(since, until)

    if not (since_iso or until_iso):
        pacotes = client.select("pacotes", order="updated_at.desc") or []
    else:
        # Uma query por (status, campo de timestamp). Cada estado tem seu
        # campo de transição; pra pacotes 'approved' (que viram confirmado/
        # pago/pendente/separado/enviado), todos os timestamps de sub-estado
        # entram. Dedup por id, pois um mesmo pacote pode bater em mais de
        # uma query (ex.: approved_at e shipped_at ambos no range).
        query_specs: List[Tuple[str, str]] = [
            ("open", "opened_at"),
            ("closed", "closed_at"),
            ("cancelled", "cancelled_at"),
            ("approved", "approved_at"),
            ("approved", "payment_validated_at"),
            ("approved", "pdf_sent_at"),
            ("approved", "shipped_at"),
            ("approved", "updated_at"),
        ]
        pacote_dict: Dict[str, Dict[str, Any]] = {}
        for pkg_status, ts_field in query_specs:
            flt: List[Tuple[str, str, Any]] = [("status", "eq", pkg_status)]
            if since_iso:
                flt.append((ts_field, "gte", since_iso))
            if until_iso:
                flt.append((ts_field, "lte", until_iso))
            rows = client.select_all(
                "pacotes", filters=flt, order=f"{ts_field}.desc"
            ) or []
            for r in rows:
                pacote_dict[r["id"]] = r
        pacotes = sorted(
            pacote_dict.values(),
            key=lambda p: p.get("updated_at") or "",
            reverse=True,
        )
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

    state_ts_map = {
        "aberto": "opened_at",
        "fechado": "closed_at",
        "confirmado": "approved_at",          # aprovado mas esperando pagamento
        "pago": "updated_at",                 # último pagamento marcado paid
        "pendente": "payment_validated_at",   # gerente validou
        "separado": "pdf_sent_at",
        "enviado": "shipped_at",
        "cancelled": "cancelled_at",
    }

    for pkg in pacotes:
        pags = pagamentos_by_pacote.get(pkg["id"], [])
        pcs_of_pkg = pc_by_pacote.get(pkg["id"], [])
        state = _derive_state(pkg, pags, pcs_of_pkg)

        # Refina o filtro de data: queries SQL fazem corte amplo por
        # status+algum_timestamp, mas o estado derivado pode usar OUTRO
        # timestamp (ex.: pacote 'approved' veio pela query approved_at,
        # mas state='enviado' usa shipped_at). Pula se o timestamp do
        # estado real não está no range. Pra separado/enviado, filtro
        # é aplicado por pacote_cliente dentro do loop de cliente-rows.
        if (since_iso or until_iso) and state not in ("separado", "enviado"):
            state_ts = pkg.get(state_ts_map.get(state, "updated_at"))
            if not state_ts:
                continue
            if since_iso and state_ts < since_iso:
                continue
            if until_iso and state_ts > until_iso:
                continue

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

        # Pra pacote 'open' o tamanho correto é o que o `rebuild_for_poll`
        # gravou no banco — `clientes_out` aqui é o pool de votos candidatos
        # (todos votos da enquete sem dono ainda) e pode somar mais que o
        # open real quando rebuild está fora de sincronia (ex.: pacote
        # cancelado libera votos antes do próximo rebuild).
        if (pkg.get("status") or "").lower() == "open":
            total_qty = pkg.get("total_qty") or 0
        else:
            total_qty = sum(c["qty"] for c in clientes_out) or pkg.get("total_qty") or 0
        total_value = round(sum((c.get("total_amount") or 0.0) for c in clientes_out), 2) or None

        pags_summary = {
            "total": len(pags),
            "paid": sum(1 for p in pags if p.get("status") == "paid"),
            "sent": sum(1 for p in pags if p.get("status") == "sent"),
            "created": sum(1 for p in pags if p.get("status") == "created"),
        }

        # timestamp "do estado atual" = horário da última transição
        state_ts_field = state_ts_map.get(state, "updated_at")

        drive_id = (enq.get("drive_file_id") or (prod.get("drive_file_id") if prod else None))

        # Separado e Enviado têm granularidade de cliente: cada pacote_cliente
        # vira uma linha independente. Um pacote pode aparecer em ambas as
        # seções simultaneamente (parcialmente enviado).
        if state in ("separado", "enviado"):
            pkg_vendas = vendas_by_pacote.get(pkg["id"], [])
            for pc in pcs_of_pkg:
                # Fallback pkg.* pra pacotes legados sem pc.shipped_at/pc.pdf_sent_at
                # (backfill faz a propagação no banco; isto é rede de segurança).
                pc_shipped_at = pc.get("shipped_at") or pkg.get("shipped_at")
                pc_pdf_sent_at = pc.get("pdf_sent_at") or pkg.get("pdf_sent_at")
                if pc_shipped_at:
                    row_state, row_ts = "enviado", pc_shipped_at
                elif pc_pdf_sent_at:
                    row_state, row_ts = "separado", pc_pdf_sent_at
                else:
                    continue
                if since_iso and row_ts < since_iso:
                    continue
                if until_iso and row_ts > until_iso:
                    continue
                c = cliente_map.get(pc["cliente_id"], {})
                pag = next(
                    (p for p in pags if _venda_for_pc(p, pkg_vendas, pc["id"])),
                    None,
                )
                grouped[row_state].append({
                    "type": "client_row",
                    "id": pc["id"],
                    "state": row_state,
                    "pacote_id": pkg["id"],
                    "cliente_id": pc["cliente_id"],
                    "cliente_nome": c.get("nome"),
                    "cliente_phone": c.get("celular"),
                    "qty": pc.get("qty"),
                    "total_amount": pc.get("total_amount"),
                    "payment_status": pag.get("status") if pag else None,
                    "pacote_friendly_id": pkg.get("friendly_id"),
                    "pacote_sequence_no": pkg.get("sequence_no"),
                    "enquete_id": pkg.get("enquete_id"),
                    "enquete_title": enq.get("titulo"),
                    "external_poll_id": enq.get("external_poll_id"),
                    "produto_name": prod.get("nome") if prod else None,
                    "image": f"/files/{drive_id}" if drive_id else None,
                    "unit_price": unit_price,
                    "pdf_sent_at": pc_pdf_sent_at,
                    "shipped_at": pc_shipped_at,
                    "state_since": row_ts,
                    "age": _age_str(row_ts),
                    "created_at": pkg.get("created_at"),
                })
            continue

        item = {
            "id": pkg["id"],
            "state": state,
            "sequence_no": pkg.get("sequence_no"),
            "friendly_id": pkg.get("friendly_id"),
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
            "fornecedor": pkg.get("fornecedor") or "",
            "pending_reasons": pkg.get("pending_reasons") or [],
            "pending_observations": pkg.get("pending_observations") or "",
            "state_since": pkg.get(state_ts_field) or pkg.get("updated_at"),
            "age": _age_str(pkg.get(state_ts_field) or pkg.get("updated_at")),
            "created_at": pkg.get("created_at"),
        }

        if state == "cancelled":
            cancelled.append(item)
        else:
            grouped[state].append(item)

    # Fechado é ordenado pela data de fechamento (mais recente encima);
    # demais estados herdam o order=updated_at.desc da query.
    # Separado/enviado são cliente-rows, ordena pelo timestamp da linha.
    grouped["fechado"].sort(
        key=lambda it: it.get("state_since") or "",
        reverse=True,
    )
    for s in ("separado", "enviado"):
        grouped[s].sort(
            key=lambda it: it.get("state_since") or "",
            reverse=True,
        )

    counts = {s: len(grouped[s]) for s in FLOW_STATES}
    counts["cancelled"] = len(cancelled)

    return {
        "states": FLOW_STATES,
        "counts": counts,
        "packages_by_state": grouped,
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
            "commission_amount": pc["commission_amount"],
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
            commission_amount = round(qty * settings.COMMISSION_PER_PIECE, 2)
            total = round(subtotal + commission_amount, 2)
            clientes_detail.append({
                "cliente_id": v.get("cliente_id"),
                "nome": c.get("nome"),
                "celular": c.get("celular"),
                "qty": qty,
                "subtotal": subtotal,
                "commission_amount": commission_amount,
                "total_amount": total,
                "venda_status": None,
                "pagamento_status": None,
                "payment_link": None,
                "paid_at": None,
                "is_voter_only": True,
            })

    state = _derive_state(pkg, pags, pcs)

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
        "friendly_id": pkg.get("friendly_id"),
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
        "pending_reasons": pkg.get("pending_reasons") or [],
        "pending_observations": pkg.get("pending_observations") or "",
    }


# ---------------------------------------------------------------------------
# Ações (features #1 e #5 — advance usa fluxo linear)
# ---------------------------------------------------------------------------

def _load_pkg_and_pags(client: SupabaseRestClient, pacote_id: str):
    """Retorna (pkg, vendas, pagamentos, pacote_clientes). Pacote_clientes
    é usado pra _derive_state diferenciar 'separado parcial' (algum pc.shipped_at
    mas não todos) de 'enviado'."""
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    vendas = client.select("vendas", filters=[("pacote_id", "eq", pacote_id)]) or []
    pags: List[Dict[str, Any]] = []
    if vendas:
        venda_ids = [v["id"] for v in vendas]
        pags = client.select("pagamentos", filters=[("venda_id", "in", venda_ids)]) or []
    pcs = client.select("pacote_clientes", filters=[("pacote_id", "eq", pacote_id)]) or []
    return pkg, vendas, pags, pcs


VALID_PENDING_REASONS = {
    "faltando_pecas", "tamanhos_trocados", "cores_trocadas",
    "modelo_errado", "pacote_com_defeito", "cancelado_fornecedor", "outros",
}


def _fornecedor_required_for(pkg: Dict[str, Any]) -> bool:
    """True se o pacote cai dentro do gate de 'fornecedor obrigatório'.
    Critério: settings.PACOTE_REQUER_FORNECEDOR_DESDE setado E pkg.closed_at
    igual ou maior que o cutoff. Default (env não setada) = False, preserva
    comportamento legado."""
    cutoff = getattr(settings, "PACOTE_REQUER_FORNECEDOR_DESDE", None) or None
    if not cutoff:
        return False
    closed_at = pkg.get("closed_at")
    if not closed_at:
        return False
    return closed_at >= cutoff


async def _persist_fornecedor_or_raise(
    request: Request,
    client: SupabaseRestClient,
    pkg: Dict[str, Any],
) -> None:
    """Lê body.fornecedor e grava em pacotes.fornecedor + enquetes.fornecedor
    (se ainda NULL). Levanta 400 fornecedor_required se o gate está ligado
    e o body veio sem fornecedor. Salva sempre que o body trouxer valor,
    mesmo com gate desligado."""
    if pkg.get("fornecedor"):
        return  # já preenchido (provavelmente herdado da enquete)
    try:
        body = await request.json()
    except Exception:
        body = {}
    fornecedor_raw = body.get("fornecedor") if isinstance(body, dict) else None
    fornecedor = (str(fornecedor_raw) if fornecedor_raw else "").strip()
    if not fornecedor:
        # Gate: só obrigatório se PACOTE_REQUER_FORNECEDOR_DESDE estiver setado
        if _fornecedor_required_for(pkg):
            raise HTTPException(
                status_code=400,
                detail={"code": "fornecedor_required",
                        "message": "Selecione o fornecedor para confirmar este pacote."},
            )
        return  # gate desligado e nada veio no body — ok
    pacote_id = pkg["id"]
    client.update(
        "pacotes",
        {"fornecedor": fornecedor},
        filters=[("id", "eq", pacote_id)],
    )
    enquete_id = pkg.get("enquete_id")
    if enquete_id:
        enq = client.select(
            "enquetes",
            columns="id,fornecedor",
            filters=[("id", "eq", enquete_id)],
            single=True,
        ) or {}
        if not (enq.get("fornecedor") or "").strip():
            client.update(
                "enquetes",
                {"fornecedor": fornecedor},
                filters=[("id", "eq", enquete_id)],
            )


async def _persist_pending_reasons(
    request: Request,
    client: SupabaseRestClient,
    pacote_id: str,
) -> None:
    """Lê motivos do body e grava nas colunas do pacote. Exigido sempre que
    o pacote vai parar em pendente via clique manual ('Marcar pendente').
    Pulos como 'Gerar etiqueta' (to=separado) marcam request.state.skip_pending_reasons."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reasons_raw = body.get("reasons") or []
    if not isinstance(reasons_raw, list):
        raise HTTPException(400, "Campo 'reasons' deve ser uma lista.")
    reasons = [str(r).strip() for r in reasons_raw if r]
    if not reasons:
        raise HTTPException(400, "Selecione pelo menos um motivo pra mover o pacote pra pendente.")
    invalid = [r for r in reasons if r not in VALID_PENDING_REASONS]
    if invalid:
        raise HTTPException(400, f"Motivo(s) inválido(s): {invalid}")
    observations = str(body.get("observations") or "").strip()
    if "outros" in reasons and not observations:
        raise HTTPException(400, "Observação obrigatória quando 'Outros' é selecionado.")
    client.update("pacotes", {
        "pending_reasons": reasons,
        "pending_observations": observations or None,
    }, filters=[("id", "eq", pacote_id)])


@router.post("/packages/{pacote_id}/advance")
async def advance_package(pacote_id: str, request: Request, to: Optional[str] = None) -> Dict[str, Any]:
    """Avança o pacote para o próximo estado do fluxo linear (feature #5).

    aberto → fechado → confirmado → pago → pendente → separado → enviado.

    Aceita query param `to=<estado>` pra pular várias etapas de uma vez.

    Quando o destino imediato é 'pendente' (Estoque clicou 'Marcar pendente'),
    exige body JSON com `{reasons: [...], observations?: '...'}`.
    """
    client = SupabaseRestClient.from_settings()
    pkg, vendas, pags, pcs = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags, pcs)
    role = _role_from(request)
    if not _auth.can_advance(role, state, to):
        raise HTTPException(403, f"Role '{role}' não pode avançar de '{state}'" + (f" pra '{to}'" if to else ""))

    # Motivos obrigatórios quando o pacote vai *parar* em pendente.
    # Pulos (to != pendente que passam por pendente como intermediário) marcam
    # skip_pending_reasons no request.state pra que a recursão não exija.
    skip_reasons = getattr(request.state, "skip_pending_reasons", False)
    going_to_pendente = (to == "pendente") or (to is None and state == "pago")
    if going_to_pendente and not skip_reasons:
        await _persist_pending_reasons(request, client, pacote_id)

    # Fornecedor obrigatório no fechado→confirmado (gated por cutoff env var).
    # Pulos via to=<estado posterior> setam skip_fornecedor_required pra não
    # exigir o body fornecedor em cada step intermediário da recursão.
    skip_fornecedor = getattr(request.state, "skip_fornecedor_required", False)
    going_to_confirmado = (to == "confirmado") or (to is None and state == "fechado")
    if going_to_confirmado and not skip_fornecedor:
        await _persist_fornecedor_or_raise(request, client, pkg)

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
        # to != pendente passando por pendente é intermediário — pula o check.
        if to != "pendente":
            request.state.skip_pending_reasons = True
        # to != confirmado passando por fechado→confirmado intermediário — pula
        # o gate de fornecedor (admin pulando várias etapas via API).
        if to != "confirmado":
            request.state.skip_fornecedor_required = True
        while cur_idx < target_idx and steps < len(FLOW_STATES):
            await advance_package(pacote_id, request, to=None)  # avança 1 step
            pkg, vendas, pags, pcs = _load_pkg_and_pags(client, pacote_id)
            state = _derive_state(pkg, pags, pcs)
            if state not in FLOW_STATES:
                break
            cur_idx = FLOW_STATES.index(state)
            steps += 1
        return {"status": "ok", "previous": previous, "new_state": state, "steps": steps}

    if state == "aberto":
        client.update("pacotes",
                      {"status": "closed", "closed_at": now},
                      filters=[("id", "eq", pacote_id)])
        try:
            from app.services.friendly_id_service import assign_friendly_id
            assign_friendly_id(client, pacote_id)
        except Exception:
            logger.exception("falha ao atribuir friendly_id pacote=%s", pacote_id)
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
                "status": "created",
                "payload_json": {},
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
                # Confirma o débito do crédito aplicado (se houver) — fora do
                # polling Asaas esse é o único ponto que abate do saldo.
                credit_service.confirm_debit(pagamento_id=p["id"])
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
        # Propaga pra todos os pacote_clientes (separado é granular por cliente).
        client.update(
            "pacote_clientes",
            {"pdf_sent_at": now},
            filters=[("pacote_id", "eq", pacote_id), ("pdf_sent_at", "is", "null")],
        )
        return {"status": "ok", "previous": "pendente", "new_state": "separado"}

    if state == "separado":
        # Atalho admin/logística: marca o pacote inteiro como enviado.
        # Marca todos os pacote_clientes que ainda não foram enviados + pkg.shipped_at.
        client.update(
            "pacote_clientes",
            {"shipped_at": now},
            filters=[("pacote_id", "eq", pacote_id), ("shipped_at", "is", "null")],
        )
        client.update("pacotes",
                      {"shipped_at": now, "shipped_by": role},
                      filters=[("id", "eq", pacote_id)])
        return {"status": "ok", "previous": "separado", "new_state": "enviado"}

    if state == "enviado":
        raise HTTPException(400, "Pacote já está no estado final (enviado)")
    if state == "cancelled":
        raise HTTPException(400, "Pacote cancelado não pode avançar")
    raise HTTPException(400, f"Estado desconhecido: {state}")


@router.post("/packages/{pacote_id}/regress")
def regress_package(pacote_id: str, request: Request) -> Dict[str, Any]:
    """Reverte o pacote pro estado anterior do fluxo. Exclusivo do role admin."""
    role = _role_from(request)
    if not _auth.can_regress(role):
        raise HTTPException(403, "Apenas o administrador pode reverter etapas.")
    client = SupabaseRestClient.from_settings()
    pkg, vendas, pags, pcs = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags, pcs)
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
        # Volta pra fila de separação. Atualizamos payment_validated_at pra
        # refletir o momento do regress: o listing filtra "pendente" por esse
        # timestamp, então sem isso o pacote sumiria da view atual.
        # Também zera pdf_sent_at nos pacote_clientes (separado é granular).
        client.update("pacotes",
                      {"pdf_sent_at": None, "pdf_status": None, "pdf_file_name": None,
                       "payment_validated_at": now},
                      filters=[("id", "eq", pacote_id)])
        client.update(
            "pacote_clientes",
            {"pdf_sent_at": None, "shipped_at": None},
            filters=[("pacote_id", "eq", pacote_id)],
        )
        return {"status": "ok", "previous": "separado", "new_state": "pendente"}

    if state == "enviado":
        # Zera pkg.shipped_at e todos os pacote_clientes.shipped_at.
        client.update("pacotes",
                      {"shipped_at": None, "shipped_by": None},
                      filters=[("id", "eq", pacote_id)])
        client.update(
            "pacote_clientes",
            {"shipped_at": None},
            filters=[("pacote_id", "eq", pacote_id)],
        )
        return {"status": "ok", "previous": "enviado", "new_state": "separado"}

    raise HTTPException(400, f"Estado desconhecido: {state}")


@router.post("/packages/{pacote_id}/cancel")
async def cancel_package(pacote_id: str, request: Request) -> Dict[str, Any]:
    """Cancela o pacote em cascata gerando crédito pros que já pagaram.

    Sem `force` e havendo pagamentos pagos: retorna 409 `blocked_paid` com a
    lista de clientes pagos pra UI confirmar. Com `force=true`: cancela tudo e
    o valor pago de cada cliente vira crédito na plataforma.
    """
    role = _role_from(request)
    if not _auth.can_cancel(role):
        raise HTTPException(403, "Apenas o administrador pode cancelar pacotes.")

    force = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force") or False)
    except Exception:
        pass

    from app.services import package_cancellation_service as pcs
    try:
        result = await asyncio.to_thread(
            pcs.cancel_package, pacote_id, force=force, cancelled_by=role
        )
    except pcs.PackageNotFound:
        raise HTTPException(404, "Pacote não encontrado")
    except pcs.PackageCancelBlocked as exc:
        return JSONResponse(
            status_code=409,
            content={
                "status": "blocked_paid",
                "paid_count": len(exc.paid_info),
                "paid_clients": exc.paid_info,
            },
        )

    try:
        from app.services.finance_service import (
            refresh_charge_snapshot, refresh_dashboard_stats,
        )
        from app.services.customer_service import refresh_customer_rows_snapshot
        await asyncio.to_thread(refresh_charge_snapshot)
        await asyncio.to_thread(refresh_dashboard_stats)
        await asyncio.to_thread(refresh_customer_rows_snapshot)
    except Exception:
        logger.warning("cancel_package: refresh de snapshots falhou", exc_info=True)

    return {"status": "ok", "new_state": "cancelled", **result}


@router.patch("/packages/{pacote_id}/fornecedor")
async def upsert_package_fornecedor(pacote_id: str, request: Request) -> Dict[str, Any]:
    """Atualiza o fornecedor de um pacote. Admin only."""
    role = _role_from(request)
    if role != "admin":
        raise HTTPException(403, "Apenas o administrador pode editar o fornecedor.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    fornecedor = (str(body.get("fornecedor") or "")).strip()
    if not fornecedor:
        raise HTTPException(400, "Fornecedor não pode ser vazio.")
    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    client.update("pacotes", {"fornecedor": fornecedor}, filters=[("id", "eq", pacote_id)])
    # Propaga pra enquete se ainda não tiver
    enquete_id = pkg.get("enquete_id")
    if enquete_id:
        enq = client.select("enquetes", columns="id,fornecedor",
                             filters=[("id", "eq", enquete_id)], single=True) or {}
        if not (enq.get("fornecedor") or "").strip():
            client.update("enquetes", {"fornecedor": fornecedor},
                          filters=[("id", "eq", enquete_id)])
    return {"status": "ok", "fornecedor": fornecedor}


@router.post("/packages/{pacote_id}/restore")
def restore_package(pacote_id: str, request: Request) -> Dict[str, Any]:
    """Restaura um pacote cancelado para 'fechado' (status=closed). Admin only."""
    role = _role_from(request)
    if not _auth.can_restore(role):
        raise HTTPException(403, "Apenas o administrador pode restaurar pacotes.")
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


@router.get("/packages/{pacote_id}/etiqueta.pdf")
def get_package_etiqueta_pdf(pacote_id: str, request: Request) -> Response:
    """Gera o PDF de etiqueta do pacote on-demand pra download no dashboard.

    Disponível a partir do estado 'separado' (quando estoque clicou 'Gerar
    etiqueta' e pdf_sent_at foi setado). Qualquer role logado pode baixar —
    estoque/logística precisam pra trabalhar.
    """
    # Imports lazy pra evitar custo no boot do módulo
    from estoque.pdf_builder import build_pdf

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")
    if not pkg.get("pdf_sent_at"):
        raise HTTPException(409, "Etiqueta ainda não gerada — avance o pacote pra 'Separado' primeiro.")

    enq = {}
    if pkg.get("enquete_id"):
        enq = client.select("enquetes", filters=[("id", "eq", pkg["enquete_id"])], single=True) or {}

    pcs = client.select("pacote_clientes", filters=[("pacote_id", "eq", pacote_id)]) or []
    cliente_ids = list({pc["cliente_id"] for pc in pcs if pc.get("cliente_id")})
    clientes = client.select_all(
        "clientes", columns="id,nome,celular",
        filters=[("id", "in", cliente_ids)],
    ) if cliente_ids else []
    cliente_by_id = {c["id"]: c for c in clientes}

    votes = []
    for pc in pcs:
        c = cliente_by_id.get(pc.get("cliente_id"), {})
        votes.append({
            "name": c.get("nome") or "Cliente",
            "phone": c.get("celular") or "",
            "qty": int(pc.get("qty") or 0),
        })

    package = {
        "id": pacote_id,
        "friendly_id": pkg.get("friendly_id") or "",
        "poll_title": enq.get("titulo") or pkg.get("custom_title") or "Pedido",
        "votes": votes,
    }

    try:
        pdf_bytes = build_pdf(package, settings.COMMISSION_PER_PIECE)
    except Exception:
        logger.exception("Falha ao gerar etiqueta on-demand pkg=%s", pacote_id)
        raise HTTPException(500, "Erro ao gerar PDF da etiqueta")

    filename = pkg.get("pdf_file_name") or f"etiqueta-{pkg.get('sequence_no') or pacote_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_CLIENT_FLOW = ["pago", "pendente", "separado", "enviado"]


@router.post("/packages/{pacote_id}/clients/{cliente_id}/advance")
def advance_client(
    pacote_id: str,
    cliente_id: str,
    request: Request,
    to: Optional[str] = None,
) -> Dict[str, Any]:
    """Avança UM cliente individualmente nas fases finais (pago→pendente→separado→enviado).
    Aceita ?to=<estado> pra pular várias etapas. Não toca em outros clientes do pacote.

    Quando este cliente é o último do pacote a ser marcado como 'enviado',
    também seta pkg.shipped_at — assim o pacote vira 'enviado' no estado agregado.
    """
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

    role = _role_from(request)
    target_state = _CLIENT_FLOW[target_idx]
    # Valida permissão pelo estado atual e destino na granularidade de cliente.
    if not _auth.can_advance(role, state, target_state):
        raise HTTPException(
            403,
            f"Role '{role}' não pode avançar cliente de '{state}' pra '{target_state}'",
        )

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

    # Se marcou shipped_at e este foi o último cliente sem shipped_at, propaga
    # pra pkg.shipped_at — pacote vira 'enviado' no agregado.
    if "shipped_at" in update_payload and not pkg.get("shipped_at"):
        all_pcs = client.select(
            "pacote_clientes",
            filters=[("pacote_id", "eq", pacote_id)],
        ) or []
        # this pc já está atualizado no banco; checa todos os outros
        all_shipped = all(
            (other["id"] == pc["id"]) or other.get("shipped_at")
            for other in all_pcs
        )
        if all_shipped and all_pcs:
            client.update(
                "pacotes",
                {"shipped_at": now, "shipped_by": role},
                filters=[("id", "eq", pacote_id)],
            )

    return {
        "status": "ok",
        "previous": state,
        "new_state": target_state,
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
    credit_service.confirm_debit(pagamento_id=pag["id"])
    return {"status": "ok", "action": "client_marked_paid", "cliente_id": cliente_id}


@router.post("/packages/{pacote_id}/resend-pix")
def resend_pix(pacote_id: str) -> Dict[str, Any]:
    client = SupabaseRestClient.from_settings()
    _pkg, _vendas, pags, _pcs = _load_pkg_and_pags(client, pacote_id)
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


def _is_pending_client(cli: Dict[str, Any]) -> bool:
    """Cliente 'pendente' = nome ainda é o fallback 'Cliente' que veio de
    webhook WHAPI/Evolution sem pushName. Não considera CPF — cliente pode
    ter nome real mas ainda estar sem CPF, e isso é fluxo válido até ele
    clicar em pagar no portal (lazy create no Asaas)."""
    nome = (cli.get("nome") or "").strip().lower()
    return nome == "cliente"


@router.get("/clientes/list")
def list_clientes_admin(
    q: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Listagem pra aba 'Clientes' do dashboard. Suporta busca por nome/celular/CPF,
    filtros `status=pending` (nome 'Cliente') e `status=complete` (nome real),
    e paginação. Retorna envelope com items + total.
    """
    client = SupabaseRestClient.from_settings()
    rows = client.select(
        "clientes",
        columns="id,nome,celular,cpf_cnpj,created_at,updated_at",
        order="created_at.desc",
    ) or []
    if q:
        ql = q.lower()
        rows = [c for c in rows
                if ql in (c.get("nome") or "").lower()
                or ql in (c.get("celular") or "")
                or ql in (c.get("cpf_cnpj") or "")]
    if status == "pending":
        rows = [c for c in rows if _is_pending_client(c)]
    elif status == "complete":
        rows = [c for c in rows if not _is_pending_client(c)]
    total = len(rows)
    page = max(page, 1)
    size = max(min(page_size, 200), 1)
    start = (page - 1) * size
    items = rows[start:start + size]
    return {"items": items, "total": total, "page": page, "page_size": size}


@router.get("/clientes/stats")
def clientes_stats() -> Dict[str, int]:
    """Contadores pros sub-itens do dropdown 'Clientes'."""
    client = SupabaseRestClient.from_settings()
    rows = client.select("clientes", columns="id,nome") or []
    pending = sum(1 for c in rows if _is_pending_client(c))
    total = len(rows)
    return {"total": total, "complete": total - pending, "pending": pending}


@router.patch("/clientes/{cliente_id}")
def update_cliente(cliente_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Atualiza dados de um cliente. Hoje só suporta `nome` — outros campos
    podem ser adicionados conforme necessidade."""
    nome = body.get("nome")
    if nome is None:
        raise HTTPException(400, "nome é obrigatório")
    from app.services.whatsapp_domain_service import _sanitize_name
    nome_clean = _sanitize_name(nome, fallback="")
    if not nome_clean:
        raise HTTPException(400, "nome não pode ser vazio")

    client = SupabaseRestClient.from_settings()
    existing = client.select(
        "clientes", filters=[("id", "eq", cliente_id)], single=True,
    )
    if not existing:
        raise HTTPException(404, "Cliente não encontrado")

    client.update(
        "clientes",
        {"nome": nome_clean, "updated_at": datetime.now(timezone.utc).isoformat()},
        filters=[("id", "eq", cliente_id)],
    )
    return {"id": cliente_id, "nome": nome_clean}


@router.post("/packages/{pacote_id}/clients")
def add_client_to_package(pacote_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Adiciona cliente. Aberto → cria voto status='in'. Fechado/confirmado →
    cria pacote_cliente (desbalanceia capacidade; aceito em dev)."""
    cliente_id = body.get("cliente_id")
    qty = int(body.get("qty") or 3)
    if not cliente_id:
        raise HTTPException(400, "cliente_id é obrigatório")
    if qty not in (3, 4, 6, 8, 9, 12, 16, 20, 24):
        raise HTTPException(400, "qty deve ser 3, 4, 6, 8, 9, 12, 16, 20 ou 24")

    client = SupabaseRestClient.from_settings()
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True)
    if not pkg:
        raise HTTPException(404, "Pacote não encontrado")

    _pkg2, _vendas, pags, pcs = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags, pcs)
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

    _pkg2, _vendas, pags, pcs = _load_pkg_and_pags(client, pacote_id)
    state = _derive_state(pkg, pags, pcs)
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


def _swap_eligible_voters(client: SupabaseRestClient, pacote_id: str, cliente_id: str) -> Dict[str, Any]:
    """Substitutos elegíveis pra trocar alguém num pacote.

    Cada candidato precisa: ter voto status != 'out' na MESMA enquete, qty
    <= qty alvo (pra permitir composição por soma — ex: trocar 1 de 12 por
    2 de 6), não estar já no pacote, e não ter sido consumido por outro
    pacote ativo dessa enquete.

    Retorna `{"target_qty": int, "candidates": [{id, nome, celular, qty,
    voto_id}, ...]}`. O frontend usa `target_qty` pra validar que a soma
    das qtys escolhidas bate antes de submeter.
    """
    pkg = client.select("pacotes", filters=[("id", "eq", pacote_id)], single=True) or {}
    pc_atual = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id), ("cliente_id", "eq", cliente_id)],
        single=True,
    )
    if not pc_atual:
        return {"target_qty": 0, "candidates": []}
    qty_alvo = int(pc_atual.get("qty") or 0)
    enquete_id = pkg.get("enquete_id")
    if not enquete_id or qty_alvo <= 0:
        return {"target_qty": qty_alvo, "candidates": []}

    # Clientes já no pacote atual (não podem ser substitutos — duplicaria
    # unique (pacote_id, cliente_id)). O que está saindo é o único exceto.
    own_pcs = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id)],
    ) or []
    own_ids = {pc["cliente_id"] for pc in own_pcs if pc["cliente_id"] != cliente_id}

    # Clientes consumidos por OUTROS pacotes não-cancelados da mesma enquete
    pcs_enq = client.select("pacote_clientes") or []
    pacotes_enq = {p["id"]: p for p in client.select("pacotes",
        filters=[("enquete_id", "eq", enquete_id)]) or []}
    consumed: Set[str] = set()
    for pc in pcs_enq:
        pkt = pacotes_enq.get(pc["pacote_id"])
        if not pkt:
            continue
        if (pkt.get("status") or "").lower() == "cancelled":
            continue
        if pc["pacote_id"] == pacote_id:
            continue  # do próprio pacote — tratado em own_ids
        consumed.add(pc["cliente_id"])

    votos = client.select(
        "votos",
        filters=[
            ("enquete_id", "eq", enquete_id),
            ("qty", "lte", qty_alvo),
            ("status", "neq", "out"),
        ],
    ) or []
    # Um voto por cliente (filtra qty>0 e dedupa preferindo o mais "novo" se houver).
    voto_by_cliente: Dict[str, Dict[str, Any]] = {}
    for v in votos:
        cli = v.get("cliente_id")
        qty = int(v.get("qty") or 0)
        if not cli or qty <= 0:
            continue
        if cli == cliente_id or cli in consumed or cli in own_ids:
            continue
        voto_by_cliente[cli] = v  # último vence

    if not voto_by_cliente:
        return {"target_qty": qty_alvo, "candidates": []}

    rows = client.select("clientes",
                         filters=[("id", "in", list(voto_by_cliente.keys()))],
                         order="nome.asc") or []
    candidates = []
    for c in rows:
        v = voto_by_cliente.get(c["id"])
        if not v:
            continue
        candidates.append({
            "id": c["id"],
            "nome": c.get("nome"),
            "celular": c.get("celular"),
            "qty": int(v["qty"]),
            "voto_id": v["id"],
        })
    return {"target_qty": qty_alvo, "candidates": candidates}


@router.get("/packages/{pacote_id}/swap-candidates/{cliente_id}")
def list_swap_candidates(pacote_id: str, cliente_id: str) -> Dict[str, Any]:
    """Quem pode substituir o cliente atual. Retorna `target_qty` (qty a ser
    coberta pela combinação) e lista de candidatos com a qty individual de
    cada voto."""
    client = SupabaseRestClient.from_settings()
    return _swap_eligible_voters(client, pacote_id, cliente_id)


@router.patch("/packages/{pacote_id}/clients/{cliente_id}")
def swap_client_in_package(pacote_id: str, cliente_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Troca o cliente no pacote por um ou mais substitutos cuja soma das qtys
    seja igual à qty do cliente que sai (ex.: 1 de 12 → 2 de 6).

    Payload: `{ "new_cliente_ids": ["c1", "c2", ...] }`.

    Cada substituto precisa ter voto na mesma enquete (qty <= qty alvo, status
    != 'out') e não estar consumido. O cliente que sai não pode ter pagamento
    'paid'. Cobranças Asaas existentes são canceladas best-effort. Venda e
    pagamento antigos são deletados; um par (venda + pagamento status='created')
    é criado por substituto — a cobrança Asaas nova nasce lazy quando o cliente
    abrir o portal.
    """
    raw_ids = body.get("new_cliente_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(400, "new_cliente_ids deve ser uma lista não vazia")
    new_cliente_ids: List[str] = [str(x) for x in raw_ids if x]
    if not new_cliente_ids:
        raise HTTPException(400, "new_cliente_ids deve ser uma lista não vazia")
    if len(set(new_cliente_ids)) != len(new_cliente_ids):
        raise HTTPException(400, "new_cliente_ids contém duplicatas")
    if cliente_id in new_cliente_ids:
        raise HTTPException(400, "novo cliente igual ao atual")

    client = SupabaseRestClient.from_settings()
    elig = _swap_eligible_voters(client, pacote_id, cliente_id)
    target_qty = int(elig.get("target_qty") or 0)
    cand_by_id = {c["id"]: c for c in elig.get("candidates", [])}
    if target_qty <= 0:
        raise HTTPException(404, "Cliente não está no pacote")

    missing = [cid for cid in new_cliente_ids if cid not in cand_by_id]
    if missing:
        raise HTTPException(
            400,
            "Substituto inválido: precisa ter voto na mesma enquete com qty <= "
            f"{target_qty} e não estar em outro pacote",
        )

    soma = sum(int(cand_by_id[cid]["qty"]) for cid in new_cliente_ids)
    if soma != target_qty:
        raise HTTPException(
            400,
            f"Soma das quantidades dos substitutos ({soma}) precisa ser igual à "
            f"qty do cliente saindo ({target_qty})",
        )

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

    vendas = client.select(
        "vendas",
        filters=[("pacote_cliente_id", "eq", pc["id"])],
    ) or []
    venda_ids = [v["id"] for v in vendas]

    pagamentos = []
    if venda_ids:
        pagamentos = client.select(
            "pagamentos",
            filters=[("venda_id", "in", venda_ids)],
        ) or []
    if any(str(p.get("status") or "").lower() == "paid" for p in pagamentos):
        raise HTTPException(409, "Cliente já pagou — não pode ser substituído")

    # Cancela cobranças Asaas (best-effort) antes de derrubar pagamentos locais.
    from integrations.asaas.client import AsaasClient
    asaas = AsaasClient()
    for pag in pagamentos:
        provider_id = pag.get("provider_payment_id")
        if not provider_id:
            continue
        try:
            asaas.cancel_payment(provider_id)
        except Exception as exc:
            logger.warning(
                "swap: falha cancelando cobrança Asaas %s (pagamento=%s): %s",
                provider_id, pag.get("id"), exc,
            )

    now_iso = datetime.now(timezone.utc).isoformat()

    # Resolve produto + unit_price da enquete pra calcular financeiros dos novos pcs.
    produto_id = pc.get("produto_id")
    unit_price = float(pc.get("unit_price") or 0)
    if (not produto_id or unit_price <= 0) and pkg.get("enquete_id"):
        enq = client.select(
            "enquetes",
            filters=[("id", "eq", pkg["enquete_id"])],
            single=True,
        ) or {}
        if not produto_id:
            produto_id = enq.get("produto_id")
        if unit_price <= 0 and enq.get("produto_id"):
            prod = client.select(
                "produtos",
                filters=[("id", "eq", enq["produto_id"])],
                single=True,
            ) or {}
            unit_price = float(prod.get("valor_unitario") or 0)

    commission_percent = float(pc.get("commission_percent") or 0)
    commission_per_piece = float(settings.COMMISSION_PER_PIECE)

    # Derruba pagamentos → vendas → pacote_cliente do cliente saindo.
    for pag in pagamentos:
        client.delete("pagamentos", filters=[("id", "eq", pag["id"])])
    for v in vendas:
        client.delete("vendas", filters=[("id", "eq", v["id"])])
    client.delete("pacote_clientes", filters=[("id", "eq", pc["id"])])

    had_vendas = bool(vendas)

    # Cria N pacote_clientes (+ vendas + pagamentos quando o pacote já tinha).
    created = []
    for cid in new_cliente_ids:
        cand = cand_by_id[cid]
        qty = int(cand["qty"])
        subtotal = round(unit_price * qty, 2)
        commission_amount = round(qty * commission_per_piece, 2)
        total_amount = round(subtotal + commission_amount, 2)
        pc_row = client.insert("pacote_clientes", {
            "pacote_id": pacote_id,
            "cliente_id": cid,
            "voto_id": cand["voto_id"],
            "produto_id": produto_id,
            "qty": qty,
            "unit_price": unit_price,
            "subtotal": subtotal,
            "commission_percent": commission_percent,
            "commission_amount": commission_amount,
            "total_amount": total_amount,
            "status": pc.get("status") or "closed",
            "created_at": now_iso,
            "updated_at": now_iso,
        })
        pc_new = pc_row[0] if isinstance(pc_row, list) else pc_row
        if had_vendas:
            venda_row = client.insert("vendas", {
                "pacote_id": pacote_id,
                "cliente_id": cid,
                "produto_id": produto_id,
                "pacote_cliente_id": pc_new["id"],
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "commission_percent": commission_percent,
                "commission_amount": commission_amount,
                "total_amount": total_amount,
                "status": "approved",
                "sold_at": now_iso,
            })
            venda_new = venda_row[0] if isinstance(venda_row, list) else venda_row
            client.insert("pagamentos", {
                "venda_id": venda_new["id"],
                "provider": "asaas",
                "status": "created",
                "payload_json": {},
            })
        created.append({"cliente_id": cid, "qty": qty})

    return {
        "status": "ok",
        "action": "swapped",
        "from_cliente_id": cliente_id,
        "to": created,
        "pagamentos_removidos": len(pagamentos),
    }


# ============================================================
# Enquetes — visão por enquete (granularidade enquete → pacotes → clientes)
# ============================================================

@router.get("/enquetes")
def list_enquetes(
    since: Optional[str] = None,
    until: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Lista enquetes paginada com contadores de pacotes por status. Filtro
    de data aplica-se a `enquetes.created_at` (BRT). Busca `q` filtra título
    (case-insensitive). `page`/`page_size` padrão 1/50 — evita estourar URL do
    PostgREST no filtro de pacotes."""
    client = SupabaseRestClient.from_settings()
    since_iso, until_iso = _parse_date_range(since, until)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    filters: List[Tuple[str, str, Any]] = []
    if since_iso:
        filters.append(("created_at", "gte", since_iso))
    if until_iso:
        filters.append(("created_at", "lte", until_iso))

    # Traz TODAS as enquetes do filtro de data + busca, calcula contagem de
    # pacotes (em batches pra não estourar URL do PostgREST com in.(...)) e
    # ordena por pacotes_fechados desc antes de paginar.
    all_filtered = client.select_all(
        "enquetes", filters=filters, order="created_at.desc",
    ) or []
    needle = (q or "").strip().lower()
    if needle:
        all_filtered = [e for e in all_filtered
                        if needle in (e.get("titulo") or "").lower()]

    all_ids = [e["id"] for e in all_filtered]
    counts_by_enq: Dict[str, Dict[str, int]] = {}
    # Lote de 100 ids por chamada — cada id ~36 chars + sep, fica em ~3.7k bytes,
    # bem abaixo do limite de URL do PostgREST.
    BATCH = 100
    for i in range(0, len(all_ids), BATCH):
        chunk = all_ids[i:i + BATCH]
        rows = client.select(
            "pacotes",
            filters=[("enquete_id", "in", chunk)],
        ) or []
        for pk in rows:
            eid = pk.get("enquete_id")
            if not eid:
                continue
            status = (pk.get("status") or "").lower() or "unknown"
            bucket = counts_by_enq.setdefault(eid, {"total": 0})
            bucket["total"] += 1
            bucket[status] = bucket.get(status, 0) + 1

    def _fechados(e: Dict[str, Any]) -> int:
        c = counts_by_enq.get(e["id"], {})
        return c.get("closed", 0) + c.get("approved", 0)

    # Ordena por fechados desc (tiebreak: created_at desc — já está nessa ordem
    # vinda do select_all, então sorted estável preserva).
    all_filtered.sort(key=_fechados, reverse=True)
    total = len(all_filtered)
    start = (page - 1) * page_size
    enquetes = all_filtered[start:start + page_size]

    produto_ids = list({e["produto_id"] for e in enquetes if e.get("produto_id")})
    produtos = client.select("produtos", filters=[("id", "in", produto_ids)]) if produto_ids else []
    prod_map = {p["id"]: p for p in produtos or []}

    items = []
    for e in enquetes:
        c = counts_by_enq.get(e["id"], {"total": 0})
        prod = prod_map.get(e.get("produto_id"), {}) if e.get("produto_id") else {}
        drive_id = e.get("drive_file_id") or (prod.get("drive_file_id") if prod else None)
        items.append({
            "id": e["id"],
            "titulo": e.get("titulo"),
            "status": e.get("status"),
            "fornecedor": e.get("fornecedor"),
            "created_at": e.get("created_at"),
            "image": f"/files/{drive_id}" if drive_id else None,
            "produto": {
                "id": prod.get("id"),
                "nome": _clean_product_name(prod.get("nome")),
                "valor_unitario": prod.get("valor_unitario"),
            } if prod else None,
            "pacotes_total": c.get("total", 0),
            "pacotes_fechados": c.get("closed", 0) + c.get("approved", 0),
            "pacotes_by_status": {
                "open": c.get("open", 0),
                "closed": c.get("closed", 0),
                "approved": c.get("approved", 0),
                "cancelled": c.get("cancelled", 0),
            },
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/enquetes/{enquete_id}")
def get_enquete_detail(enquete_id: str) -> Dict[str, Any]:
    """Detalhe de uma enquete: dados básicos + lista de pacotes, cada um com
    seus clientes e o estado individual derivado (mesma lógica do detail de
    pacote)."""
    client = SupabaseRestClient.from_settings()
    enq = client.select("enquetes", filters=[("id", "eq", enquete_id)], single=True)
    if not enq:
        raise HTTPException(404, "Enquete não encontrada")

    prod = {}
    if enq.get("produto_id"):
        prod = client.select(
            "produtos",
            filters=[("id", "eq", enq["produto_id"])],
            single=True,
        ) or {}

    pacotes = client.select(
        "pacotes",
        filters=[("enquete_id", "eq", enquete_id)],
        order="sequence_no.asc",
    ) or []
    pacote_ids = [p["id"] for p in pacotes]

    pcs = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "in", pacote_ids)],
    ) if pacote_ids else []
    cliente_ids = list({pc["cliente_id"] for pc in pcs})
    clientes = client.select("clientes", filters=[("id", "in", cliente_ids)]) if cliente_ids else []
    cliente_map = {c["id"]: c for c in clientes or []}

    vendas = client.select(
        "vendas",
        filters=[("pacote_id", "in", pacote_ids)],
    ) if pacote_ids else []
    venda_by_pc = {v["pacote_cliente_id"]: v for v in vendas or [] if v.get("pacote_cliente_id")}
    venda_ids = [v["id"] for v in vendas or []]
    pags = client.select(
        "pagamentos",
        filters=[("venda_id", "in", venda_ids)],
    ) if venda_ids else []
    pag_by_venda = {p["venda_id"]: p for p in pags or []}

    pcs_by_pacote: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pc in pcs or []:
        pcs_by_pacote[pc["pacote_id"]].append(pc)

    pags_by_pacote: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in vendas or []:
        pag = pag_by_venda.get(v["id"])
        if pag:
            pags_by_pacote[v["pacote_id"]].append(pag)

    pacotes_out: List[Dict[str, Any]] = []
    for pk in pacotes:
        pkg_pags = pags_by_pacote.get(pk["id"], [])
        pk_pcs = pcs_by_pacote.get(pk["id"], [])
        state = _derive_state(pk, pkg_pags, pk_pcs)
        clientes_detail = []
        for pc in pcs_by_pacote.get(pk["id"], []):
            c = cliente_map.get(pc["cliente_id"], {})
            venda = venda_by_pc.get(pc["id"])
            pag = pag_by_venda.get(venda["id"]) if venda else None
            client_state = _derive_client_state(pk, pc, pag)
            clientes_detail.append({
                "cliente_id": pc["cliente_id"],
                "nome": c.get("nome"),
                "celular": c.get("celular"),
                "qty": pc.get("qty"),
                "total_amount": pc.get("total_amount"),
                "venda_status": venda.get("status") if venda else None,
                "pagamento_status": pag.get("status") if pag else None,
                "paid_at": pag.get("paid_at") if pag else None,
                "state": client_state or state,
            })
        clientes_detail.sort(key=lambda r: (r.get("nome") or "").lower())
        pacotes_out.append({
            "id": pk["id"],
            "sequence_no": pk.get("sequence_no"),
            "friendly_id": pk.get("friendly_id"),
            "status": pk.get("status"),
            "state": state,
            "total_qty": pk.get("total_qty") or 0,
            "capacidade_total": pk.get("capacidade_total") or 24,
            "participants_count": pk.get("participants_count") or len(clientes_detail),
            "opened_at": pk.get("opened_at") or pk.get("created_at"),
            "closed_at": pk.get("closed_at"),
            "approved_at": pk.get("approved_at"),
            "shipped_at": pk.get("shipped_at"),
            "cancelled_at": pk.get("cancelled_at"),
            "clientes": clientes_detail,
        })

    # Contadores agregados pra header da enquete.
    status_counts: Dict[str, int] = {}
    for pk in pacotes:
        s = (pk.get("status") or "").lower() or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    drive_id = enq.get("drive_file_id") or (prod.get("drive_file_id") if prod else None)
    return {
        "id": enq["id"],
        "titulo": enq.get("titulo"),
        "status": enq.get("status"),
        "fornecedor": enq.get("fornecedor"),
        "created_at": enq.get("created_at"),
        "image": f"/files/{drive_id}" if drive_id else None,
        "produto": {
            "id": prod.get("id"),
            "nome": _clean_product_name(prod.get("nome")),
            "valor_unitario": prod.get("valor_unitario"),
        } if prod else None,
        "pacotes_total": len(pacotes),
        "pacotes_fechados": status_counts.get("closed", 0) + status_counts.get("approved", 0),
        "pacotes_by_status": {
            "open": status_counts.get("open", 0),
            "closed": status_counts.get("closed", 0),
            "approved": status_counts.get("approved", 0),
            "cancelled": status_counts.get("cancelled", 0),
        },
        "pacotes": pacotes_out,
    }


@router.post("/enquetes/{enquete_id}/votos")
def add_voto_manual(enquete_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Adiciona voto manualmente a uma enquete. synthetic=1 para auditoria."""
    VALID_QTY = ALLOWED_QTY - {0}
    qty = body.get("qty")
    busca = (body.get("busca") or "").strip()
    nome = (body.get("nome") or "").strip()
    celular = (body.get("celular") or "").strip()

    if qty not in VALID_QTY:
        raise HTTPException(400, f"qty deve ser um de: {sorted(VALID_QTY)}")
    if not busca:
        raise HTTPException(400, "busca é obrigatório")

    client = SupabaseRestClient.from_settings()

    enq = client.select("enquetes", filters=[("id", "eq", enquete_id)], single=True)
    if not enq:
        raise HTTPException(404, "Enquete não encontrada")

    from app.services.portal_service import _normalize_phone, _phone_variants
    from app.services.whatsapp_domain_service import _sanitize_name

    all_clientes = client.select("clientes") or []
    busca_lower = busca.lower()
    normalized_busca = _normalize_phone(busca)
    phone_variants = set(_phone_variants(normalized_busca)) if normalized_busca else set()

    cliente = None
    for c in all_clientes:
        c_phone = _normalize_phone(c.get("celular") or "")
        c_nome = (c.get("nome") or "").lower()
        if (phone_variants and c_phone in phone_variants) or busca_lower in c_nome:
            cliente = c
            break

    if not cliente:
        if not nome or not celular:
            return {"found": False}
        new_cli = client.insert("clientes", {
            "nome": _sanitize_name(nome),
            "celular": _normalize_phone(celular),
        })
        cliente = new_cli[0] if isinstance(new_cli, list) else new_cli

    now = client.now_iso()
    existing_voto = client.select(
        "votos",
        filters=[("enquete_id", "eq", enquete_id), ("cliente_id", "eq", cliente["id"])],
        single=True,
    )
    if existing_voto:
        client.update("votos", {
            "qty": qty, "status": "in", "synthetic": 1, "voted_at": now,
        }, filters=[("id", "eq", existing_voto["id"])])
        voto_id = existing_voto["id"]
    else:
        voto_row = client.insert("votos", {
            "enquete_id": enquete_id,
            "cliente_id": cliente["id"],
            "alternativa_id": None,
            "qty": qty,
            "status": "in",
            "synthetic": 1,
            "voted_at": now,
        })
        voto = voto_row[0] if isinstance(voto_row, list) else voto_row
        voto_id = voto["id"]

    package_result = None
    try:
        from app.services.whatsapp_domain_service import PackageService
        package_result = PackageService(client).rebuild_for_poll(enquete_id)
    except Exception:
        logger.exception("rebuild_for_poll falhou após voto manual enquete=%s", enquete_id)

    return {
        "status": "ok",
        "voto_id": voto_id,
        "cliente": {"id": cliente["id"], "nome": cliente.get("nome"), "celular": cliente.get("celular")},
        "package_result": package_result,
    }
