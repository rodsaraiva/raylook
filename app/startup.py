import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.locks import refresh_lock, packages_lock

logger = logging.getLogger("raylook.startup")

def init_app(app: FastAPI) -> None:
    """
    Register startup/shutdown events and initialize shared resources using lifespan.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        app.state.refresh_lock = refresh_lock
        app.state.packages_lock = packages_lock
        logger.info("App startup: locks initialized")

        from app.services.payment_sync_service import start_payment_sync_scheduler
        asyncio.create_task(start_payment_sync_scheduler(interval_minutes=15))
        logger.info("App startup: agendador de sincronização de pagamentos iniciado")
        
        from app.services.payment_queue_service import start_payment_queue_worker
        await start_payment_queue_worker()
        logger.info("App startup: worker da fila de pagamentos iniciado")

        # Manutenção de PDFs (Sanitização de nomes de arquivos antigos)
        try:
            from app.services.pdf_maintenance_service import run_maintenance
            # Executa em uma thread separada para não bloquear o startup
            await asyncio.to_thread(run_maintenance)
            logger.info("App startup: manutenção de PDFs concluída")
        except Exception as e:
            logger.error(f"App startup: erro na manutenção de PDFs: {e}")
        
        yield # Run application
        
        # Shutdown
        try:
            from app.services.payment_queue_service import stop_payment_queue_worker
            await stop_payment_queue_worker()
        except Exception:
            logger.exception("Erro ao encerrar worker da fila de pagamentos")
        logger.info("App shutdown")

    app.router.lifespan_context = lifespan


