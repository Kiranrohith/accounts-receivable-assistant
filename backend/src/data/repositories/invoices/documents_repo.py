import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import (
    Dispute,
    Invoice,
    Payment,
    PendingInvoiceChange,
    PurchaseOrder,
    RejectedInvoice,
    SourceDocument,
)
from data.models.postgres.enums import DocKind, UploadedVia
from data.repositories import unit_of_work


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> SourceDocument | None:
    return await db.get(SourceDocument, document_id)


async def get_document_for_update(db: AsyncSession, document_id: uuid.UUID) -> SourceDocument | None:
    """Row-locks the document so two simultaneous re-runs cannot both queue a job."""
    return await db.scalar(
        select(SourceDocument).where(SourceDocument.id == document_id).with_for_update()
    )


async def documents_by_ids(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SourceDocument]:
    result = await db.scalars(select(SourceDocument).where(SourceDocument.id.in_(document_ids)))
    return {d.id: d for d in result}


async def documents_of_mail(
    db: AsyncSession, email_id: uuid.UUID, kind: DocKind | None = None
) -> list[SourceDocument]:
    """The documents made from one polled mail (its attachments, its own text), oldest first."""
    query = select(SourceDocument).where(SourceDocument.ingested_email_id == email_id)
    if kind is not None:
        query = query.where(SourceDocument.doc_kind == kind)
    return list(await db.scalars(query.order_by(SourceDocument.created_at, SourceDocument.id)))


async def document_id_by_hash(db: AsyncSession, file_hash: str) -> uuid.UUID | None:
    return await db.scalar(select(SourceDocument.id).where(SourceDocument.file_hash == file_hash))


async def insert_document(db: AsyncSession, **fields: Any) -> SourceDocument:
    document = SourceDocument(**fields)
    db.add(document)
    await unit_of_work.flush(db)
    return document


async def list_uploaded_documents(
    db: AsyncSession,
    limit: int,
    doc_kind: DocKind | None = DocKind.INVOICE,
    *,
    offset: int = 0,
    created_by: uuid.UUID | None = None,
) -> list[SourceDocument]:
    """Newest first; `doc_kind=None` lists every kind; `created_by` keeps one person's uploads."""
    query = select(SourceDocument).where(SourceDocument.uploaded_via == UploadedVia.UPLOAD)
    if doc_kind is not None:
        query = query.where(SourceDocument.doc_kind == doc_kind)
    if created_by is not None:
        query = query.where(SourceDocument.created_by == created_by)
    result = await db.scalars(
        query.order_by(SourceDocument.created_at.desc(), SourceDocument.id).limit(limit).offset(offset)
    )
    return list(result)


async def count_uploaded_documents(db: AsyncSession, created_by: uuid.UUID) -> int:
    """How many documents one person uploaded (every kind)."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.uploaded_via == UploadedVia.UPLOAD, SourceDocument.created_by == created_by)
        )
        or 0
    )


def set_kind(document: SourceDocument, kind: DocKind) -> None:
    document.doc_kind = kind


def save_classification(document: SourceDocument, classification: dict) -> None:
    """The classifier's raw verdict, kept beside the field extraction that may follow it."""
    document.raw_extraction_json = {
        **(document.raw_extraction_json or {}),
        "classification": classification,
    }


def save_extraction(document: SourceDocument, raw: dict, confidences: dict) -> None:
    previous = document.raw_extraction_json or {}
    kept = {k: previous[k] for k in ("classification", "dispute_extraction") if previous.get(k)}
    document.raw_extraction_json = {**raw, **kept}
    document.field_confidences = confidences


def fill_customer_tax_id(document: SourceDocument, tax_id: str, tax_id_type: object) -> None:
    """The document's extraction had no customer tax ID and the customer is now known: keep the customer's own tax ID and
    type in the extraction, marked as coming from the customer record (an id the reader found is never replaced)."""
    raw = dict(document.raw_extraction_json or {})
    if (raw.get("tax_id_value") or "").strip():
        return
    kind = getattr(tax_id_type, "value", tax_id_type)
    document.raw_extraction_json = {**raw, "tax_id_value": tax_id, "tax_id_type": kind, "tax_id_from_customer": True}


def save_dispute_extraction(document: SourceDocument, raw: dict) -> None:
    """The complaint read from a document that ALSO holds a payment advice. The payment's own
    fields stay the document's main extraction; this one is kept beside them."""
    document.raw_extraction_json = {**(document.raw_extraction_json or {}), "dispute_extraction": raw}


async def has_outcome_since(db: AsyncSession, document_id: uuid.UUID, since: datetime) -> bool:
    """True if processing already wrote this document's business outcome (an invoice or a new
    version of one, a purchase order, a payment, a review item or a rejection) after `since`: a re-run must
    not write it twice."""
    touched_invoice = await db.scalar(
        select(Invoice.id)
        .where(
            Invoice.source_document_id == document_id,
            or_(Invoice.created_at >= since, Invoice.updated_at >= since),
        )
        .limit(1)
    )
    if touched_invoice is not None:
        return True
    for model in (PurchaseOrder, Payment, Dispute, PendingInvoiceChange, RejectedInvoice):
        found = await db.scalar(
            select(model.id)
            .where(model.source_document_id == document_id, model.created_at >= since)
            .limit(1)
        )
        if found is not None:
            return True
    return False
