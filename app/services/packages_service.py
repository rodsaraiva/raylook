from typing import Dict, Any, Optional
import logging

from metrics.actions import ConfirmAction, RejectAction, RevertAction
from app.services.metrics_service import load_metrics, save_metrics
from app.services.confirmed_packages_service import add_confirmed_package, get_confirmed_package
from app.services.rejected_packages_service import add_rejected_package
from app.locks import finance_lock
try:
    from finance.manager import FinanceManager
except ImportError:
    FinanceManager = None

logger = logging.getLogger("raylook.packages_service")

async def confirm_package(pkg_id: str, user: Optional[str] = None, tag: Optional[str] = None) -> Dict[str, Any]:
    data = load_metrics()
    try:
        action = ConfirmAction(pkg_id, user)
        data = action.execute(data)
        moved = action.confirmed_pkg
    except KeyError:
        raise KeyError("not_found")
    
    # Salvar o pacote no armazenamento separado e registrar no financeiro
    if moved:
        if tag is not None:
            moved["tag"] = str(tag)
            try:
                from app.services.package_state_service import update_package_state
                update_package_state(pkg_id, {"tag": str(tag)})
            except Exception as e:
                logger.warning("Falha ao persistir tag do pacote %s no state: %s", pkg_id, e)

        # Registrar no financeiro
        if FinanceManager:
            try:
                async with finance_lock:
                    fm = FinanceManager()
                    fm.register_package_confirmation(moved, confirmed_by=user)
            except Exception as e:
                logger.error(f"Falha ao registrar dados financeiros no service para o pacote {pkg_id}: {e}")

        # initialize pdf metadata if moved and not sent
        if moved.get("pdf_status") == "sent":
            logger.info("Pacote %s já com pdf enviado", pkg_id)
        else:
            moved.setdefault("pdf_attempts", 0)
            moved.setdefault("pdf_status", "queued")
            moved.setdefault("pdf_file_name", None)
            
        add_confirmed_package(moved)
        
    save_metrics(data)
    return {"data": data, "moved": moved}

def reject_package(pkg_id: str, user: Optional[str] = None) -> Dict[str, Any]:
    data = load_metrics()
    try:
        action = RejectAction(pkg_id, user)
        data = action.execute(data)
        moved = action.rejected_pkg
    except KeyError:
        raise KeyError("not_found")
    
    if moved:
        add_rejected_package(moved)
        
    save_metrics(data)
    return {"data": data, "moved": moved}

def revert_package(pkg_id: str, user: Optional[str] = None) -> Dict[str, Any]:
    # Como os pacotes confirmados são removidos do dashboard_metrics.json
    # e a nova regra impede reversão, lançamos um erro.
    raise RuntimeError("Reversão não permitida para pacotes confirmados.")

