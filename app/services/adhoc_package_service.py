"""Criação de pacote do zero (adhoc) — sem depender de enquete WHAPI."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from app.config import settings
from app.services.confirmation_pipeline import run_post_confirmation_effects
from app.services.supabase_service import SupabaseRestClient

logger = logging.getLogger("raylook.adhoc_package")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _phantom_poll_title(product_name: str, unit_price: float) -> str:
    # Formato `<nome> R$XX,XX` pra ser compatível com finance.utils.extract_price
    # (usado pelo gerador de etiquetas, que lê o valor do título da enquete).
    price_str = f"{unit_price:.2f}".replace(".", ",")
    return f"{product_name} R${price_str}"


def create_phantom_poll_and_product(
    product_name: str,
    unit_price: float,
    drive_file_id: str,
) -> Tuple[str, str]:
    """Insere produto novo e enquete fantasma. Retorna (produto_id, enquete_id)."""
    client = SupabaseRestClient.from_settings()

    produto = client.insert(
        "produtos",
        {
            "nome": product_name,
            "valor_unitario": unit_price,
            "drive_file_id": drive_file_id,
            "source": "manual",
        },
    )[0]
    produto_id = produto["id"]

    enquete = client.insert(
        "enquetes",
        {
            "titulo": _phantom_poll_title(product_name, unit_price),
            "produto_id": produto_id,
            "drive_file_id": drive_file_id,
            "source": "manual",
            # external_poll_id é NOT NULL + UNIQUE; enquete fantasma não tem
            # pollId do WHAPI, então geramos sintético com prefixo claro.
            "external_poll_id": f"manual_{uuid4().hex}",
            "created_at_provider": _now_iso(),
            # chat_id do grupo oficial pra passar nos filtros do metrics
            # (fetch_enquetes_for_metrics filtra por OFFICIAL_GROUP_CHAT_ID).
            "chat_id": settings.OFFICIAL_GROUP_CHAT_ID,
        },
    )[0]
    enquete_id = enquete["id"]

    logger.info(
        "adhoc: fantasma criada produto_id=%s enquete_id=%s", produto_id, enquete_id
    )
    return produto_id, enquete_id


def _clean_phone(phone: Any) -> str:
    return "".join(filter(str.isdigit, str(phone or "")))


def _total_qty(votes: List[Dict[str, Any]]) -> int:
    return sum(int(v.get("qty") or 0) for v in votes)


def create_adhoc_package(
    *,
    product_name: str,
    unit_price: float,
    drive_file_id: str,
    votes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fluxo completo: produto + enquete fantasma + pacote + votos sintéticos + pacote_clientes + pós-confirmação."""
    total = _total_qty(votes)
    if total != 24:
        raise ValueError(f"Pacote deve ter exatamente 24 peças, recebeu {total}.")

    client = SupabaseRestClient.from_settings()
    produto_id, enquete_id = create_phantom_poll_and_product(
        product_name=product_name,
        unit_price=unit_price,
        drive_file_id=drive_file_id,
    )

    now_iso = _now_iso()
    pacote = client.insert(
        "pacotes",
        {
            "enquete_id": enquete_id,
            "sequence_no": 1,
            "capacidade_total": 24,
            "total_qty": 24,
            "participants_count": len(votes),
            "status": "closed",
            "opened_at": now_iso,
            "closed_at": now_iso,
            "created_via": "adhoc",
        },
    )[0]
    pacote_id = pacote["id"]

    commission_pct = float(settings.COMMISSION_PERCENT)

    for v in votes:
        phone = _clean_phone(v.get("phone"))
        qty = int(v.get("qty") or 0)
        name = (v.get("name") or phone).strip()

        customer = client.upsert_one(
            "clientes",
            {"celular": phone, "nome": name},
            on_conflict="celular",
        )
        cliente_id = customer["id"]

        voto = client.insert(
            "votos",
            {
                "enquete_id": enquete_id,
                "cliente_id": cliente_id,
                "qty": qty,
                "status": "in",
                "voted_at": now_iso,
                "synthetic": True,
            },
        )[0]

        subtotal = round(unit_price * qty, 2)
        commission_amount = round(subtotal * (commission_pct / 100), 2)
        total_amount = round(subtotal + commission_amount, 2)

        client.insert(
            "pacote_clientes",
            {
                "pacote_id": pacote_id,
                "cliente_id": cliente_id,
                "voto_id": voto["id"],
                "produto_id": produto_id,
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "commission_percent": commission_pct,
                "commission_amount": commission_amount,
                "total_amount": total_amount,
                "status": "closed",
            },
        )

    legacy_package_id = f"adhoc_{pacote_id}"
    package_dict = {
        "id": legacy_package_id,
        "poll_title": _phantom_poll_title(product_name, unit_price),
        "valor_col": unit_price,
        "qty": 24,
        "status": "confirmed",
        "manual_creation": True,
        "created_via": "adhoc",
        "confirmed_at": now_iso,
        "closed_at": now_iso,
        "votes": votes,
    }
    try:
        import asyncio
        asyncio.run(
            run_post_confirmation_effects(package_dict, legacy_package_id, metrics_data_to_save=None)
        )
    except Exception:
        logger.exception("adhoc: pipeline pós-confirmação falhou (pacote persistido, efeitos podem ser retentados)")

    return {"package_id": str(pacote_id), "legacy_package_id": legacy_package_id}
