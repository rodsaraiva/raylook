import asyncio
import logging
from typing import List
from integrations.mercadopago.client import MercadoPagoClient, MercadoPagoError
from finance.manager import FinanceManager
from app.locks import finance_lock

logger = logging.getLogger("raylook.services.payment_sync")

async def sync_mercadopago_payments():
    """
    Busca todas as cobranças pendentes no payments.json, 
    verifica o status no Mercado Pago e atualiza as pagas.
    """
    async with finance_lock:
        try:
            fm = FinanceManager()
            pending_ids = fm.get_pending_mercadopago_ids()
            
            if not pending_ids:
                logger.debug("Nenhum pagamento pendente para sincronizar.")
                return 0

            logger.info(f"Iniciando sincronização de {len(pending_ids)} pagamentos pendentes...")
            
            mp_client = MercadoPagoClient()
            paid_ids = []

            # Para cada pagamento pendente, verifica no Mercado Pago
            # Nota: Em produção com muitos pagamentos, isso poderia ser otimizado com busca em lote se a API permitir
            for mp_id in pending_ids:
                try:
                    # Executa a chamada de rede em uma thread para não bloquear o loop de eventos
                    payment_data = await asyncio.to_thread(mp_client.get_payment_pix, mp_id)
                    status = payment_data.get("status")
                    
                    if status == "approved":
                        logger.info(f"Pagamento {mp_id} confirmado como aprovado.")
                        paid_ids.append(mp_id)
                    else:
                        logger.debug(f"Pagamento {mp_id} ainda com status: {status}")
                        
                except MercadoPagoError as e:
                    if e.status_code == 404:
                        logger.warning(f"Pagamento {mp_id} não encontrado no Mercado Pago.")
                    else:
                        logger.error(f"Erro ao buscar status do pagamento {mp_id}: {e}")
                except Exception as e:
                    logger.error(f"Erro inesperado ao processar pagamento {mp_id}: {e}")

            if paid_ids:
                updated_count = fm.sync_paid_status(paid_ids)
                logger.info(f"Sincronização concluída: {updated_count} cobranças atualizadas para 'paid'.")
                return updated_count
            
            logger.info("Sincronização concluída: nenhuma nova cobrança paga encontrada.")
            return 0

        except Exception as e:
            logger.error(f"Erro crítico no serviço de sincronização de pagamentos: {e}", exc_info=True)
            return 0

async def start_payment_sync_scheduler(interval_minutes: int = 15):
    """
    Inicia um loop infinito que executa a sincronização a cada X minutos.
    """
    logger.info(f"Agendador de sincronização de pagamentos iniciado (intervalo: {interval_minutes}min).")
    while True:
        await sync_mercadopago_payments()
        await asyncio.sleep(interval_minutes * 60)
