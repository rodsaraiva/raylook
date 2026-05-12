"""Stub do envio de PDF de estoque.

Original mandava o PDF via Evolution API pra um número WhatsApp do estoque.
Em raylook (sandbox), apenas registra que o envio aconteceria.
Quando o WHAPI próprio do raylook for provisionado, este módulo pode ser
reescrito pra chamar a API do canal próprio.
"""
import logging

logger = logging.getLogger("raylook.estoque.sender")


def send_pdf_to_estoque(pdf_bytes: bytes, filename: str = "pedido_estoque.pdf", image_url: str = None) -> None:
    logger.info(
        "[estoque-stub] envio desativado (sandbox): filename=%s size=%d image_url=%s",
        filename,
        len(pdf_bytes or b""),
        image_url,
    )
