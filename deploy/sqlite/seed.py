"""Popula o SQLite do raylook com dados fictícios pra a UI ter algo visível.

Uso:
    cd /root/rodrigo/raylook && .venv/bin/python deploy/sqlite/seed.py

Recria do zero: apaga o arquivo .db antes de popular. Safe em dev.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

from app.services.sqlite_service import SQLiteRestClient, _default_db_path


COMMISSION_PER_PIECE = 5.0


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _compute_amounts(unit_price: float, qty: int) -> Dict[str, float]:
    subtotal = round(unit_price * qty, 2)
    commission = round(qty * COMMISSION_PER_PIECE, 2)
    return {
        "unit_price": float(unit_price),
        "subtotal": subtotal,
        "commission_percent": 0,
        "commission_amount": commission,
        "total_amount": round(subtotal + commission, 2),
    }


def _make_votes(
    client: SQLiteRestClient,
    enquete: Dict,
    alternativas_by_qty: Dict[int, Dict],
    voters: List[Tuple[Dict, int]],
    voted_at: datetime,
    status: str = "in",
) -> List[Tuple[Dict, Dict, int]]:
    """Cria votos e retorna lista (voto_row, cliente, qty)."""
    rows = []
    for cliente, qty in voters:
        alt = alternativas_by_qty[qty]
        voto = client.insert(
            "votos",
            {
                "enquete_id": enquete["id"],
                "cliente_id": cliente["id"],
                "alternativa_id": alt["id"],
                "qty": qty,
                "status": status,
                "voted_at": _iso(voted_at),
            },
        )[0]
        rows.append((voto, cliente, qty))
    return rows


def _close_pacote(
    client: SQLiteRestClient,
    enquete: Dict,
    produto: Dict,
    voto_rows: List[Tuple[Dict, Dict, int]],
    opened_at: datetime,
    closed_at: datetime,
) -> str:
    """Roda close_package RPC com os votos fornecidos e retorna o pacote_id."""
    votes_payload = []
    for voto, cliente, qty in voto_rows:
        amounts = _compute_amounts(float(produto["valor_unitario"]), qty)
        votes_payload.append(
            {
                "vote_id": voto["id"],
                "cliente_id": cliente["id"],
                "qty": qty,
                **amounts,
            }
        )
    result = client.rpc(
        "close_package",
        {
            "p_enquete_id": enquete["id"],
            "p_produto_id": produto["id"],
            "p_votes": votes_payload,
            "p_opened_at": _iso(opened_at),
            "p_closed_at": _iso(closed_at),
            "p_capacidade_total": 24,
            "p_total_qty": sum(v["qty"] for v in votes_payload),
        },
    )
    return result["pacote_id"]


def _add_vendas_pagamentos(
    client: SQLiteRestClient,
    pacote_id: str,
    produto: Dict,
    pagamento_statuses: List[str],
    providers: List[str],
    sold_at: datetime,
) -> None:
    """Cria venda+pagamento pra cada pacote_cliente. Use None em pagamento_status pra pular."""
    pacs = client.select(
        "pacote_clientes",
        filters=[("pacote_id", "eq", pacote_id)],
        order="created_at.asc",
    )
    for idx, pc in enumerate(pacs):
        status_pag = pagamento_statuses[idx] if idx < len(pagamento_statuses) else None
        provider = providers[idx] if idx < len(providers) else "asaas"
        venda = client.insert(
            "vendas",
            {
                "pacote_id": pacote_id,
                "cliente_id": pc["cliente_id"],
                "produto_id": produto["id"],
                "pacote_cliente_id": pc["id"],
                "qty": pc["qty"],
                "unit_price": pc["unit_price"],
                "subtotal": pc["subtotal"],
                "commission_percent": pc["commission_percent"],
                "commission_amount": pc["commission_amount"],
                "total_amount": pc["total_amount"],
                "status": "approved" if status_pag else "pending",
                "sold_at": _iso(sold_at),
            },
        )[0]
        if status_pag is None:
            continue
        paid_at = _iso(sold_at + timedelta(hours=2)) if status_pag == "paid" else None
        client.insert(
            "pagamentos",
            {
                "venda_id": venda["id"],
                "provider": provider,
                "provider_customer_id": f"cus_demo_{venda['id'][:8]}",
                # IDs de payment: só colocamos pra status "paid" (finalizados);
                # pros demais deixamos null pra não poluir o log com tentativas
                # de sync do Asaas/MP contra IDs inexistentes.
                "provider_payment_id": f"pay_demo_{venda['id'][:8]}" if status_pag == "paid" else None,
                "payment_link": f"https://exemplo.dev/pay/{venda['id']}",
                "pix_payload": "00020101021...PIX-FAKE",
                "due_date": (sold_at + timedelta(days=3)).date().isoformat(),
                "paid_at": paid_at,
                "status": status_pag,
                "payload_json": {"demo": True},
            },
        )


def main() -> None:
    db_path = _default_db_path()
    if os.path.exists(db_path):
        print(f"Removendo DB existente em {db_path}")
        os.remove(db_path)

    client = SQLiteRestClient(db_path=db_path)
    print(f"DB criado em {db_path}\n")

    now = datetime.now(timezone.utc)

    # ========================================================
    # Produtos
    # ========================================================
    produtos_payload = [
        {"nome": "Blusa cetim lisa", "descricao": "PMG", "tamanho": "PMG", "valor_unitario": 33.00},
        {"nome": "Conjunto estruturado", "descricao": "PMG", "tamanho": "PMG", "valor_unitario": 79.00},
        {"nome": "Vestido midi", "descricao": "Cores variadas", "tamanho": "PMGG", "valor_unitario": 129.00},
        {"nome": "Saia pregueada", "descricao": "Moda atual", "tamanho": "PMG", "valor_unitario": 89.00},
        {"nome": "Calça wide leg", "descricao": "Lançamento", "tamanho": "PMGG", "valor_unitario": 149.00},
    ]
    produtos = [client.insert("produtos", p)[0] for p in produtos_payload]
    print(f"Produtos:  {len(produtos)}")

    # ========================================================
    # Clientes (15 — pra ter variedade entre pacotes)
    # ========================================================
    nomes = [
        "Ana Beatriz", "Carla Santos", "Daniela Lima", "Eduarda Ferreira",
        "Fernanda Costa", "Gabriela Rocha", "Helena Martins", "Isabela Souza",
        "Juliana Pereira", "Kelly Oliveira", "Larissa Andrade", "Mariana Silva",
        "Natália Borges", "Olivia Carvalho", "Patricia Mendes",
    ]
    clientes = []
    for i, nome in enumerate(nomes, start=1):
        celular = f"5562988{str(100000 + i).zfill(6)}"
        primeiro = nome.split()[0].lower()
        row = client.insert(
            "clientes",
            {"nome": nome, "celular": celular, "email": f"{primeiro}@exemplo.com"},
        )[0]
        clientes.append(row)
    print(f"Clientes:  {len(clientes)}")

    # ========================================================
    # Enquetes
    # ========================================================
    enquetes_payload = [
        # Enquete 1: abertíssima, só votos ainda
        {
            "external_poll_id": "POLL-DEMO-001",
            "provider": "whapi",
            "chat_id": "120363000000000001@g.us",
            "produto_id": produtos[0]["id"],
            "titulo": "Blusa cetim lisa PMG — R$33,00",
            "status": "open",
            "created_at_provider": _iso(now - timedelta(hours=4)),
        },
        # Enquete 2: aberta mas com 2 pacotes já fechados
        {
            "external_poll_id": "POLL-DEMO-002",
            "provider": "whapi",
            "chat_id": "120363000000000001@g.us",
            "produto_id": produtos[1]["id"],
            "titulo": "Conjunto estruturado PMG — R$79,00",
            "status": "open",
            "created_at_provider": _iso(now - timedelta(days=1)),
        },
        # Enquete 3: fechada com 1 pacote approved (histórico)
        {
            "external_poll_id": "POLL-DEMO-003",
            "provider": "whapi",
            "chat_id": "120363000000000001@g.us",
            "produto_id": produtos[2]["id"],
            "titulo": "Vestido midi — R$129,00",
            "status": "closed",
            "created_at_provider": _iso(now - timedelta(days=3)),
        },
        # Enquete 4: aberta há pouco, só 1 pacote fechado aguardando aprovação
        {
            "external_poll_id": "POLL-DEMO-004",
            "provider": "whapi",
            "chat_id": "120363000000000001@g.us",
            "produto_id": produtos[3]["id"],
            "titulo": "Saia pregueada — R$89,00",
            "status": "open",
            "created_at_provider": _iso(now - timedelta(hours=8)),
        },
    ]
    enquetes = [client.insert("enquetes", e)[0] for e in enquetes_payload]
    print(f"Enquetes:  {len(enquetes)}")

    # Alternativas 3/6/9/12 pra cada enquete
    alternativas_por_enquete: Dict[str, Dict[int, Dict]] = {}
    for enq in enquetes:
        alts = {}
        for pos, qty in enumerate([3, 6, 9, 12]):
            row = client.insert(
                "enquete_alternativas",
                {
                    "enquete_id": enq["id"],
                    "option_external_id": f"{enq['external_poll_id']}-opt-{qty}",
                    "label": f"{qty} peças",
                    "qty": qty,
                    "position": pos,
                },
            )[0]
            alts[qty] = row
        alternativas_por_enquete[enq["id"]] = alts

    # ========================================================
    # Enquete 1 (Blusa cetim, R$33) — 2 pacotes + votos soltos
    # ========================================================
    enq = enquetes[0]
    produto = produtos[0]
    alts = alternativas_por_enquete[enq["id"]]

    # Pacote A: closed, aguardando aprovação do gerente (sem vendas ainda)
    votos_a = _make_votes(
        client, enq, alts,
        [(clientes[3], 6), (clientes[4], 6), (clientes[5], 6), (clientes[6], 6)],
        now - timedelta(hours=3, minutes=30),
    )
    pacote_a = _close_pacote(client, enq, produto, votos_a,
                             opened_at=now - timedelta(hours=4),
                             closed_at=now - timedelta(hours=3))
    # Fica como 'closed' (default do close_package) — sem approved_at, sem vendas.

    # Pacote B: approved com vendas (paid/sent/created)
    votos_b = _make_votes(
        client, enq, alts,
        [(clientes[7], 12), (clientes[8], 9), (clientes[9], 3)],
        now - timedelta(hours=2, minutes=30),
    )
    pacote_b = _close_pacote(client, enq, produto, votos_b,
                             opened_at=now - timedelta(hours=3),
                             closed_at=now - timedelta(hours=2))
    client.update("pacotes", {"status": "approved", "approved_at": _iso(now - timedelta(hours=1, minutes=30))},
                  filters=[("id", "eq", pacote_b)])
    _add_vendas_pagamentos(client, pacote_b, produto,
                           pagamento_statuses=["paid", "sent", "created"],
                           providers=["asaas", "asaas", "mercadopago"],
                           sold_at=now - timedelta(hours=1, minutes=30))

    # Pacote C-open: status='open', acumulando votos (18 de 24). Usado pra
    # testar a coluna "Em aberto" do dashboard — precisa de opened_at recente
    # (<72h) + votos status='in' na enquete pra aparecer no open_votes_by_poll.
    votos_cop = _make_votes(
        client, enq, alts,
        [(clientes[10], 3), (clientes[11], 6), (clientes[12], 9)],
        now - timedelta(minutes=45),
        status="in",
    )
    pacote_cop = client.insert(
        "pacotes",
        {
            "enquete_id": enq["id"],
            "sequence_no": client.rpc("next_pacote_sequence", {"p_enquete_id": enq["id"]}),
            "capacidade_total": 24,
            "total_qty": sum(v[2] for v in votos_cop),
            "participants_count": len(votos_cop),
            "status": "open",
            "opened_at": _iso(now - timedelta(minutes=50)),
        },
    )[0]

    print("Enquete 1 (Blusa cetim):    3 pacotes (1 open, 1 closed, 1 approved)")

    # ========================================================
    # Enquete 2 (Conjunto estruturado, R$79) — 2 pacotes (1 approved, 1 cancelled)
    # ========================================================
    enq = enquetes[1]
    produto = produtos[1]
    alts = alternativas_por_enquete[enq["id"]]

    # Pacote C: approved, todas as vendas PAGAS
    votos_c = _make_votes(
        client, enq, alts,
        [(clientes[0], 12), (clientes[1], 9), (clientes[2], 3)],
        now - timedelta(days=1, hours=2),
    )
    pacote_c = _close_pacote(client, enq, produto, votos_c,
                             opened_at=now - timedelta(days=1, hours=3),
                             closed_at=now - timedelta(days=1, hours=1))
    # Pacote C: aprovado + todos pagos + PDF de etiqueta enviado ao estoque
    # → vira estado "separado" no fluxo de 6 etapas (aguardando despacho).
    client.update(
        "pacotes",
        {
            "status": "approved",
            "approved_at": _iso(now - timedelta(hours=20)),
            "pdf_sent_at": _iso(now - timedelta(hours=18)),
            "pdf_status": "sent",
            "pdf_file_name": "conjunto-estruturado-01.pdf",
        },
        filters=[("id", "eq", pacote_c)],
    )
    _add_vendas_pagamentos(client, pacote_c, produto,
                           pagamento_statuses=["paid", "paid", "paid"],
                           providers=["asaas", "mercadopago", "asaas"],
                           sold_at=now - timedelta(hours=20))

    # Pacote D: cancelado (ex: gerente rejeitou)
    votos_d = _make_votes(
        client, enq, alts,
        [(clientes[5], 12), (clientes[6], 12)],
        now - timedelta(hours=18),
    )
    pacote_d = _close_pacote(client, enq, produto, votos_d,
                             opened_at=now - timedelta(hours=19),
                             closed_at=now - timedelta(hours=17))
    client.update(
        "pacotes",
        {
            "status": "cancelled",
            "cancelled_at": _iso(now - timedelta(hours=16)),
            "cancelled_by": "gerente@exemplo.com",
        },
        filters=[("id", "eq", pacote_d)],
    )

    # Pacote E-open: status='open' na enquete 2, acumulando 18/24.
    votos_eop = _make_votes(
        client, enq, alts,
        [(clientes[13], 6), (clientes[14], 12)],
        now - timedelta(minutes=20),
        status="in",
    )
    client.insert(
        "pacotes",
        {
            "enquete_id": enq["id"],
            "sequence_no": client.rpc("next_pacote_sequence", {"p_enquete_id": enq["id"]}),
            "capacidade_total": 24,
            "total_qty": sum(v[2] for v in votos_eop),
            "participants_count": len(votos_eop),
            "status": "open",
            "opened_at": _iso(now - timedelta(minutes=25)),
        },
    )

    print("Enquete 2 (Conjunto):       3 pacotes (1 open, 1 approved, 1 cancelled)")

    # ========================================================
    # Enquete 3 (Vestido midi, R$129) — 1 pacote approved (histórico)
    # ========================================================
    enq = enquetes[2]
    produto = produtos[2]
    alts = alternativas_por_enquete[enq["id"]]

    votos_e = _make_votes(
        client, enq, alts,
        [(clientes[0], 12), (clientes[1], 9), (clientes[2], 3)],
        now - timedelta(days=2, hours=4),
    )
    pacote_e = _close_pacote(client, enq, produto, votos_e,
                             opened_at=now - timedelta(days=2, hours=6),
                             closed_at=now - timedelta(days=2, hours=3))
    # Pacote E: histórico — aprovado + todos pagos + PDF enviado + despachado.
    # → vira estado "enviado" (última etapa do fluxo).
    client.update(
        "pacotes",
        {
            "status": "approved",
            "approved_at": _iso(now - timedelta(days=2, hours=2)),
            "pdf_sent_at": _iso(now - timedelta(days=2, hours=1)),
            "pdf_status": "sent",
            "pdf_file_name": "vestido-midi-01.pdf",
            "shipped_at": _iso(now - timedelta(days=1, hours=8)),
            "shipped_by": "estoque@exemplo.com",
        },
        filters=[("id", "eq", pacote_e)],
    )
    _add_vendas_pagamentos(client, pacote_e, produto,
                           pagamento_statuses=["paid", "paid", "paid"],
                           providers=["asaas", "asaas", "asaas"],
                           sold_at=now - timedelta(days=2, hours=2))

    # Pacote H: aprovado + todos pagos MAS sem pdf_sent_at ainda
    # → estado "pendente" (pagamentos realizados, aguardando separação no estoque)
    votos_h = _make_votes(
        client, enq, alts,
        [(clientes[3], 12), (clientes[4], 9), (clientes[5], 3)],
        now - timedelta(hours=10),
    )
    pacote_h = _close_pacote(client, enq, produto, votos_h,
                             opened_at=now - timedelta(hours=12),
                             closed_at=now - timedelta(hours=9))
    client.update("pacotes",
                  {"status": "approved", "approved_at": _iso(now - timedelta(hours=8))},
                  filters=[("id", "eq", pacote_h)])
    _add_vendas_pagamentos(client, pacote_h, produto,
                           pagamento_statuses=["paid", "paid", "paid"],
                           providers=["asaas", "asaas", "mercadopago"],
                           sold_at=now - timedelta(hours=7))

    print("Enquete 3 (Vestido):        2 pacotes (1 enviado, 1 pendente sem separar)")

    # ========================================================
    # Enquete 4 (Saia pregueada, R$89) — 1 pacote closed aguardando
    # ========================================================
    enq = enquetes[3]
    produto = produtos[3]
    alts = alternativas_por_enquete[enq["id"]]

    votos_f = _make_votes(
        client, enq, alts,
        [(clientes[3], 9), (clientes[4], 6), (clientes[5], 6), (clientes[6], 3)],
        now - timedelta(hours=5),
    )
    _close_pacote(client, enq, produto, votos_f,
                  opened_at=now - timedelta(hours=7),
                  closed_at=now - timedelta(hours=4))
    # Fica em 'closed' — sem approved nem vendas ainda

    votos_gop = _make_votes(
        client, enq, alts,
        [(clientes[7], 3), (clientes[8], 6)],
        now - timedelta(minutes=10),
        status="in",
    )
    client.insert(
        "pacotes",
        {
            "enquete_id": enq["id"],
            "sequence_no": client.rpc("next_pacote_sequence", {"p_enquete_id": enq["id"]}),
            "capacidade_total": 24,
            "total_qty": sum(v[2] for v in votos_gop),
            "participants_count": len(votos_gop),
            "status": "open",
            "opened_at": _iso(now - timedelta(minutes=12)),
        },
    )

    print("Enquete 4 (Saia):           2 pacotes (1 open, 1 closed aguardando)")

    # ========================================================
    # Resumo
    # ========================================================
    print("\n=== Resumo do seed ===")
    totals = {}
    for t in [
        "produtos", "clientes", "enquetes", "enquete_alternativas",
        "votos", "pacotes", "pacote_clientes", "vendas", "pagamentos",
    ]:
        rows = client.select(t) or []
        totals[t] = len(rows)
    for k, v in totals.items():
        print(f"  {k:22s} {v}")

    # Breakdown de status
    print("\n=== Status breakdown ===")
    for status in ("open", "closed", "approved", "cancelled"):
        rows = client.select("pacotes", filters=[("status", "eq", status)]) or []
        print(f"  pacotes.{status:10s} {len(rows)}")
    for status in ("created", "sent", "paid", "failed", "cancelled"):
        rows = client.select("pagamentos", filters=[("status", "eq", status)]) or []
        print(f"  pagamentos.{status:8s} {len(rows)}")


if __name__ == "__main__":
    main()
