import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.invoices.review import (
    approve_review,
    get_my_review,
    list_my_reviews,
    reject_review,
)
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import PendingChangeStatus
from schemas.invoices.review import (
    ReviewApprove,
    ReviewDecision,
    ReviewDetail,
    ReviewReject,
    ReviewSummary,
)

router = APIRouter(prefix="/invoices/reviews", tags=["invoice reviews"])

_ONLY_UPLOADER = " Only the person who uploaded the document can see and decide on it."


@router.get(
    "",
    response_model=list[ReviewSummary],
    summary="My invoices waiting for review",
    description="Requires invoices.review. Documents you uploaded that need your decision."
    + _ONLY_UPLOADER,
)
async def list_reviews(
    status: Literal["Pending", "Approved", "Rejected"] = Query("Pending"),
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewSummary]:
    return await list_my_reviews(db, user, PendingChangeStatus(status))


@router.get(
    "/{review_id}",
    response_model=ReviewDetail,
    summary="One review item",
    description="Requires invoices.review. The extracted values you can correct, and why "
    "it needs a decision." + _ONLY_UPLOADER,
)
async def review_detail(
    review_id: uuid.UUID,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> ReviewDetail:
    return await get_my_review(db, user, review_id)


@router.post(
    "/{review_id}/approve",
    response_model=ReviewDecision,
    summary="Approve (and optionally correct) the invoice",
    description=(
        "Requires invoices.review. A reason is required. Send `edits` to correct fields and "
        "`customer_id` to pick the customer yourself when matching failed. The data is "
        "re-checked: missing invoice number, dates or amount, an unidentified customer, a "
        "non-INR currency (not supported yet) or an invoice number that already exists "
        "block the approval (the item stays pending). On success the invoice is created."
        + _ONLY_UPLOADER
    ),
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided, or invoice number already exists"},
        422: {"description": "Cannot approve yet; the message says what to fix"},
    },
)
async def approve(
    review_id: uuid.UUID,
    body: ReviewApprove,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> ReviewDecision:
    return await approve_review(db, user, review_id, body)


@router.post(
    "/{review_id}/reject",
    response_model=ReviewDecision,
    summary="Reject the invoice",
    description="Requires invoices.review. A reason is required. The document moves to the "
    "rejected invoices with your reason." + _ONLY_UPLOADER,
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided"},
    },
)
async def reject(
    review_id: uuid.UUID,
    body: ReviewReject,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> ReviewDecision:
    return await reject_review(db, user, review_id, body)
