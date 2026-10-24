"""Invoices, their line items, and the two places a document can end up instead of
becoming an invoice: pending_invoice_changes (needs a human) and rejected_invoices."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, CreatedMixin, uuid_pk
from data.models.postgres.purchase_orders import PO_NO_NORM_SQL
from data.models.postgres.enums import (
    InvoiceStatus,
    InvoiceType,
    PendingChangeReason,
    PendingChangeStatus,
    RejectionResolution,
)

MONEY = Numeric(18, 2)
# The generated invoices.invoice_number_norm; core.services.invoices.numbers holds the same rule in Python.
INVOICE_NO_NORM_SQL = "upper(regexp_replace(invoice_number, '[^A-Za-z0-9]', '', 'g'))"


def _enum(cls: type, name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Invoice(Base, AuditMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_customer_status", "customer_id", "status"),
        Index("ix_invoices_customer_po_norm", "customer_id", "po_number_norm"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    invoice_number_norm: Mapped[str] = mapped_column(
        String,
        Computed(INVOICE_NO_NORM_SQL, persisted=True),
        unique=True,
        nullable=False,
        comment="generated: upper-case letters and digits of invoice_number only (INV-1176 = INV1176)",
    )
    type: Mapped[InvoiceType] = mapped_column(
        _enum(InvoiceType, "invoice_type"),
        default=InvoiceType.INVOICE,
        server_default="INVOICE",
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"),
        default=InvoiceStatus.OPEN,
        server_default="Open",
        nullable=False,
    )
    is_disputed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    invoice_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    po_number: Mapped[str | None] = mapped_column(
        String, comment="matching key for the purchase-order link; invoices without a PO are valid"
    )
    po_number_norm: Mapped[str | None] = mapped_column(
        String,
        Computed(PO_NO_NORM_SQL, persisted=True),
        comment="generated: upper-case letters and digits of po_number only (PO-123 = PO 123); NULL without a PO",
    )
    po_hold: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
        comment="true while a po_mismatch review is pending (mirrors is_disputed)",
    )
    subtotal: Mapped[Decimal | None] = mapped_column(MONEY, comment="invoice currency")
    tax_total: Mapped[Decimal | None] = mapped_column(MONEY, comment="invoice currency")
    tax_breakdown: Mapped[list | None] = mapped_column(JSONB)
    amount_inr: Mapped[Decimal] = mapped_column(MONEY, nullable=False, comment="gross INR")
    amount_foreign: Mapped[Decimal | None] = mapped_column(MONEY)
    fx_rate_invoice: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), comment="frozen at creation"
    )
    tds_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    collectible_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), server_default=text("0"), nullable=False
    )
    collectible_balance: Mapped[Decimal] = mapped_column(
        MONEY, Computed("collectible_total - amount_paid", persisted=True)
    )
    line_items_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_documents.id"),
        comment="DEBIT_NOTE only: system-rendered PDF for its reminder emails",
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )


class InvoiceVersion(Base, CreatedMixin):
    """Superseded invoice snapshots (append-only). Table only: version-check logic comes later."""

    __tablename__ = "invoice_versions"
    __table_args__ = (UniqueConstraint("invoice_id", "version_no", name="uq_invoice_versions_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[dict | None] = mapped_column(JSONB)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_documents.id"),
        comment="NULL for TDS-recompute versions",
    )


class InvoiceLineItem(Base, CreatedMixin):
    __tablename__ = "invoice_line_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    line_total: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))


class PendingInvoiceChange(Base, CreatedMixin):
    """Single review store; here only reason=extraction_review (invoice_id is NULL)."""

    __tablename__ = "pending_invoice_changes"
    __table_args__ = (
        Index(
            "uq_pending_invoice_changes_one_pending",
            "invoice_id",
            unique=True,
            postgresql_where=text("status = 'Pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), comment="NULL = new-invoice review"
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )
    reason: Mapped[PendingChangeReason] = mapped_column(
        _enum(PendingChangeReason, "pending_change_reason"), nullable=False
    )
    proposed_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[PendingChangeStatus] = mapped_column(
        _enum(PendingChangeStatus, "pending_change_status"),
        default=PendingChangeStatus.PENDING,
        server_default="Pending",
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(
        Text, comment="the reviewer's reason when approving or rejecting"
    )


class RejectedInvoice(Base, AuditMixin):
    __tablename__ = "rejected_invoices"
    __table_args__ = (
        CheckConstraint(
            "source_document_id IS NOT NULL OR duplicate_of_source_document_id IS NOT NULL",
            name="ck_rejected_invoices_has_a_document",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )
    duplicate_of_source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )
    attempted_file_name: Mapped[str | None] = mapped_column(String)
    extracted_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    rejection_reasons: Mapped[list | None] = mapped_column(JSONB, comment="all reasons")
    resolution: Mapped[RejectionResolution] = mapped_column(
        _enum(RejectionResolution, "rejection_resolution"),
        default=RejectionResolution.NONE,
        server_default="None",
        nullable=False,
    )
    created_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
