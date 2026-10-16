import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.purchase_orders import mismatch_review, review
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import PendingChangeStatus
from schemas.purchase_orders.review import (
    MismatchDecision,
    MismatchDetail,
    MismatchSummary,
    OverrideBody,
    POReviewApprove,
    POReviewDecision,
    POReviewDetail,
    POReviewReject,
    POReviewSummary,
    RecheckBody,
)

# ------------------------------------------------------------------ PO extraction review
router = APIRouter(prefix="/purchase-orders/reviews", tags=["purchase order reviews"])

_ONLY_UPLOADER = " Only the person who uploaded the document can see and decide on it."


@router.get(
    "",
    response_model=list[POReviewSummary],
    summary="My purchase orders waiting for review",
    description="Requires invoices.review. Purchase-order documents you uploaded that need your "
    "decision (wrong upload button, PO number not read, unknown customer, duplicate PO number...)."
    + _ONLY_UPLOADER,
)
async def list_reviews(
    status: Literal["Pending", "Approved", "Rejected"] = Query("Pending"),
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> list[POReviewSummary]:
    return await review.list_my_reviews(db, user, PendingChangeStatus(status))


@router.get(
    "/{review_id}",
    response_model=POReviewDetail,
    summary="One purchase-order review item",
    description="Requires invoices.review. The extracted values you can correct." + _ONLY_UPLOADER,
)
async def review_detail(
    review_id: uuid.UUID,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> POReviewDetail:
    return await review.get_my_review(db, user, review_id)


@router.post(
    "/{review_id}/approve",
    response_model=POReviewDecision,
    summary="Approve (and optionally correct) the purchase order",
    description=(
        "Requires invoices.review. A reason is required. Send `edits` to correct fields (send "
        "`line_items` to replace the whole list) and `customer_id` to pick the customer "
        "yourself. Approving says this document is a purchase order. The data is re-checked: a "
        "missing PO number, no customer, no lines or a bad quantity block the approval; a PO "
        "number that already exists for the customer returns 409. On success the PO is created "
        "and waiting invoices are linked and checked." + _ONLY_UPLOADER
    ),
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided, or PO number already exists for the customer"},
        422: {"description": "Cannot approve yet; the message says what to fix"},
    },
)
async def approve(
    review_id: uuid.UUID,
    body: POReviewApprove,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> POReviewDecision:
    return await review.approve_review(db, user, review_id, body)


@router.post(
    "/{review_id}/reject",
    response_model=POReviewDecision,
    summary="Reject the purchase order",
    description="Requires invoices.review. A reason is required. The document moves to the "
    "rejected list with your reason; the file is kept." + _ONLY_UPLOADER,
    responses={403: {"description": "Not the uploader"}, 409: {"description": "Already decided"}},
)
async def reject(
    review_id: uuid.UUID,
    body: POReviewReject,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> POReviewDecision:
    return await review.reject_review(db, user, review_id, body)


# ------------------------------------------------------------------ PO mismatch review
mismatch_router = APIRouter(
    prefix="/purchase-orders/mismatch-reviews", tags=["purchase order mismatch reviews"]
)

_NO_REJECT = (
    " There is deliberately no reject: the hold releases only through an override or a "
    "passing re-check."
)


@mismatch_router.get(
    "",
    response_model=list[MismatchSummary],
    summary="Invoices held for not matching their purchase order",
    description="Requires invoices.review. Visible to everyone with that permission, not only "
    "the uploader." + _NO_REJECT,
)
async def list_mismatches(
    status: Literal["Pending", "Approved"] = Query("Pending"),
    _: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> list[MismatchSummary]:
    return await mismatch_review.list_mismatch_reviews(db, PendingChangeStatus(status))


@mismatch_router.get(
    "/{review_id}",
    response_model=MismatchDetail,
    summary="One held invoice: the line-by-line comparison",
    description="Requires invoices.review. Per invoice line: ordered, already invoiced, this "
    "invoice, remaining after (negative = over the order)." + _NO_REJECT,
)
async def mismatch_detail(
    review_id: uuid.UUID,
    _: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> MismatchDetail:
    return await mismatch_review.get_mismatch_review(db, review_id)


@mismatch_router.post(
    "/{review_id}/override",
    response_model=MismatchDecision,
    summary="Approve the held invoice as-is (override)",
    description=(
        "Requires payments.po_override (FTM and ADM) and a reason. The invoice's quantities "
        "are written to the ledger even if a PO line goes negative (shown, never hidden), "
        "and po_hold is cleared." + _NO_REJECT
    ),
    responses={403: {"description": "Missing payments.po_override"}, 409: {"description": "Already decided"}},
)
async def override(
    review_id: uuid.UUID,
    body: OverrideBody,
    user: User = Depends(require_permission("payments.po_override")),
    db: AsyncSession = Depends(get_db),
) -> MismatchDecision:
    return await mismatch_review.override(db, user, review_id, body)


@mismatch_router.post(
    "/{review_id}/recheck",
    response_model=MismatchDecision,
    summary="Correct the invoice lines and re-check",
    description=(
        "Requires invoices.review. Send the corrected invoice lines (a full replacement set, "
        "saved as a new line-item version; the old one is kept). If they now match the "
        "purchase order the hold is released; if not, the review stays Pending with the new "
        "comparison." + _NO_REJECT
    ),
    responses={409: {"description": "Already decided"}},
)
async def recheck(
    review_id: uuid.UUID,
    body: RecheckBody,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> MismatchDecision:
    return await mismatch_review.recheck(db, user, review_id, body)
