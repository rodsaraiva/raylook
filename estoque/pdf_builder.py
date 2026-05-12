"""
Gerador de PDF de etiquetas usando HTML/CSS (xhtml2pdf).
"""

from __future__ import annotations

import io
import re
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa  # type: ignore

from finance.utils import extract_price, resolve_unit_price

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

def _client_code(phone: str) -> str:
    """Retorna os últimos 4 dígitos do telefone."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-4:] if len(digits) >= 4 else digits or "----"

def _format_phone(phone: str) -> str:
    """Formata telefone para exibição (ex: (62) 99335-3390)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 13 and digits.startswith("55"): # 55 62 99335 3390
        return f"({digits[2:4]}) {digits[4:9]}-{digits[9:]}"
    if len(digits) == 12 and digits.startswith("55"): # 55 62 9335 3390
        return f"({digits[2:4]}) {digits[4:8]}-{digits[8:]}"
    return phone

def _fmt_brl(value: float) -> str:
    """Formata valor em BRL (apenas o número)."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --------------------------------------------------------------------------- #
#  Builder                                                                    #
# --------------------------------------------------------------------------- #

def build_pdf(package: Dict[str, Any], commission_per_piece: float = 5.0) -> bytes:
    """Gera o PDF da etiqueta a partir de um template HTML."""

    # 1. Preparar dados
    poll_title = package.get("poll_title", "Pedido")
    # Título alternativo se vier como ID (correção temporária do bug do dashboard)
    if poll_title and len(poll_title) > 30 and " " not in poll_title:
        poll_title = f"Enquete {poll_title[:10]}..."

    votes = package.get("votes", [])

    # Tag customizada para substituir "peças" no PDF (fallback: "peças")
    raw_tag = package.get("tag")
    pieces_label = "peças"
    if raw_tag is not None:
        # Cobrir casos tipo "None"/"null" vindos de payloads inconsistentes
        s = str(raw_tag).strip()
        if s.lower() in {"none", "null", "undefined"}:
            s = ""
        pieces_label = s or "peças"
    # Evitar injeção de HTML no template (Jinja aqui não usa autoescape)
    pieces_label = html.escape(pieces_label, quote=True)

    valor_col = package.get("valor_col")
    unit_price = resolve_unit_price(poll_title, valor_col)

    # Ordena por quantidade
    sorted_votes = sorted(votes, key=lambda v: v.get("qty", 0), reverse=True)

    processed_votes = []
    for i, v in enumerate(sorted_votes):
        # Robust parsing of quantity (accept "2", "2.0", numeric types, fall back to 0)
        try:
            qty = int(float(v.get("qty", 0) or 0))
        except Exception:
            try:
                qty = int(v.get("qty", 0))
            except Exception:
                qty = 0
        subtotal = qty * float(unit_price or 0.0)
        total_comm = subtotal + qty * float(commission_per_piece)

        processed_votes.append({
            "order_num": i + 1,
            "name": v.get("name") or "Desconhecido",
            "client_code": _client_code(v.get("phone", "")),
            "qty": qty,
            "unit_price_fmt": _fmt_brl(unit_price),
            "subtotal_fmt": _fmt_brl(subtotal),
            "commission_fmt": _fmt_brl(qty * float(commission_per_piece)),
            "total_with_commission_fmt": _fmt_brl(total_comm),
        })

    context = {
        "poll_title": poll_title,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "votes": processed_votes,
        "unit_price": unit_price,
        "commission_per_piece": commission_per_piece,
        "pieces_label": pieces_label,
    }

    # 2. Carregar Template
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("etiqueta.html")
    html_content = template.render(**context)

    # 3. Converter para PDF
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(
        io.BytesIO(html_content.encode("utf-8")),
        dest=pdf_buffer,
        encoding="utf-8"
    )

    if pisa_status.err:
        raise RuntimeError(f"Erro ao gerar PDF: {pisa_status.err}")

    return pdf_buffer.getvalue()
