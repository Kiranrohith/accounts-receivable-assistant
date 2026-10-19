import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.documents import type_review
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import PendingChangeStatus
from schemas.documents.type_review import (
    TypeChoice,
    TypeDecision,
    TypeReject,
    TypeReviewDetail,
    TypeReviewSummary,
)

router = APIRouter(prefix="/documents/type-reviews", tags=["document type reviews"])

_ONLY_UPLOADER = " Only the person who uploaded the document can see and decide on it."


@router.get(
    "",
    response_model=list[TypeReviewSummary],
    summary="My uploads waiting for a document-type decision",
    description=(
        "Requires invoices.review. Documents you uploaded that the reader could not confidently "
        "tell apart (invoice, purchase order or something else). Pick what it is, or reject it."
        + _ONLY_UPLOADER
    ),
)
async def list_reviews(
    status: Literal["Pending", "Approved", "Rejected"] = Query("Pending"),
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> list[TypeReviewSummary]:
    return await type_review.list_my_reviews(db, user, PendingChangeStatus(status))


@router.get(
    "/{review_id}",
    response_model=TypeReviewDetail,
    summary="One document-type review item",
    description="Requires invoices.review." + _ONLY_UPLOADER,
)
async def review_detail(
    review_id: uuid.UUID,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> TypeReviewDetail:
    return await type_review.get_my_review(db, user, review_id)


@router.post(
    "/{review_id}/choose",
    response_model=TypeDecision,
    summary="Say what the document is: an invoice or a purchase order",
    description=(
        "Requires invoices.review. `doc_type` is `invoice` or `po`; a reason is optional. The "
        "document is read again as that kind in the background (poll the upload status), with "
        "that kind's usual checks: a document that does not fit ends in the rejected list with "
        "every reason." + _ONLY_UPLOADER
    ),
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided"},
        422: {"description": "The file is no longer available"},
    },
)
async def choose(
    review_id: uuid.UUID,
    body: TypeChoice,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> TypeDecision:
    return await type_review.choose_type(db, user, review_id, body)


@router.post(
    "/{review_id}/reject",
    response_model=TypeDecision,
    summary="Reject the upload",
    description=(
        "Requires invoices.review. A reason is required. The document goes to the rejected "
        "uploads with your reason; the file is kept." + _ONLY_UPLOADER
    ),
    responses={
        403: {"description": "Not the uploader"},
        409: {"description": "Already decided"},
    },
)
async def reject(
    review_id: uuid.UUID,
    body: TypeReject,
    user: User = Depends(require_permission("invoices.review")),
    db: AsyncSession = Depends(get_db),
) -> TypeDecision:
    return await type_review.reject_upload(db, user, review_id, body)
