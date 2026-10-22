"""Structured output the LLM must return for one customer complaint."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

CategoryValue = Literal[
    "Pricing", "Quality", "Delivery", "AlreadyPaid", "WrongDuplicateInvoice",
    "TaxDiscrepancy", "POMismatch", "CreditNotePending", "Other",
]


class DisputeFieldConfidences(BaseModel):
    """Model's own confidence per field, 0-100; null when the field is absent."""

    category: int | None = Field(default=None, ge=0, le=100)
    payment_related: int | None = Field(default=None, ge=0, le=100)
    customer: int | None = Field(default=None, ge=0, le=100)
    invoices: int | None = Field(default=None, ge=0, le=100)


class ExtractedDispute(BaseModel):
    customer_name: str | None = Field(default=None, description="The customer who is complaining")
    customer_gstin: str | None = None
    from_email: str | None = Field(default=None, description="The sender's e-mail address, if shown")
    invoice_numbers: list[str] = Field(
        default_factory=list, description="Every invoice the complaint is about, as printed"
    )
    po_number: str | None = Field(default=None, description="A purchase order number it mentions")
    summary: str | None = Field(
        default=None, description="What the customer complains about, in one to three sentences"
    )
    category: CategoryValue | None = Field(
        default=None, description="The reason for the complaint; null if it cannot be told"
    )
    payment_related: bool = Field(
        default=False,
        description="The customer says they are withholding or paying less than the invoice, on "
        "purpose, because of this complaint",
    )
    complaint_date: date | None = None
    confidence: DisputeFieldConfidences = Field(default_factory=DisputeFieldConfidences)
