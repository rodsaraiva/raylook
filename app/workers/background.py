import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.config import settings
from app.services.package_state_service import update_package_state, update_vote_state
from app.locks import finance_lock

logger = logging.getLogger("raylook.workers")

async def pdf_worker(package_snapshot: Dict[str, Any]) -> None:
    pkg_id = package_snapshot.get("id")
    logger.info("PDF worker started for pkg=%s", pkg_id)
    try:
        from estoque.pdf_builder import build_pdf
        from finance.utils import get_pdf_filename_by_id

        pdf_bytes = await asyncio.to_thread(build_pdf, package_snapshot, settings.COMMISSION_PERCENT)

        filename = get_pdf_filename_by_id(pkg_id)

        out_dir = Path("etiquetas_estoque").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        local_path = out_dir / filename
        with open(local_path, "wb") as f:
            f.write(pdf_bytes)
        logger.info("PDF salvo: %s", local_path)

        # Envio pro WhatsApp do estoque removido: PDF fica disponível só
        # via endpoint /api/packages/{pkg_id}/pdf no dashboard.
        update_package_state(pkg_id, {
            "pdf_file_name": filename,
            "pdf_status": "sent",
            "pdf_sent_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.error("ERRO no PDF worker pkg=%s: %s", pkg_id, e, exc_info=True)
        try:
            update_package_state(pkg_id, {"pdf_status": "failed"})
        except Exception:
            pass

async def payments_worker(package_snapshot: Dict[str, Any], concurrency: int = 5) -> None:
    """F-040: usa Asaas em vez de Mercado Pago.

    Fluxo:
      1. Instancia AsaasClient
      2. Pra cada voto do pacote:
         a. create_customer (find or create por phone)
         b. create_payment_pix (amount, due_date, description, customer_id)
         c. update_vote_state com asaas_payment_id (que grava em
            pagamentos.provider_payment_id via package_state_service)
         d. enqueue_whatsapp_job pra fila que manda a mensagem (throttled)
    """
    pkg_id = package_snapshot.get("id")
    logger.info("Payments worker started for pkg=%s (provider=asaas)", pkg_id)
    try:
        from integrations.asaas.client import AsaasClient

        asaas = None
        try:
            asaas = AsaasClient()
        except Exception as e:
            logger.error("Falha ao instanciar AsaasClient: %s", e)
            return

        votes_local = package_snapshot.get("votes", []) or []
        sem = asyncio.Semaphore(concurrency)

        from app.services.customer_service import get_customer_name

        async def create_payment(idx, vote):
            async with sem:
                if vote.get("asaas_payment_id") or vote.get("mercadopago_payment_id"):
                    existing_id = vote.get("asaas_payment_id") or vote.get("mercadopago_payment_id")
                    logger.info("Payment already exists for vote idx=%s id=%s", idx, existing_id)
                    return {"status": "exists"}
                try:
                    import re
                    from app.config import settings
                    from finance.utils import resolve_unit_price
                    from finance.manager import FinanceManager
                    from app.services.baserow_lookup import (
                        poll_id_from_package_snapshot,
                        get_poll_data_by_poll_id,
                        get_latest_vote_qty,
                    )

                    poll_id = poll_id_from_package_snapshot(package_snapshot) or ""
                    poll_title = package_snapshot.get("poll_title", "Pedido")
                    valor_col = None
                    if poll_id:
                        poll_data = get_poll_data_by_poll_id(poll_id)
                        if poll_data:
                            poll_title = poll_data.get("title") or poll_title
                            valor_col = poll_data.get("valor")

                    # F-040 fix: preferir valores já persistidos em
                    # pacote_clientes/snapshot (total_amount, subtotal,
                    # unit_price) em vez de recalcular a partir do poll_title.
                    # Se o título foi editado pra algo sem "$XX,XX" (ex: 'teste'),
                    # extract_price() retorna 0 e o Asaas rejeita com "value
                    # deve ser informado". Os valores corretos já estão no
                    # snapshot via clients_by_package.
                    vote_phone = vote.get("phone") or vote.get("voterPhone") or ""
                    snapshot_total = float(vote.get("total_amount") or vote.get("total") or 0)
                    snapshot_subtotal = float(vote.get("subtotal") or 0)
                    snapshot_unit = float(vote.get("unit_price") or 0)

                    qty = get_latest_vote_qty(poll_id, vote_phone) if poll_id else None
                    if qty is None:
                        qty = int(float(vote.get("qty", 0) or 0))

                    if snapshot_total > 0:
                        total_with_comm = round(snapshot_total, 2)
                        subtotal = snapshot_subtotal or round(total_with_comm / (1 + (settings.COMMISSION_PERCENT / 100)), 2)
                        unit_price = snapshot_unit or (subtotal / qty if qty > 0 else 0)
                    else:
                        # Fallback: recalcula a partir do título (fluxo antigo)
                        unit_price = resolve_unit_price(poll_title, valor_col)
                        subtotal = qty * unit_price
                        total_with_comm = round(subtotal * (1 + (settings.COMMISSION_PERCENT / 100)), 2)

                    if total_with_comm <= 0:
                        raise Exception(
                            f"Impossível calcular total da cobrança para pkg={pkg_id} vote_idx={idx}: "
                            f"snapshot_total={snapshot_total} unit_price={unit_price} qty={qty}"
                        )

                    # Item name: remover preço e emojis
                    item_name = poll_title
                    item_name = re.sub(r"(?:R\$\s*|\$\s*)?\d+(?:[.,]\d{1,2})?", "", item_name)
                    item_name = re.sub(r"[🔥🎯📦💕✨✅💰👇]", "", item_name)
                    item_name = re.sub(r"\s+", " ", item_name).strip()
                    if not item_name:
                        item_name = "Peça"

                    due_date_obj = datetime.utcnow() + __import__("datetime").timedelta(days=7)
                    # Asaas usa formato yyyy-mm-dd no dueDate
                    due = due_date_obj.strftime("%Y-%m-%d")

                    customer_name = vote.get("name")
                    if not customer_name or str(customer_name).strip() == "":
                        if vote_phone:
                            customer_name = get_customer_name(vote_phone, "Cliente Genérico")
                        else:
                            customer_name = "Cliente"
                    customer_name = re.sub(r"[^\w\s]", "", str(customer_name)).strip()
                    if not customer_name:
                        customer_name = "Cliente"

                    # F-040 — Asaas: create_customer + create_payment_pix
                    # O customer_id do Asaas é buscado/criado por cpfCnpj,
                    # mas como não temos CPF real no staging, usamos o default
                    # (24971563792) do AsaasClient. Em produção, passar o
                    # CPF real se disponível.
                    customer = await asyncio.to_thread(
                        asaas.create_customer,
                        name=customer_name,
                        phone=vote_phone or "",
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

                    # Grava no banco via update_vote_state (F-036): o service
                    # detecta asaas_payment_id e grava em
                    # pagamentos.provider_payment_id + provider='asaas'.
                    update_vote_state(pkg_id, idx, {
                        "asaas_customer_id": asaas_customer_id,
                        "asaas_payment_id": pid,
                        "asaas_payment_status": "created" if pid else "failed",
                        "asaas_payment_attempts": 1,
                        "asaas_payment_payload": payment
                    })
                    logger.info("Asaas payment created for vote idx=%s payment_id=%s", idx, pid)

                    # Envio de WhatsApp removido. Cobrança fica disponível
                    # exclusivamente via /portal/pedidos; status vai pra "paid"
                    # quando o webhook do Asaas confirmar o pagamento.
                    return {"status": "created", "payment_id": pid}
                except Exception as e:
                    logger.error("Erro criando pagamento para vote idx=%s: %s", idx, e, exc_info=True)
                    try:
                        update_vote_state(pkg_id, idx, {
                            "asaas_payment_status": "failed",
                            "asaas_payment_error": str(e)
                        })
                    except Exception:
                        pass
                    return {"status": "error", "error": str(e)}

        payment_tasks = [asyncio.create_task(create_payment(i, v)) for i, v in enumerate(votes_local)]
        results = []
        for fut in asyncio.as_completed(payment_tasks):
            results.append(await fut)

        success_count = sum(1 for r in results if r.get("status") in ("created", "exists"))
        
        if len(votes_local) > 0 and success_count == 0:
            logger.error(
                "Falha total nos pagamentos do pacote %s. NÃO revertendo — "
                "o pacote permanece approved no banco Supabase mas o operador "
                "precisa investigar por que a integração Asaas falhou e usar "
                "o botão 'Reenviar' no dash após resolver.",
                pkg_id,
            )

        logger.info("Payments worker finished for pkg=%s", pkg_id)
    except Exception as e:
        logger.error("ERRO no payments worker pkg=%s: %s", pkg_id, e, exc_info=True)

