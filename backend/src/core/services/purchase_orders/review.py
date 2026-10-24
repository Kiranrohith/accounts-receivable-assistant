"""The uploader's review of a purchase-order document that needed a human.

Mirrors the invoice review: only the person who uploaded the document decides. Approving
re-checks the (possibly edited) data and creates the PO, then links the invoices waiting for
it; rejecting writes a rejected_invoices row with the reason.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailure,
)
from core.services.customers.customers import remember_name
from core.services.customers.gstin import with_customer_gstin
from core.services.customers.resolution import resolve_customer
from core.services.invoices.review import record_decision
from core.services.purchase_orders.routing import PO_KIND, create_purchase_order
from core.services.purchase_orders.validation import validate_purchase_order
from core.services.users.users import permission_codes_for
from data.models.postgres import PendingInvoiceChange, SourceDocument, User
from data.models.postgres.enums import PendingChangeReason, PendingChangeStatus
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from data.repositories.unit_of_work import DuplicateRecordError
from schemas.invoices.validation import Severity
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POLineItem
from schemas.purchase_orders.review import (
    POReviewApprove,
    POReviewDecision,
    POReviewDetail,
    POReviewReject,
    POReviewSummary,
)


def _summary(item: PendingInvoiceChange, document: SourceDocument | None) -> POReviewSummary:
    snapshot = item.proposed_snapshot or {}
    extraction = snapshot.get("extraction") or {}
    matched = snapshot.get("matched_customer_id")
    return POReviewSummary(
        review_id=item.id,
        source_document_id=item.source_document_id,
        file_name=document.file_name if document else None,
        uploaded_at=document.created_at if document else None,
        status=item.status.value,
        reasons=snapshot.get("reasons", []),
        po_number=extraction.get("po_number"),
        customer_name=extraction.get("customer_name"),
        total_amount=extraction.get("total_amount"),
        matched_customer_id=uuid.UUID(matched) if matched else None,
    )


def _own_item(item: PendingInvoiceChange | None, user: User) -> PendingInvoiceChange:
    if (
        item is None
        or item.reason is not PendingChangeReason.EXTRACTION_REVIEW
        or (item.proposed_snapshot or {}).get("kind") != PO_KIND
    ):
        raise NotFoundError("Review item not found")
    if item.created_by != user.id:
        raise PermissionDeniedError("Only the person who uploaded this document can review it")
    return item


async def list_my_reviews(
    db: AsyncSession, user: User, status: PendingChangeStatus = PendingChangeStatus.PENDING
) -> list[POReviewSummary]:
    items = await invoices_repo.list_pending_po_extraction_reviews(db, user.id, status)
    documents = await documents_repo.documents_by_ids(
        db, [i.source_document_id for i in items if i.source_document_id]
    )
    return [_summary(i, documents.get(i.source_document_id)) for i in items]


async def get_my_review(db: AsyncSession, user: User, review_id: uuid.UUID) -> POReviewDetail:
    item = _own_item(await invoices_repo.get_pending(db, review_id), user)
    document = await documents_repo.get_document(db, item.source_document_id)
    snapshot = item.proposed_snapshot
    matched = snapshot.get("matched_customer_id")
    customer = await customers_repo.get_by_id(db, uuid.UUID(matched)) if matched else None
    order = (await po_repo.purchase_orders_by_document(db, [item.source_document_id])).get(
        item.source_document_id
    )
    return POReviewDetail(
        **_summary(item, document).model_dump(),
        extraction=ExtractedPurchaseOrder.model_validate(snapshot["extraction"]),
        matched_customer_name=customer.customer_name if customer else None,
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
        purchase_order_id=order.id if order else None,
    )


async def _lock_decidable(db: AsyncSession, user: User, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _own_item(await invoices_repo.get_pending_for_update(db, review_id), user)
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


async def approve_review(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: POReviewApprove
) -> POReviewDecision:
    if body.save_alias and "customers.alias.confirm" not in await permission_codes_for(db, user):
        raise PermissionDeniedError("Missing permission: customers.alias.confirm (to save an alias)")
    item = await _lock_decidable(db, user, review_id)
    document = await documents_repo.get_document(db, item.source_document_id)

    edits = body.edits.model_dump(exclude_unset=True, exclude={"line_items"})
    if body.edits.line_items is not None:
        edits["line_items"] = [
            POLineItem(**line.model_dump()) for line in body.edits.line_items
        ]
    edited = ExtractedPurchaseOrder.model_validate(item.proposed_snapshot["extraction"]).model_copy(
        update=edits
    )

    hard = [
        i.message for i in validate_purchase_order(edited).issues if i.severity is Severity.FAIL
    ]
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

    # the document showed no customer GSTIN: the customer's own is kept in its extraction (a printed one is never replaced)
    filled = with_customer_gstin(edited, customer.gstin)
    if filled is not edited:
        edited = filled
        documents_repo.fill_customer_gstin(document, customer.gstin)

    number = edited.po_number.strip()
    if await po_repo.get_by_customer_number(db, customer.id, number):
        raise ConflictError(
            f"A purchase order numbered {number} already exists for {customer.customer_name}; "
            "correct the number or reject"
        )

    # the name the PO carries becomes one of the customer's aliases (saved or refused together with the PO)
    alias_saved = await remember_name(db, user, customer, edited.customer_name) if body.save_alias else None

    try:
        po_id, linked = await create_purchase_order(db, document, edited, customer, user.id)
        item.diff = {
            "edits": body.edits.model_dump(mode="json", exclude_unset=True),
            "customer_id": str(customer.id),
            **({"alias_saved": alias_saved} if alias_saved else {}),
        }
        record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:
        raise ConflictError(f"A purchase order numbered {number} already exists") from exc
    return POReviewDecision(
        review_id=item.id, status="Approved", purchase_order_id=po_id, linked_invoices=linked,
        alias_saved=alias_saved,
    )


async def reject_review(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: POReviewReject
) -> POReviewDecision:
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
        extracted_snapshot={"kind": PO_KIND, **(snapshot.get("extraction") or {})},
        rejection_reasons=reasons,
        created_by=user.id,
        updated_by=user.id,
    )
    record_decision(db, item, user, PendingChangeStatus.REJECTED, body.reason)
    await unit_of_work.commit(db)
    return POReviewDecision(review_id=item.id, status="Rejected")
