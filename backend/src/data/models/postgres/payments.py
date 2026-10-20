"""Payments (from remittance advices), what they were applied to, and credit notes.

Money identities (verified by core.services.payments.identities):
  1. invoices.amount_paid = sum of Active allocations (payment- and note-funded) per invoice
  2. payments.amount_received_inr = sum of Active (allocated_amount + tolerance_residual)
     + unapplied_amount + held_amount + credited_amount   (states where it is enforced)
  3. notes.remaining_amount = notes.amount - sum of Active note-funded allocations
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
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

from data.models.postgres.base import AuditMixin, Base, uuid_pk
from data.models.postgres.enums import (
    AllocationStatus,
    MatchedLayer,
    NoteStatus,
    PaymentState,
    Tolerance,
)

MONEY = Numeric(18, 2)
_ZERO = text("0")


def _enum(cls: type, name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Payment(Base, AuditMixin):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("source_document_id", name="uq_payments_source_document"),
        Index("ix_payments_reference_customer", "reference_number", "customer_id"),
        Index("ix_payments_state", "state"),
        Index(
            "idx_payments_unapplied",
            "customer_id",
            postgresql_where=text("unapplied_amount > 0"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    state: Mapped[PaymentState] = mapped_column(
        _enum(PaymentState, "payment_state"),
        default=PaymentState.RECEIVED,
        server_default="Received",
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        comment="resolved identity, set the moment the waterfall resolves it",
    )
    payment_currency: Mapped[str | None] = mapped_column(CHAR(3))
    amount_received_foreign: Mapped[Decimal | None] = mapped_column(
        MONEY, comment="as received, in payment_currency; only for a non-INR payment"
    )
    amount_received_inr: Mapped[Decimal | None] = mapped_column(
        MONEY,
        comment="AUTHORITATIVE identity basis; a foreign payment gets it when matched "
        "(received x the paid invoices' frozen rate)",
    )
    unapplied_amount: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), server_default=_ZERO, nullable=False,
        comment="unclaimed residual only",
    )
    held_amount: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), server_default=_ZERO, nullable=False,
        comment="dispute-held portion (the dispute module is not built yet: always 0)",
    )
    credited_amount: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), server_default=_ZERO, nullable=False,
        comment="sum of credit notes born from this payment",
    )
    payment_date: Mapped[date | None] = mapped_column(Date)
    reference_number: Mapped[str | None] = mapped_column(String, comment="junk refs never dedup")
    payee_name: Mapped[str | None] = mapped_column(String, comment="who paid, as written")
    payee_bank: Mapped[str | None] = mapped_column(String)
    purpose: Mapped[str | None] = mapped_column(String)
    from_email: Mapped[str | None] = mapped_column(String, comment="L2 sender key")
    extracted_invoice_numbers: Mapped[list | None] = mapped_column(JSONB)
    extracted_po: Mapped[str | None] = mapped_column(String, comment="display only")
    extracted_credit_note_ref: Mapped[str | None] = mapped_column(
        String, comment="a note_number the remittance quotes: the SPECIFIC spend instruction"
    )
    credit_note_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False,
        comment="GENERIC 'use our credit' instruction",
    )
    confidence_score: Mapped[int | None] = mapped_column(
        Integer, comment="stored clamped at 100; ties are decided on raw scores"
    )
    matched_layer: Mapped[MatchedLayer | None] = mapped_column(_enum(MatchedLayer, "matched_layer"))
    match_reason: Mapped[str | None] = mapped_column(Text)
    review_reasons: Mapped[list | None] = mapped_column(
        JSONB, comment="plain-English reasons while the payment waits for a person"
    )
    rematch_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_ZERO, nullable=False,
        comment="contention requeues; capped, then review",
    )
    held_for_dispute_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("disputes.id"),
        comment="non-null = a hold is active: that portion sits in held_amount",
    )
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id")
    )
    ingested_email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingested_emails.id")
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )


class PaymentAllocation(Base, AuditMixin):
    """What a payment (or a credit note) paid, invoice by invoice. Exactly one funder."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint(
            "(payment_id IS NULL) <> (note_id IS NULL)", name="ck_allocations_one_funder"
        ),
        CheckConstraint(
            "note_id IS NULL OR tolerance_residual IS NULL OR tolerance_residual = 0",
            name="ck_allocations_note_exact",
        ),
        CheckConstraint(
            "spent_for_payment_id IS NULL OR note_id IS NOT NULL", name="ck_allocations_spend_trace"
        ),
        Index("ix_payment_allocations_payment", "payment_id"),
        Index("ix_payment_allocations_spent_for_payment", "spent_for_payment_id"),
        Index("ix_payment_allocations_note", "note_id"),
        Index("ix_payment_allocations_invoice", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id")
    )
    note_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notes.id"))
    spent_for_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id"),
        comment="the payment whose 'use our credit' instruction caused this note-funded spend",
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    allocated_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    matched_layer: Mapped[MatchedLayer | None] = mapped_column(_enum(MatchedLayer, "matched_layer"))
    tolerance_applied: Mapped[Tolerance | None] = mapped_column(_enum(Tolerance, "tolerance"))
    fx_rate_used: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), comment="the invoice's frozen rate used for this allocation"
    )
    tolerance_residual: Mapped[Decimal | None] = mapped_column(
        MONEY, comment="SIGNED: received - allocated (T1 rounding and T2 band absorption)"
    )
    tds_deducted: Mapped[Decimal | None] = mapped_column(MONEY)
    allocated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), comment="NULL = the system"
    )
    status: Mapped[AllocationStatus] = mapped_column(
        _enum(AllocationStatus, "allocation_status"),
        default=AllocationStatus.ACTIVE,
        server_default="Active",
        nullable=False,
    )


class Note(Base, AuditMixin):
    """Credit notes only (a debit note is an invoice of type DEBIT_NOTE)."""

    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint("currency = 'INR'", name="ck_notes_inr"),
        CheckConstraint("remaining_amount >= 0 AND remaining_amount <= amount", name="ck_notes_remaining"),
        Index("ix_notes_customer_status", "customer_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    note_number: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, comment="quotable by a remittance"
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        CHAR(3), default="INR", server_default="INR", nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    linked_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id")
    )
    linked_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id")
    )
    status: Mapped[NoteStatus] = mapped_column(
        _enum(NoteStatus, "note_status"),
        default=NoteStatus.OPEN,
        server_default="Open",
        nullable=False,
    )
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_documents.id"),
        comment="customer-facing PDF; rendering comes with the template phase",
    )
