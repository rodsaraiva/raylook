from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.customer_service import list_customer_rows_page, update_customer, search_customers_light


router = APIRouter(prefix="/api/customers")


class CustomerUpdate(BaseModel):
    name: str


class CustomerCreate(BaseModel):
    phone: str
    name: str


@router.get("/")
async def get_customers(
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
):
    """Retorna clientes com estatísticas agregadas e paginação consistente com o dashboard."""
    try:
        return list_customer_rows_page(page=page, page_size=page_size, search=search)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{phone}")
async def patch_customer(phone: str, update: CustomerUpdate):
    """Atualiza o nome de um cliente específico."""
    try:
        update_customer(phone, update.name)
        return {"status": "success", "phone": phone, "name": update.name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/search")
async def search(q: str = "", limit: int = 10):
    return {"results": search_customers_light(q, limit=min(max(limit, 1), 25))}


@router.post("/")
async def create_customer(customer: CustomerCreate):
    """Cria ou atualiza um cliente."""
    try:
        update_customer(customer.phone, customer.name)
        return {"status": "success", "phone": customer.phone, "name": customer.name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
