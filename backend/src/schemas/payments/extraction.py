"""Structured output the LLM must return for one payment / remittance advice."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class PaidInvoice(BaseModel):
    """One invoice the advice says it settles, with the amount it states for it (if any)."""

    invoice_number: str
    amount: Decimal | None = None


class PaymentFieldConfidences(BaseModel):
    """Model's own confidence per field, 0-100; null when the field is absent."""

    amount: int | None = Field(default=None, ge=0, le=100)
    currency: int | None = Field(default=None, ge=0, le=100)
    payment_date: int | None = Field(default=None, ge=0, le=100)
    reference_number: int | None = Field(default=None, ge=0, le=100)
    payer_name: int | None = Field(default=None, ge=0, le=100)
    from_email: int | None = Field(default=None, ge=0, le=100)
    invoices: int | None = Field(default=None, ge=0, le=100)


class ExtractedPayment(BaseModel):
    amount: Decimal | None = Field(
        default=None, description="The total amount actually paid / transferred, as printed"
    )
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. INR")
    payment_date: date | None = None
    reference_number: str | None = Field(
        default=None, description="Payment reference: UTR, NEFT/RTGS/IMPS ref, cheque or transaction no."
    )
    payer_name: str | None = Field(default=None, description="Who paid: the customer")
    payer_bank: str | None = None
    purpose: str | None = None
    from_email: str | None = Field(default=None, description="The sender's e-mail address, if shown")
    invoices: list[PaidInvoice] = Field(default_factory=list)
    po_number: str | None = None
    credit_note_reference: str | None = Field(
        default=None, description="A credit note number the payer says to adjust against"
    )
    credit_note_requested: bool = Field(
        default=False,
        description="True when the payer asks us to use / adjust their credit without a number",
    )
    confidence: PaymentFieldConfidences = Field(default_factory=PaymentFieldConfidences)
