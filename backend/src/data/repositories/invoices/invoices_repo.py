import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import (
    Customer,
    Invoice,
    InvoiceLineItem,
    PendingInvoiceChange,
    RejectedInvoice,
    SourceDocument,
)
from data.models.postgres.enums import (
    InvoiceStatus,
    PendingChangeReason,
    PendingChangeStatus,
    RejectionResolution,
)
from data.repositories import unit_of_work


async def invoice_number_exists(db: AsyncSession, invoice_number: str) -> bool:
    found = await db.scalar(select(Invoice.id).where(Invoice.invoice_number == invoice_number))
    return found is not None


async def insert_invoice(db: AsyncSession, **fields: Any) -> Invoice:
    invoice = Invoice(**fields)
    db.add(invoice)
    await unit_of_work.flush(db)
    return invoice


async def insert_line_items(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    db.add_all(InvoiceLineItem(**row) for row in rows)
    await unit_of_work.flush(db)


async def insert_pending_change(db: AsyncSession, **fields: Any) -> PendingInvoiceChange:
    change = PendingInvoiceChange(**fields)
    db.add(change)
    await unit_of_work.flush(db)
    return change


async def insert_rejected(db: AsyncSession, **fields: Any) -> RejectedInvoice:
    rejected = RejectedInvoice(**fields)
    db.add(rejected)
    await unit_of_work.flush(db)
    return rejected


async def get_pending_for_update(db: AsyncSession, review_id: uuid.UUID) -> PendingInvoiceChange | None:
    """Row-locks the review item so two simultaneous decisions cannot both win."""
    return await db.scalar(
        select(PendingInvoiceChange).where(PendingInvoiceChange.id == review_id).with_for_update()
    )


async def get_pending(db: AsyncSession, review_id: uuid.UUID) -> PendingInvoiceChange | None:
    return await db.get(PendingInvoiceChange, review_id)


async def list_pending_for_user(
    db: AsyncSession, user_id: uuid.UUID, status: PendingChangeStatus
) -> list[PendingInvoiceChange]:
    result = await db.scalars(
        select(PendingInvoiceChange)
        .where(
            PendingInvoiceChange.created_by == user_id,
            PendingInvoiceChange.reason == PendingChangeReason.EXTRACTION_REVIEW,
            PendingInvoiceChange.status == status,
            # purchase-order and document-type reviews have their own lists
            PendingInvoiceChange.proposed_snapshot["kind"].astext.is_distinct_from("purchase_order"),
            PendingInvoiceChange.proposed_snapshot["kind"].astext.is_distinct_from("doc_type"),
        )
        .order_by(PendingInvoiceChange.created_at.desc())
    )
    return list(result)


async def list_pending_by_reason(
    db: AsyncSession, reason: PendingChangeReason, status: PendingChangeStatus
) -> list[PendingInvoiceChange]:
    result = await db.scalars(
        select(PendingInvoiceChange)
        .where(PendingInvoiceChange.reason == reason, PendingInvoiceChange.status == status)
        .order_by(PendingInvoiceChange.created_at.desc())
    )
    return list(result)


async def list_pending_po_extraction_reviews(
    db: AsyncSession, user_id: uuid.UUID, status: PendingChangeStatus
) -> list[PendingInvoiceChange]:
    result = await db.scalars(
        select(PendingInvoiceChange)
        .where(
            PendingInvoiceChange.created_by == user_id,
            PendingInvoiceChange.reason == PendingChangeReason.EXTRACTION_REVIEW,
            PendingInvoiceChange.status == status,
            PendingInvoiceChange.proposed_snapshot["kind"].astext == "purchase_order",
        )
        .order_by(PendingInvoiceChange.created_at.desc())
    )
    return list(result)


async def list_pending_doc_type_reviews(
    db: AsyncSession, user_id: uuid.UUID, status: PendingChangeStatus
) -> list[PendingInvoiceChange]:
    result = await db.scalars(
        select(PendingInvoiceChange)
        .where(
            PendingInvoiceChange.created_by == user_id,
            PendingInvoiceChange.reason == PendingChangeReason.EXTRACTION_REVIEW,
            PendingInvoiceChange.status == status,
            PendingInvoiceChange.proposed_snapshot["kind"].astext == "doc_type",
        )
        .order_by(PendingInvoiceChange.created_at.desc())
    )
    return list(result)


async def get_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    return await db.get(Invoice, invoice_id)


async def invoices_by_document(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Invoice]:
    result = await db.scalars(select(Invoice).where(Invoice.source_document_id.in_(document_ids)))
    return {inv.source_document_id: inv for inv in result}


async def latest_pending_by_document(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PendingInvoiceChange]:
    result = await db.scalars(
        select(PendingInvoiceChange)
        .where(PendingInvoiceChange.source_document_id.in_(document_ids))
        .order_by(PendingInvoiceChange.created_at)
    )
    return {p.source_document_id: p for p in result}  # later rows overwrite earlier ones


async def rejected_by_document(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RejectedInvoice]:
    result = await db.scalars(
        select(RejectedInvoice)
        .where(RejectedInvoice.source_document_id.in_(document_ids))
        .order_by(RejectedInvoice.created_at)
    )
    return {r.source_document_id: r for r in result}


async def get_rejected_for_update(db: AsyncSession, rejected_id: uuid.UUID) -> RejectedInvoice | None:
    """Row-locks the rejected entry so two simultaneous send-backs cannot both win."""
    return await db.scalar(
        select(RejectedInvoice).where(RejectedInvoice.id == rejected_id).with_for_update()
    )


def mark_reuploaded(rejected: RejectedInvoice, actor: uuid.UUID) -> None:
    """The rejection is resolved: the uploader sent the document back for a type decision."""
    rejected.resolution = RejectionResolution.REUPLOADED
    rejected.updated_by = actor


# ---------------------------------------------------------------- read side (lists)
async def list_invoices(
    db: AsyncSession,
    *,
    status: InvoiceStatus | None,
    customer_id: uuid.UUID | None,
    po_hold: bool | None,
    limit: int,
    offset: int,
) -> list[tuple[Invoice, str]]:
    query = select(Invoice, Customer.customer_name).join(Customer, Customer.id == Invoice.customer_id)
    if status is not None:
        query = query.where(Invoice.status == status)
    if customer_id is not None:
        query = query.where(Invoice.customer_id == customer_id)
    if po_hold is not None:
        query = query.where(Invoice.po_hold == po_hold)
    rows = await db.execute(
        query.order_by(Invoice.created_at.desc(), Invoice.id).limit(limit).offset(offset)
    )
    return [(invoice, name) for invoice, name in rows]


async def current_line_items(db: AsyncSession, invoice: Invoice) -> list[InvoiceLineItem]:
    result = await db.scalars(
        select(InvoiceLineItem)
        .where(
            InvoiceLineItem.invoice_id == invoice.id,
            InvoiceLineItem.version_no == invoice.line_items_version,
        )
        .order_by(InvoiceLineItem.created_at, InvoiceLineItem.id)
    )
    return list(result)


async def open_po_mismatch(db: AsyncSession, invoice_id: uuid.UUID) -> PendingInvoiceChange | None:
    return await db.scalar(
        select(PendingInvoiceChange).where(
            PendingInvoiceChange.invoice_id == invoice_id,
            PendingInvoiceChange.reason == PendingChangeReason.PO_MISMATCH,
            PendingInvoiceChange.status == PendingChangeStatus.PENDING,
        )
    )


async def list_rejected(
    db: AsyncSession, *, kind: str, include_resolved: bool, limit: int, offset: int
) -> list[tuple[RejectedInvoice, SourceDocument | None]]:
    snapshot_kind = RejectedInvoice.extracted_snapshot["kind"].astext
    is_po = snapshot_kind == "purchase_order"
    is_other = snapshot_kind == "other"
    query = select(RejectedInvoice, SourceDocument).outerjoin(
        SourceDocument, SourceDocument.id == RejectedInvoice.source_document_id
    )
    if kind == "invoice":
        query = query.where(is_po.is_not(True), is_other.is_not(True))
    elif kind == "purchase_order":
        query = query.where(is_po.is_(True))
    elif kind == "other":
        query = query.where(is_other.is_(True))
    if not include_resolved:
        query = query.where(RejectedInvoice.resolution == RejectionResolution.NONE)
    rows = await db.execute(
        query.order_by(RejectedInvoice.created_at.desc(), RejectedInvoice.id).limit(limit).offset(offset)
    )
    return [(rejected, document) for rejected, document in rows]
