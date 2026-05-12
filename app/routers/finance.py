from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import defaultdict
import logging

from finance.manager import FinanceManager
from app.services.finance_service import (
    build_receivables_by_client,
    build_aging_summary,
    build_payment_history,
    mark_payment_written_off,
    PaymentNotFound,
)

router = APIRouter(prefix="/api/finance")
logger = logging.getLogger("raylook.routers.finance")

finance_manager = FinanceManager()


@router.get("/charges")
async def get_charges() -> List[Dict[str, Any]]:
    """Retorna todas as cobranças."""
    try:
        charges = finance_manager.list_charges()
        return charges
    except Exception as e:
        logger.exception("Erro ao carregar cobranças")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """Retorna estatísticas agregadas para gráficos."""
    try:
        charges = finance_manager.list_charges()

        # Timeline dos últimos 7 dias
        today = datetime.now().date()
        timeline = {}

        for i in range(6, -1, -1):
            date = today - timedelta(days=i)
            date_str = date.strftime("%d/%m")
            timeline[date_str] = {"created": 0.0, "paid": 0.0}

        for charge in charges:
            created_at = charge.get("created_at", "")
            if created_at:
                try:
                    charge_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
                    date_str = charge_date.strftime("%d/%m")
                    if date_str in timeline:
                        timeline[date_str]["created"] += charge.get("total_amount", 0)
                        if charge.get("status") == "paid":
                            timeline[date_str]["paid"] += charge.get("total_amount", 0)
                except (ValueError, TypeError):
                    pass

        # Totais
        total_pending = sum(c.get("total_amount", 0) for c in charges if c.get("status") == "pending")
        total_paid = sum(c.get("total_amount", 0) for c in charges if c.get("status") == "paid")

        return {
            "timeline": timeline,
            "total_pending": total_pending,
            "total_paid": total_paid,
            "total_charges": len(charges)
        }
    except Exception as e:
        logger.exception("Erro ao calcular estatísticas")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


class WriteOffRequest(BaseModel):
    reason: str


@router.get("/receivables")
async def get_receivables() -> List[Dict[str, Any]]:
    """Contas a receber agregadas por cliente."""
    try:
        return build_receivables_by_client()
    except Exception:
        logger.exception("Erro ao agregar receivables")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.get("/aging-summary")
async def get_aging_summary() -> Dict[str, Any]:
    """KPIs de aging para o topo da aba."""
    try:
        return build_aging_summary()
    except Exception:
        logger.exception("Erro ao construir aging summary")
        return JSONResponse(status_code=500, content={"error": "internal"})


@router.post("/pagamentos/{pagamento_id}/write-off")
async def post_write_off(pagamento_id: str, body: WriteOffRequest) -> Dict[str, Any]:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")
    try:
        return mark_payment_written_off(pagamento_id, reason=reason)
    except PaymentNotFound:
        raise HTTPException(status_code=404, detail="pagamento not found")


@router.get("/pagamentos/{pagamento_id}/history")
async def get_payment_history(pagamento_id: str) -> List[Dict[str, Any]]:
    return build_payment_history(pagamento_id)
