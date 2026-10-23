import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.purchase_orders import listing
from data.clients.db import get_db
from data.models.postgres import User
from schemas.common import Page
from schemas.purchase_orders.listing import Fulfillment, POListItem, PODetail

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
    "/search",
    response_model=Page[POListItem],
    summary="Purchase orders, a page at a time",
    description=(
        "Requires invoices.view. The same rows as GET /purchase-orders, searched, filtered, sorted and cut to one page by "
        "the server, with the `total` of everything that matches. `q` matches the PO number or the customer's name as "
        "plain text. Filters: customer_id, `fulfillment` (Not invoiced, Partly invoiced, Fully invoiced, Over-invoiced) "
        "and `on_hold` (some linked invoice does not match the PO). `sort` is one of created (default, newest first), "
        "po_number, customer, po_date, amount; `dir` is asc or desc."
    ),
)
async def search_purchase_orders(
    q: str | None = Query(None, max_length=100),
    customer_id: uuid.UUID | None = Query(None),
    fulfillment: Fulfillment | None = Query(None),
    on_hold: bool | None = Query(None),
    sort: Literal["created", "po_number", "customer", "po_date", "amount"] = Query("created"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> Page[POListItem]:
    return await listing.search_purchase_orders(
        db,
        customer_id=customer_id,
        fulfillment=fulfillment,
        on_hold=on_hold,
        search=q,
        sort=sort,
        descending=(dir == "desc") if dir else sort == "created",
        limit=limit,
        offset=offset,
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
