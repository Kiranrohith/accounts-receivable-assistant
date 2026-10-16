from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.customers.customers import create_customer, list_customers
from data.clients.db import get_db
from data.models.postgres import User
from schemas.customers.customers import CustomerCreate, CustomerOut

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post(
    "",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a customer",
    description="Requires customers.manage. GSTIN, when given, must be unique.",
)
async def create(
    body: CustomerCreate,
    actor: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> CustomerOut:
    return CustomerOut.model_validate(await create_customer(db, actor, body))


@router.get(
    "",
    response_model=list[CustomerOut],
    summary="List customers",
    description="Requires customers.view. Active customers, alphabetical.",
)
async def list_all(
    _: User = Depends(require_permission("customers.view")),
    db: AsyncSession = Depends(get_db),
) -> list[CustomerOut]:
    return [CustomerOut.model_validate(c) for c in await list_customers(db)]
