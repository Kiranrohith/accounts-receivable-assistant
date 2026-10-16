import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import SourceDocument
from data.models.postgres.enums import DocKind, UploadedVia
from data.repositories import unit_of_work


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> SourceDocument | None:
    return await db.get(SourceDocument, document_id)


async def documents_by_ids(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SourceDocument]:
    result = await db.scalars(select(SourceDocument).where(SourceDocument.id.in_(document_ids)))
    return {d.id: d for d in result}


async def document_id_by_hash(db: AsyncSession, file_hash: str) -> uuid.UUID | None:
    return await db.scalar(select(SourceDocument.id).where(SourceDocument.file_hash == file_hash))


async def insert_document(db: AsyncSession, **fields: Any) -> SourceDocument:
    document = SourceDocument(**fields)
    db.add(document)
    await unit_of_work.flush(db)
    return document


async def list_uploaded_documents(
    db: AsyncSession, limit: int, doc_kind: DocKind | None = DocKind.INVOICE
) -> list[SourceDocument]:
    """Newest first; `doc_kind=None` lists every kind."""
    query = select(SourceDocument).where(SourceDocument.uploaded_via == UploadedVia.UPLOAD)
    if doc_kind is not None:
        query = query.where(SourceDocument.doc_kind == doc_kind)
    result = await db.scalars(query.order_by(SourceDocument.created_at.desc()).limit(limit))
    return list(result)


def set_kind(document: SourceDocument, kind: DocKind) -> None:
    document.doc_kind = kind


def save_classification(document: SourceDocument, classification: dict) -> None:
    """The classifier's raw verdict, kept beside the field extraction that may follow it."""
    document.raw_extraction_json = {
        **(document.raw_extraction_json or {}),
        "classification": classification,
    }


def save_extraction(document: SourceDocument, raw: dict, confidences: dict) -> None:
    classification = (document.raw_extraction_json or {}).get("classification")
    document.raw_extraction_json = {**raw, "classification": classification} if classification else raw
    document.field_confidences = confidences
