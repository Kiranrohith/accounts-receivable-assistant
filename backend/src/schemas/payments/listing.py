import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

PaymentStateName = Literal[
    "Received", "Reading", "Matching", "Applied", "PartiallyApplied", "Unapplied",
    "NeedsReview", "Rejected", "Held", "Failed",
]


class PaymentListItem(BaseModel):
    payment_id: uuid.UUID
    state: PaymentStateName
    customer_id: uuid.UUID | None
    customer_name: str | None
    payer_name: str | None  # who paid, as written on the advice
    currency: str | None
    amount: Decimal | None  # as received, in `currency`
    amount_received_inr: Decimal | None  # the INR value once known (always, for an INR payment)
    reference_number: str | None
    payment_date: date | None
    matched_layer: str | None  # L1 invoice number, L2 sender e-mail, L3 payer name, L4 a person
    confidence_score: int | None
    unapplied_amount: Decimal
    held_amount: Decimal
    credited_amount: Decimal
    reasons: list[str]  # why it waits for a person (empty otherwise)
    source_document_id: uuid.UUID | None
    file_name: str | None
    created_at: datetime | None


class AllocationOut(BaseModel):
    allocation_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
    allocated_amount: Decimal
    status: Literal["Active", "Reversed"]
    tolerance: str | None  # T1 rounding, T2 FX band, T3 over, T4 short
    tolerance_residual: Decimal | None  # signed: received - allocated
    fx_rate_used: Decimal | None
    matched_layer: str | None
    created_at: datetime | None


class NoteRef(BaseModel):
    note_id: uuid.UUID
    note_number: str
    amount: Decimal
    remaining_amount: Decimal
    status: str


class QuotedInvoice(BaseModel):
    invoice_number: str
    stated_amount: Decimal | None  # what the advice says it paid for this invoice
    known: bool  # we have this invoice (for this customer)
    invoice_status: str | None = None


class OpenInvoiceOut(BaseModel):
    """What a reviewer can still apply this payment to."""

    invoice_id: uuid.UUID
    invoice_number: str
    due_date: date
    invoice_currency: str
    fx_rate: Decimal | None
    collectible_balance: Decimal  # INR
    status: str
    po_hold: bool
    is_disputed: bool


class TimelineEntry(BaseModel):
    at: datetime
    from_state: str | None
    to_state: str
    reason: str | None


class PaymentDetail(PaymentListItem):
    payer_bank: str | None
    purpose: str | None
    from_email: str | None
    extracted_po: str | None
    credit_note_reference: str | None
    credit_note_requested: bool
    quoted_invoices: list[QuotedInvoice]
    match_reason: str | None
    rematch_count: int
    duplicate_of: uuid.UUID | None
    allocations: list[AllocationOut]
    notes: list[NoteRef]  # credit notes born from this payment
    open_invoices: list[OpenInvoiceOut]  # shown while a person can still decide it
    timeline: list[TimelineEntry]
    money_identity_ok: bool  # the received amount equals everything it was split into
    extraction: dict | None  # what the reader took from the advice


class InvoicePayment(BaseModel):
    """One allocation on an invoice (shown in the invoice's detail)."""

    allocation_id: uuid.UUID
    funded_by: Literal["payment", "credit_note"]
    payment_id: uuid.UUID | None
    payment_reference: str | None
    note_number: str | None
    allocated_amount: Decimal
    status: Literal["Active", "Reversed"]
    tolerance: str | None
    tolerance_residual: Decimal | None
    created_at: datetime | None
