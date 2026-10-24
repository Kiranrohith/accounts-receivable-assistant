"""Structured output the LLM must return for one invoice document."""

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from schemas.customers.tax_id import TaxIdFields, from_legacy


class DocumentType(str, Enum):
    INVOICE = "INVOICE"
    DEBIT_NOTE = "DEBIT_NOTE"


class TaxLine(BaseModel):
    type: str = Field(description="Tax name, e.g. CGST, SGST, IGST, VAT, GST")
    rate: Decimal | None = Field(default=None, description="Percent, e.g. 9 for 9%")
    amount: Decimal = Field(description="Tax amount in the invoice currency")


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    tax_rate: Decimal | None = Field(default=None, description="Percent")


class FieldConfidences(BaseModel):
    """Model's own confidence per field, 0-100; null when the field is absent."""

    invoice_number: int | None = Field(default=None, ge=0, le=100)
    customer_name: int | None = Field(default=None, ge=0, le=100)
    tax_id: int | None = Field(default=None, ge=0, le=100)
    invoice_date: int | None = Field(default=None, ge=0, le=100)
    due_date: int | None = Field(default=None, ge=0, le=100)
    currency: int | None = Field(default=None, ge=0, le=100)
    subtotal: int | None = Field(default=None, ge=0, le=100)
    tax: int | None = Field(default=None, ge=0, le=100)
    total_amount: int | None = Field(default=None, ge=0, le=100)
    line_items: int | None = Field(default=None, ge=0, le=100)
    po_number: int | None = Field(default=None, ge=0, le=100)


class ExtractedInvoice(TaxIdFields):
    document_type: DocumentType = DocumentType.INVOICE
    invoice_number: str | None = None
    customer_name: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. INR")
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    tax_breakdown: list[TaxLine] = Field(default_factory=list)
    total_amount: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    po_number: str | None = None
    confidence: FieldConfidences = Field(default_factory=FieldConfidences)

    @model_validator(mode="before")
    @classmethod
    def _read_old_gstin(cls, data):  # noqa: ANN001, ANN206
        return from_legacy(data)
