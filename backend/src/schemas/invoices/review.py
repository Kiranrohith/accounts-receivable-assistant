import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, field_validator

from schemas.invoices.extraction import DocumentType, ExtractedInvoice

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ReviewEdits(BaseModel):
    """Fields the reviewer may correct. Anything left out keeps its extracted value."""

    invoice_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    document_type: DocumentType | None = None
    customer_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    customer_gstin: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    total_amount: Decimal | None = None
    po_number: str | None = None

    @field_validator("customer_gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        return "".join(value.split()).upper() if value else None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class ReviewApprove(BaseModel):
    reason: Reason
    customer_id: uuid.UUID | None = None  # pick the customer yourself when matching failed
    edits: ReviewEdits = ReviewEdits()


class ReviewReject(BaseModel):
    reason: Reason


class ReviewSummary(BaseModel):
    review_id: uuid.UUID
    source_document_id: uuid.UUID | None
    file_name: str | None
    uploaded_at: datetime | None
    status: Literal["Pending", "Approved", "Rejected"]
    reasons: list[str]  # why it needed a human
    invoice_number: str | None = None
    customer_name: str | None = None
    total_amount: str | None = None
    matched_customer_id: uuid.UUID | None = None


class ReviewDetail(ReviewSummary):
    extraction: ExtractedInvoice  # the values the reviewer can correct
    matched_customer_name: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    invoice_id: uuid.UUID | None = None  # set once approved


class ReviewDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Approved", "Rejected"]
    invoice_id: uuid.UUID | None = None
    po_hold: bool = False  # created, but on hold: it does not match its purchase order
    po_reasons: list[str] = []
