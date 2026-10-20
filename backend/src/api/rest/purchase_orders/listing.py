import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.purchase_orders import listing
from data.clients.db import get_db
from data.models.postgres import User
from schemas.purchase_orders.listing import POListItem, PODetail

# Registered after the other /purchase-orders/... routers: the dynamic /{purchase_order_id}
# path must not shadow /purchase-orders/reviews and /purchase-orders/mismatch-reviews.
router = APIRouter(prefix="/purchase-orders", tags=["purchase orders"])


@router.get(
    "",
    response_model=list[POListItem],
    summary="List purchase orders",
    description=(
        "Requires invoices.view. Created purchase orders, newest first. `fulfillment` says how "
        "much has been invoiced so far (Not invoiced / Partly invoiced / Fully invoiced / "
        "Over-invoiced); `invoices_on_hold` counts linked invoices that do not match the PO. "
        "Filter by `customer_id`, or `search` a PO number (contains, case-insensitive)."
    ),
)
async def list_purchase_orders(
    customer_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> list[POListItem]:
    return await listing.list_purchase_orders(
        db, customer_id=customer_id, search=search, limit=limit, offset=offset
    )


@router.get(
    "/{purchase_order_id}",
    response_model=PODetail,
    summary="One purchase order",
    description=(
        "Requires invoices.view. Each line with the quantity ordered, invoiced so far and "
        "remaining (negative after an approved override), plus every linked invoice with how "
        "it was linked, whether it is on hold, and the quantity it consumed."
    ),
)
async def purchase_order_detail(
    purchase_order_id: uuid.UUID,
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> PODetail:
    return await listing.get_purchase_order(db, purchase_order_id)
