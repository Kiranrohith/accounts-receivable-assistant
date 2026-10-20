"""The uploader's decision on a changed version of an already-saved invoice (reason version_upload).

Only the person who uploaded the document decides. Approving applies the new version (the same
code as an automatic apply) after re-checking everything under a lock; rejecting writes a
rejected-list entry. An invoice that is already paid (closed) cannot take a new version: its
review can only be rejected.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailure,
)
from core.services.invoices import versioning
from core.services.invoices.fx import rate_for_invoice
from core.services.invoices.review import record_decision
from data.models.postgres import Invoice, PendingInvoiceChange, SourceDocument, User
from data.models.postgres.enums import InvoiceStatus, PendingChangeReason, PendingChangeStatus
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import documents_repo, invoices_repo
from schemas.invoices.extraction import ExtractedInvoice
from schemas.invoices.version_review import (
    ChangeOut,
    VersionApprove,
    VersionDecision,
    VersionReject,
    VersionReviewDetail,
    VersionReviewSummary,
)


def _own_item(item: PendingInvoiceChange | None, user: User) -> PendingInvoiceChange:
    if item is None or item.reason is not PendingChangeReason.VERSION_UPLOAD or item.invoice_id is None:
        raise NotFoundError("Review item not found")
    if item.created_by != user.id:
        raise PermissionDeniedError("Only the person who uploaded this document can review it")
    return item


def _summary(
    item: PendingInvoiceChange, invoice: Invoice | None, document: SourceDocument | None
) -> VersionReviewSummary:
    snapshot = item.proposed_snapshot or {}
    return VersionReviewSummary(
        review_id=item.id,
        invoice_id=item.invoice_id,
        invoice_number=invoice.invoice_number if invoice else None,
        source_document_id=item.source_document_id,
        file_name=document.file_name if document else None,
        uploaded_at=document.created_at if document else None,
        status=item.status.value,
        reasons=snapshot.get("reasons", []),
        can_approve=bool(snapshot.get("can_approve", True)),
        base_version=snapshot.get("base_version", 0),
        changes=[ChangeOut(**c) for c in snapshot.get("changes", [])],
    )


async def list_my_reviews(
    db: AsyncSession, user: User, status: PendingChangeStatus = PendingChangeStatus.PENDING
) -> list[VersionReviewSummary]:
    items = await invoices_repo.list_pending_version_reviews(db, user.id, status)
    documents = await documents_repo.documents_by_ids(
        db, [i.source_document_id for i in items if i.source_document_id]
    )
    out = []
    for item in items:
        invoice = await invoices_repo.get_invoice(db, item.invoice_id)
        out.append(_summary(item, invoice, documents.get(item.source_document_id)))
    return out


async def get_my_review(db: AsyncSession, user: User, review_id: uuid.UUID) -> VersionReviewDetail:
    item = _own_item(await invoices_repo.get_pending(db, review_id), user)
    invoice = await invoices_repo.get_invoice(db, item.invoice_id)
    document = await documents_repo.get_document(db, item.source_document_id)
    customer = await customers_repo.get_by_id(db, invoice.customer_id) if invoice else None
    return VersionReviewDetail(
        **_summary(item, invoice, document).model_dump(),
        current={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status.value,
            "version": invoice.version,
            "customer_name": customer.customer_name if customer else None,
            "currency": (invoice.invoice_currency or "").strip(),
            "total_amount": str(versioning.stored_total(invoice)),
            "amount_paid": str(invoice.amount_paid),
            "due_date": invoice.due_date.isoformat(),
            "po_number": invoice.po_number,
        }
        if invoice
        else {},
        proposed=(item.proposed_snapshot or {}).get("extraction", {}),
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
    )


async def _lock_decidable(db: AsyncSession, user: User, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _own_item(await invoices_repo.get_pending_for_update(db, review_id), user)
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


async def approve(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: VersionApprove
) -> VersionDecision:
    item = await _lock_decidable(db, user, review_id)
    snapshot = item.proposed_snapshot
    invoice = await invoices_repo.get_invoice_for_update(db, item.invoice_id)
    if invoice.status is InvoiceStatus.PAID:
        raise ValidationFailure(
            f"Invoice {invoice.invoice_number} is already paid (closed), so a changed version "
            "cannot be applied. Reject this review."
        )
    if invoice.version != snapshot["base_version"]:
        raise ConflictError(
            "The saved invoice changed after this document was uploaded; reject this review and "
            "upload the document again"
        )
    document = await documents_repo.get_document(db, item.source_document_id)
    extracted = ExtractedInvoice.model_validate(snapshot["extraction"])
    customer = await customers_repo.get_by_id(db, uuid.UUID(snapshot["matched_customer_id"]))
    if customer is None or not customer.is_active:
        raise ValidationFailure("The customer of this document does not exist or is deactivated")
    old_customer = await customers_repo.get_by_id(db, invoice.customer_id)
    lines = await invoices_repo.current_line_items(db, invoice)
    changes = versioning.compare(
        invoice, lines, extracted, customer, old_customer.customer_name if old_customer else ""
    )
    if not changes:
        raise ConflictError("The saved invoice already matches this document; there is nothing to apply")

    # A foreign invoice whose currency or date changed needs a fresh rate. No usable rate (422) or
    # the service being down (502) stops the approval; the item stays pending.
    currency = (extracted.currency or "").strip().upper()
    fx = (
        await rate_for_invoice(currency, extracted.invoice_date)
        if versioning.needs_new_rate(changes, currency)
        else None
    )

    record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
    # Free the invoice's single Pending slot before a purchase-order re-check may need it.
    await db.flush()
    applied = await versioning.apply_version(db, invoice, document, extracted, customer, user.id, changes, fx)
    item.diff = {"changes": [c.out() for c in changes], "applied_version": applied.version}
    await unit_of_work.commit(db)
    return VersionDecision(
        review_id=item.id,
        status="Approved",
        invoice_id=invoice.id,
        version=applied.version,
        po_hold=applied.po_hold,
        po_reasons=applied.po_reasons,
    )


async def reject(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: VersionReject
) -> VersionDecision:
    item = await _lock_decidable(db, user, review_id)
    snapshot = item.proposed_snapshot
    document = await documents_repo.get_document(db, item.source_document_id)
    invoice = await invoices_repo.get_invoice(db, item.invoice_id)
    reasons = [
        {"code": "rejected_in_review", "field": None, "severity": "fail", "message": body.reason}
    ] + [
        {"code": "review_reason", "field": None, "severity": "review", "message": r}
        for r in snapshot.get("reasons", [])
    ]
    await invoices_repo.insert_rejected(
        db,
        source_document_id=item.source_document_id,
        duplicate_of_source_document_id=invoice.source_document_id if invoice else None,
        attempted_file_name=document.file_name if document else None,
        extracted_snapshot=snapshot.get("extraction"),
        rejection_reasons=reasons,
        created_by=user.id,
        updated_by=user.id,
    )
    record_decision(db, item, user, PendingChangeStatus.REJECTED, body.reason)
    await unit_of_work.commit(db)
    return VersionDecision(review_id=item.id, status="Rejected", invoice_id=item.invoice_id)
