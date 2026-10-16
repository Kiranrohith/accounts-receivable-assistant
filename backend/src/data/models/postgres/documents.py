"""Ingestion tables: ingested_emails (mail registry) and source_documents (uploads)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, CreatedMixin, uuid_pk
from data.models.postgres.enums import (
    DocKind,
    DocType,
    EmailClassification,
    EmailStatus,
    UploadedVia,
)


def _enum(cls: type, name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class IngestedEmail(Base, AuditMixin):
    __tablename__ = "ingested_emails"

    id: Mapped[uuid.UUID] = uuid_pk()
    gmail_message_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, comment="idempotency anchor"
    )
    gmail_thread_id: Mapped[str | None] = mapped_column(String)
    history_id: Mapped[str | None] = mapped_column(String)
    sender: Mapped[str | None] = mapped_column(String)
    subject: Mapped[str | None] = mapped_column(String)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification: Mapped[EmailClassification | None] = mapped_column(
        _enum(EmailClassification, "email_classification")
    )
    classification_confidence: Mapped[int | None] = mapped_column(Integer)
    label_applied: Mapped[str | None] = mapped_column(String)
    status: Mapped[EmailStatus | None] = mapped_column(_enum(EmailStatus, "email_status"))


class SourceDocument(Base, CreatedMixin):
    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(
            "(uploaded_via = 'email_body') = (file_hash IS NULL)",
            name="ck_source_documents_hash_iff_not_body",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    doc_type: Mapped[DocType | None] = mapped_column(_enum(DocType, "doc_type"))
    uploaded_via: Mapped[UploadedVia | None] = mapped_column(
        _enum(UploadedVia, "uploaded_via")
    )
    doc_kind: Mapped[DocKind] = mapped_column(
        _enum(DocKind, "doc_kind"),
        default=DocKind.INVOICE,
        server_default="invoice",
        nullable=False,
        comment="which pipeline reads this document: invoice or purchase order",
    )
    ingested_email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingested_emails.id")
    )
    file_name: Mapped[str | None] = mapped_column(String)
    file_path: Mapped[str | None] = mapped_column(String)
    file_hash: Mapped[str | None] = mapped_column(
        String, unique=True, comment="sha256; NULL for email_body sources"
    )
    raw_extraction_json: Mapped[dict | None] = mapped_column(JSONB)
    field_confidences: Mapped[dict | None] = mapped_column(JSONB)
