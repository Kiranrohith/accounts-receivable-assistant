import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.documents import type_review
from core.services.invoices import listing
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import InvoiceStatus
from schemas.documents.type_review import SendBackResult
from schemas.invoices.listing import InvoiceDetail, InvoiceListItem, RejectedItem

# Registered after every other /invoices/... router: the dynamic /{invoice_id} path must not
# shadow /invoices/uploads, /invoices/reviews, ...
router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get(
    "/rejected",
    response_model=list[RejectedItem],
    summary="Rejected documents, with every reason",
    description=(
        "Requires invoices.rejected.rerun. Newest first. `kind` filters: invoice (default: "
        "invoices rejected for validation or in review), purchase_order, other (documents that "
        "are neither an invoice nor a purchase order), duplicate (an identical file uploaded again), mail_attachment (an e-mail attachment we "
        "could not use; each carries `source_mail_link`) or all. Resolved rejections (sent back "
        "for a type decision) are hidden unless include_resolved=true. `can_send_to_review` "
        "says the uploader may send a 'neither' rejection back for a document-type decision."
    ),
)
async def rejected(
    kind: Literal["invoice", "purchase_order", "other", "duplicate", "mail_attachment", "all"] = Query("invoice"),
    include_resolved: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("invoices.rejected.rerun")),
    db: AsyncSession = Depends(get_db),
) -> list[RejectedItem]:
    return await listing.list_rejected(
        db, kind=kind, include_resolved=include_resolved, limit=limit, offset=offset
    )


@router.post(
    "/rejected/{rejected_id}/send-to-review",
    response_model=SendBackResult,
    summary="Send a 'neither an invoice nor a PO' rejection back for a type decision",
    description=(
        "Requires invoices.rejected.rerun. For a rejected item with `can_send_to_review` true "
        "(the reader was confident the document is neither an invoice nor a purchase order, "
        "and you think it is wrong). Only the person who uploaded the document can do this. The "
        "rejection is marked resolved and a document-type review item opens in "
        "GET /documents/type-reviews, where you pick invoice or PO, or reject it again."
    ),
    responses={
        403: {"description": "Not the uploader"},
        404: {"description": "No such rejected item"},
        409: {"description": "Already resolved"},
        422: {"description": "Not a 'neither' rejection, or the file is no longer available"},
    },
)
async def send_back(
    rejected_id: uuid.UUID,
    user: User = Depends(require_permission("invoices.rejected.rerun")),
    db: AsyncSession = Depends(get_db),
) -> SendBackResult:
    return await type_review.send_back_to_review(db, user, rejected_id)


@router.get(
    "",
    response_model=list[InvoiceListItem],
    summary="Invoices",
    description=(
        "Requires invoices.view. The invoices that were created, newest first, with customer, "
        "dates, currency and frozen rate, INR amount, TDS, collectible total and balance, and "
        "status. Filter by status (Open, PartlyPaid, Paid), customer_id, or po_hold "
        "(invoices held for not matching their purchase order)."
    ),
)
async def list_invoices(
    status: InvoiceStatus | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    po_hold: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceListItem]:
    return await listing.list_invoices(
        db, status=status, customer_id=customer_id, po_hold=po_hold, limit=limit, offset=offset
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetail,
    summary="One invoice",
    description=(
        "Requires invoices.view. Everything on the invoice: amounts and frozen FX rate, the "
        "current line items, the source file name, the linked purchase order, and the open "
        "PO-mismatch review while it is on hold."
    ),
    responses={404: {"description": "No such invoice"}},
)
async def invoice_detail(
    invoice_id: uuid.UUID,
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> InvoiceDetail:
    return await listing.get_invoice(db, invoice_id)
