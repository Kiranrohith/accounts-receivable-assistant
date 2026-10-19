import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.invoices import version_review
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import PendingChangeStatus
from schemas.invoices.version_review import (
    VersionApprove,
    VersionDecision,
    VersionReject,
    VersionReviewDetail,
    VersionReviewSummary,
)

router = APIRouter(prefix="/invoices/version-reviews", tags=["invoice version reviews"])

_ONLY_UPLOADER = " Only the person who uploaded the document can see and decide on it."


@router.get(
    "",
    response_model=list[VersionReviewSummary],
    summary="My changed invoices waiting for a decision",
    description=(
        "Requires invoices.review. Documents you uploaded whose invoice number is already saved "
        "and which changed something a person has to look at (customer, amount after payments, "
        "an invoice that is already paid, a linked purchase order, and so on)." + _ONLY_UPLOADER
    ),
)
async def list_reviews(
    status: Literal["Pending", "Approved", "Rejected"] = Query("Pending"),
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> list[VersionReviewSummary]:
    return await version_review.list_my_reviews(db, user, PendingChangeStatus(status))


@router.get(
    "/{review_id}",
    response_model=VersionReviewDetail,
    summary="One version review: the saved invoice, the new values and the differences",
    description="Requires invoices.review." + _ONLY_UPLOADER,
)
async def review_detail(
    review_id: uuid.UUID,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> VersionReviewDetail:
    return await version_review.get_my_review(db, user, review_id)


@router.post(
    "/{review_id}/approve",
    response_model=VersionDecision,
    summary="Apply the new version",
    description=(
        "Requires invoices.review. A reason is required. The old state is saved as a superseded "
        "version and the invoice is updated. Refused (422) when the invoice is already paid "
        "(closed) or the new amount is below what was already paid; 409 when the saved invoice "
        "changed since the upload. If the invoice is linked to a purchase order, its lines are "
        "re-checked against it (it may go on hold)." + _ONLY_UPLOADER
    ),
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided, or the saved invoice changed since"},
        422: {"description": "Cannot be applied (closed invoice, amount below what was paid, no rate)"},
    },
)
async def approve(
    review_id: uuid.UUID,
    body: VersionApprove,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> VersionDecision:
    return await version_review.approve(db, user, review_id, body)


@router.post(
    "/{review_id}/reject",
    response_model=VersionDecision,
    summary="Reject the changed version",
    description=(
        "Requires invoices.review. A reason is required. The document goes to the rejected list "
        "with your reason; the saved invoice is untouched." + _ONLY_UPLOADER
    ),
    responses={403: {"description": "Not the uploader"}, 409: {"description": "Already decided"}},
)
async def reject(
    review_id: uuid.UUID,
    body: VersionReject,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> VersionDecision:
    return await version_review.reject(db, user, review_id, body)
