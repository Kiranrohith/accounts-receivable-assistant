"""Disputes (a customer's complaint about an invoice), their correspondence, and notifications."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, CreatedMixin, uuid_pk
from data.models.postgres.enums import (
    DisputeCategory,
    DisputeMessageKind,
    DisputeStatus,
    NotificationType,
)


def _enum(cls: type, name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Dispute(Base, AuditMixin):
    """Raised -> Categorised -> ClarificationSent or Assigned -> Resolved, plus Escalated.

    Deadlines are DATE columns (working days, IST): first response in 2, resolution in 10.
    At most one dispute per invoice is open at a time; a follow-up is appended to it (E2).
    """

    __tablename__ = "disputes"
    __table_args__ = (
        Index(
            "uq_disputes_one_open_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status <> 'Resolved' AND invoice_id IS NOT NULL"),
        ),
        Index("ix_disputes_status", "status"),
        Index("ix_disputes_customer", "customer_id"),
        Index("ix_disputes_assigned", "assigned_to", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id")
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), comment="NULL = a customer-level dispute (E1)"
    )
    linked_dispute_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("disputes.id"),
        comment="a reply after resolution opens a new dispute linked to the old one (E3)",
    )
    ingested_email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingested_emails.id")
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_documents.id"),
        comment="the uploaded complaint this dispute was read from",
    )
    category: Mapped[DisputeCategory | None] = mapped_column(_enum(DisputeCategory, "dispute_category"))
    status: Mapped[DisputeStatus] = mapped_column(
        _enum(DisputeStatus, "dispute_status"),
        default=DisputeStatus.RAISED,
        server_default="Raised",
        nullable=False,
    )
    payment_related: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False,
        comment="the customer withheld or short-paid on purpose: this one can hold a payment",
    )
    classification_confidence: Mapped[int | None] = mapped_column(Integer)
    clarification_confidence: Mapped[int | None] = mapped_column(
        Integer, comment="gates the FIRST clarification draft only; later drafts are human-sent"
    )
    summary: Mapped[str | None] = mapped_column(Text, comment="what the customer complains about")
    from_email: Mapped[str | None] = mapped_column(String)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    classified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), comment="NULL = the reader classified it"
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_response_due: Mapped[date | None] = mapped_column(Date)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_due: Mapped[date | None] = mapped_column(Date)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    gmail_thread_id: Mapped[str | None] = mapped_column(String)
    resolution_narrative: Mapped[str | None] = mapped_column(Text)


class DisputeMessage(Base, CreatedMixin):
    """Append-only correspondence: inbound follow-ups (E2), drafts, sent replies, internal notes."""

    __tablename__ = "dispute_messages"
    __table_args__ = (
        Index(
            "uq_dispute_messages_thread_evidence",
            "dispute_id",
            "gmail_message_id",
            unique=True,
            postgresql_where=text("gmail_message_id IS NOT NULL"),
        ),
        Index("ix_dispute_messages_dispute", "dispute_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disputes.id"), nullable=False
    )
    kind: Mapped[DisputeMessageKind] = mapped_column(
        _enum(DisputeMessageKind, "dispute_message_kind"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="drafted clarifications persist here")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gmail_message_id: Mapped[str | None] = mapped_column(String)
    ingested_email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingested_emails.id")
    )


class Notification(Base, AuditMixin):
    """In-app notice to one user (mutable: is_read)."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_recipient", "recipient_user_id", "is_read", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        _enum(NotificationType, "notification_type"), nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
