"""The doc-type review queue: the uploader decides what a doubtful document is.

Same rules as the extraction reviews: only the person who uploaded the document can decide, a
decision is row-locked (one wins, the other gets 409). Picking a type sends the document back
through that kind's extraction (the caller schedules it); rejecting writes a rejected-uploads
entry. Nothing is deleted: the file is always kept.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailure,
)
from core.services.documents.routing import DOC_TYPE_KIND, OTHER_KIND, send_to_type_review
from core.services.invoices.review import record_decision
from data.models.postgres import PendingInvoiceChange, SourceDocument, User
from data.models.postgres.enums import (
    DocKind,
    PendingChangeReason,
    PendingChangeStatus,
    RejectionResolution,
    TriggerType,
)
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.platform import logs_repo
from schemas.documents.classification import DocClassification
from schemas.documents.type_review import (
    SendBackResult,
    TypeChoice,
    TypeDecision,
    TypeReject,
    TypeReviewDetail,
    TypeReviewSummary,
)

_CHOICE_TO_KIND = {"invoice": DocKind.INVOICE, "po": DocKind.PO}


def _summary(item: PendingInvoiceChange, document: SourceDocument | None) -> TypeReviewSummary:
    snapshot = item.proposed_snapshot or {}
    classification = snapshot.get("classification") or {}
    return TypeReviewSummary(
        review_id=item.id,
        source_document_id=item.source_document_id,
        file_name=document.file_name if document else None,
        uploaded_at=document.created_at if document else None,
        status=item.status.value,
        reasons=snapshot.get("reasons", []),
        suggested_type=classification.get("doc_class"),
        confidence=classification.get("confidence"),
        reader_note=classification.get("reason"),
    )


def _own_item(item: PendingInvoiceChange | None, user: User) -> PendingInvoiceChange:
    if (
        item is None
        or item.reason is not PendingChangeReason.EXTRACTION_REVIEW
        or (item.proposed_snapshot or {}).get("kind") != DOC_TYPE_KIND
    ):
        raise NotFoundError("Review item not found")
    if item.created_by != user.id:
        raise PermissionDeniedError("Only the person who uploaded this document can review it")
    return item


async def list_my_reviews(
    db: AsyncSession, user: User, status: PendingChangeStatus = PendingChangeStatus.PENDING
) -> list[TypeReviewSummary]:
    items = await invoices_repo.list_pending_doc_type_reviews(db, user.id, status)
    documents = await documents_repo.documents_by_ids(
        db, [i.source_document_id for i in items if i.source_document_id]
    )
    return [_summary(i, documents.get(i.source_document_id)) for i in items]


async def get_my_review(db: AsyncSession, user: User, review_id: uuid.UUID) -> TypeReviewDetail:
    item = _own_item(await invoices_repo.get_pending(db, review_id), user)
    document = await documents_repo.get_document(db, item.source_document_id)
    return TypeReviewDetail(
        **_summary(item, document).model_dump(),
        chosen_type=(item.diff or {}).get("chosen_type"),
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
    )


async def _lock_decidable(db: AsyncSession, user: User, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _own_item(await invoices_repo.get_pending_for_update(db, review_id), user)
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


async def choose_type(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: TypeChoice
) -> tuple[TypeDecision, uuid.UUID, DocKind]:
    """Record the choice. Returns the decision plus what the caller must now process."""
    item = await _lock_decidable(db, user, review_id)
    document = await documents_repo.get_document(db, item.source_document_id)
    if document is None or not document.file_path:
        raise ValidationFailure("The file is no longer available, so it cannot be read again")
    kind = _CHOICE_TO_KIND[body.doc_type]
    item.diff = {"chosen_type": body.doc_type}
    documents_repo.set_kind(document, kind)
    record_decision(
        db, item, user, PendingChangeStatus.APPROVED,
        body.reason or f"Reviewer chose: this is {'an invoice' if kind is DocKind.INVOICE else 'a PO'}",
    )
    await unit_of_work.commit(db)
    return (
        TypeDecision(
            review_id=item.id, status="Approved", chosen_type=body.doc_type, processing=True
        ),
        document.id,
        kind,
    )


async def send_back_to_review(
    db: AsyncSession, user: User, rejected_id: uuid.UUID
) -> SendBackResult:
    """Undo a "neither an invoice nor a purchase order" rejection: the uploader asks for a human
    type decision instead. The rejection is marked resolved and a doc-type review item opens."""
    rejected = await invoices_repo.get_rejected_for_update(db, rejected_id)
    if rejected is None:
        raise NotFoundError("Rejected item not found")
    snapshot = rejected.extracted_snapshot or {}
    if snapshot.get("kind") != OTHER_KIND:
        raise ValidationFailure(
            "Only a document rejected as neither an invoice nor a purchase order can be sent back"
        )
    document = (
        await documents_repo.get_document(db, rejected.source_document_id)
        if rejected.source_document_id
        else None
    )
    if document is None or document.created_by != user.id:
        raise PermissionDeniedError("Only the person who uploaded this document can send it back")
    if rejected.resolution is not RejectionResolution.NONE:
        raise ConflictError("This rejection was already resolved")
    if not document.file_path:
        raise ValidationFailure("The file is no longer available, so it cannot be reviewed")

    invoices_repo.mark_reuploaded(rejected, user.id)
    original = [r.get("message", "") for r in (rejected.rejection_reasons or [])]
    change_id, _ = await send_to_type_review(
        db,
        document,
        DocClassification.model_validate(snapshot["classification"])
        if snapshot.get("classification")
        else None,
        extra_reasons=["You sent this back for a decision after it was rejected", *original],
    )
    logs_repo.add_state_log(
        db, entity_type="source_document", entity_id=document.id, from_state="Rejected",
        to_state="SentToTypeReview", triggered_by_type=TriggerType.USER, triggered_by_id=user.id,
        reason="The uploader sent a rejected document back for a document-type decision",
        occurred_at=datetime.now(UTC),
    )
    await unit_of_work.commit(db)
    return SendBackResult(rejected_id=rejected.id, review_id=change_id)


async def reject_upload(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: TypeReject
) -> TypeDecision:
    item = await _lock_decidable(db, user, review_id)
    document = await documents_repo.get_document(db, item.source_document_id)
    snapshot = item.proposed_snapshot
    reasons = [
        {"code": "rejected_in_review", "field": None, "severity": "fail", "message": body.reason}
    ] + [
        {"code": "review_reason", "field": None, "severity": "review", "message": r}
        for r in snapshot.get("reasons", [])
    ]
    await invoices_repo.insert_rejected(
        db,
        source_document_id=item.source_document_id,
        attempted_file_name=document.file_name if document else None,
        extracted_snapshot={"kind": OTHER_KIND, "classification": snapshot.get("classification")},
        rejection_reasons=reasons,
        created_by=user.id,
        updated_by=user.id,
    )
    record_decision(db, item, user, PendingChangeStatus.REJECTED, body.reason)
    await unit_of_work.commit(db)
    return TypeDecision(review_id=item.id, status="Rejected")
