"""The uploader's review of a document that needed a human (pending_invoice_changes).

Only the person who uploaded the document can decide. Approving re-checks the (possibly
edited) data and creates the invoice; rejecting writes a rejected_invoices row with the
reason. Soft doubts (low confidence, date range, arithmetic) are the reviewer's call and
do not block an approval; hard problems do.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailure,
)
from core.services.customers.resolution import resolve_customer
from core.services.invoices.fx import is_foreign, manual_rate, rate_for_invoice
from core.services.invoices import versioning
from core.services.invoices.routing import create_invoice
from core.services.invoices.validation import validate_extraction
from data.models.postgres import PendingInvoiceChange, SourceDocument, User
from data.models.postgres.enums import PendingChangeReason, PendingChangeStatus, TriggerType
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.unit_of_work import DuplicateRecordError
from schemas.invoices.extraction import ExtractedInvoice
from schemas.invoices.review import (
    ReviewApprove,
    ReviewDecision,
    ReviewDetail,
    ReviewReject,
    ReviewSummary,
)
from schemas.invoices.validation import Severity

ENTITY_TYPE = "pending_invoice_change"


def _summary(item: PendingInvoiceChange, document: SourceDocument | None) -> ReviewSummary:
    snapshot = item.proposed_snapshot or {}
    extraction = snapshot.get("extraction") or {}
    matched = snapshot.get("matched_customer_id")
    return ReviewSummary(
        review_id=item.id,
        source_document_id=item.source_document_id,
        file_name=document.file_name if document else None,
        uploaded_at=document.created_at if document else None,
        status=item.status.value,
        reasons=snapshot.get("reasons", []),
        invoice_number=extraction.get("invoice_number"),
        customer_name=extraction.get("customer_name"),
        total_amount=extraction.get("total_amount"),
        matched_customer_id=uuid.UUID(matched) if matched else None,
    )


def _own_item(item: PendingInvoiceChange | None, user: User) -> PendingInvoiceChange:
    if (
        item is None
        or item.reason is not PendingChangeReason.EXTRACTION_REVIEW
        or (item.proposed_snapshot or {}).get("kind") in ("purchase_order", "doc_type")
    ):
        raise NotFoundError("Review item not found")
    if item.created_by != user.id:
        raise PermissionDeniedError("Only the person who uploaded this document can review it")
    return item


async def list_my_reviews(
    db: AsyncSession, user: User, status: PendingChangeStatus = PendingChangeStatus.PENDING
) -> list[ReviewSummary]:
    items = await invoices_repo.list_pending_for_user(db, user.id, status)
    documents = await documents_repo.documents_by_ids(
        db, [i.source_document_id for i in items if i.source_document_id]
    )
    return [_summary(i, documents.get(i.source_document_id)) for i in items]


async def get_my_review(db: AsyncSession, user: User, review_id: uuid.UUID) -> ReviewDetail:
    item = _own_item(await invoices_repo.get_pending(db, review_id), user)
    document = await documents_repo.get_document(db, item.source_document_id)
    snapshot = item.proposed_snapshot
    matched = snapshot.get("matched_customer_id")
    customer = await customers_repo.get_by_id(db, uuid.UUID(matched)) if matched else None
    invoice = (await invoices_repo.invoices_by_document(db, [item.source_document_id])).get(
        item.source_document_id
    )
    return ReviewDetail(
        **_summary(item, document).model_dump(),
        extraction=ExtractedInvoice.model_validate(snapshot["extraction"]),
        matched_customer_name=customer.customer_name if customer else None,
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
        invoice_id=invoice.id if invoice else None,
    )


async def _lock_decidable(db: AsyncSession, user: User, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _own_item(await invoices_repo.get_pending_for_update(db, review_id), user)
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


def record_decision(
    db: AsyncSession,
    item: PendingInvoiceChange,
    user: User,
    status: PendingChangeStatus,
    reason: str,
) -> None:
    now = datetime.now(UTC)
    item.status = status
    item.decided_by = user.id
    item.decided_at = now
    item.decision_reason = reason
    logs_repo.add_state_log(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=item.id,
        from_state=PendingChangeStatus.PENDING.value,
        to_state=status.value,
        triggered_by_type=TriggerType.USER,
        triggered_by_id=user.id,
        reason=reason,
        occurred_at=now,
    )


async def approve_review(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: ReviewApprove
) -> ReviewDecision:
    item = await _lock_decidable(db, user, review_id)
    document = await documents_repo.get_document(db, item.source_document_id)

    edits = body.edits.model_dump(exclude_unset=True)
    edited = ExtractedInvoice.model_validate(item.proposed_snapshot["extraction"]).model_copy(
        update=edits
    )

    hard = [i.message for i in validate_extraction(edited).issues if i.severity is Severity.FAIL]
    if hard:
        raise ValidationFailure("Cannot approve yet: " + "; ".join(hard))

    if body.customer_id:
        customer = await customers_repo.get_by_id(db, body.customer_id)
        if customer is None or not customer.is_active:
            raise ValidationFailure("The chosen customer does not exist or is deactivated")
    else:
        settings = get_settings()
        match = await resolve_customer(
            db,
            gstin=edited.customer_gstin,
            name=edited.customer_name,
            our_gstin=settings.our_gstin,
            our_company_name=settings.our_company_name,
        )
        if not match.matched:
            raise ValidationFailure(
                "Cannot approve yet: " + "; ".join(match.issues)
                + ". Choose the customer (customer_id) or correct the customer name or GSTIN."
            )
        customer = match.customer

    currency = (edited.currency or "").strip().upper()
    if not currency:
        raise ValidationFailure("Cannot approve: the currency is missing; set it in the edits")
    number = edited.invoice_number.strip()
    if await invoices_repo.invoice_number_exists(db, number):
        # The number is already saved: the approved document is compared with it like any other
        # re-upload (never a raw unique-key error).
        outcome = await versioning.route_existing(
            db, document, edited, customer, user.id, write_rejections=False
        )
        if outcome.kind in ("duplicate", "blocked"):
            raise ConflictError(outcome.reasons[0] + "; correct the number or reject this review")
        item.diff = {
            "edits": body.edits.model_dump(mode="json", exclude_unset=True),
            "customer_id": str(customer.id),
            "version_outcome": outcome.kind,
        }
        record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
        await unit_of_work.commit(db)
        return ReviewDecision(
            review_id=item.id,
            status="Approved",
            invoice_id=outcome.invoice_id,
            version=outcome.version,
            version_review_id=outcome.review_id,
            po_hold=outcome.po_hold,
            po_reasons=outcome.po_reasons,
        )

    # Rate lookup only for a foreign invoice. No usable rate (422) or the service being
    # down (502) stops the approval; nothing is saved and the item stays pending.
    # A rate a person typed in (a currency the ECB does not publish) is used instead of the ECB's.
    if not is_foreign(currency):
        fx = None
    elif body.fx_rate is not None:
        fx = manual_rate(currency, edited.invoice_date, body.fx_rate)
    else:
        fx = await rate_for_invoice(currency, edited.invoice_date)

    try:
        created = await create_invoice(db, document, edited, customer, user.id, fx)
        item.diff = {
            "edits": body.edits.model_dump(mode="json", exclude_unset=True),
            "customer_id": str(customer.id),
        }
        record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:
        raise ConflictError(f"An invoice numbered {number} already exists") from exc
    return ReviewDecision(
        review_id=item.id,
        status="Approved",
        invoice_id=created.id,
        po_hold=created.po_hold,
        po_reasons=created.po_reasons,
    )


async def reject_review(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: ReviewReject
) -> ReviewDecision:
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
        extracted_snapshot=snapshot.get("extraction"),
        rejection_reasons=reasons,
        created_by=user.id,
        updated_by=user.id,
    )
    record_decision(db, item, user, PendingChangeStatus.REJECTED, body.reason)
    await unit_of_work.commit(db)
    return ReviewDecision(review_id=item.id, status="Rejected")
