"""Structured output the LLM must return for one purchase-order document."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from schemas.customers.tax_id import TaxIdFields, from_legacy
from schemas.quantity import Quantity


class POLineItem(BaseModel):
    description: str
    quantity: Quantity = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class POFieldConfidences(BaseModel):
    """Model's own confidence per field, 0-100; null when the field is absent."""

    po_number: int | None = Field(default=None, ge=0, le=100)
    po_date: int | None = Field(default=None, ge=0, le=100)
    customer_name: int | None = Field(default=None, ge=0, le=100)
    tax_id: int | None = Field(default=None, ge=0, le=100)
    currency: int | None = Field(default=None, ge=0, le=100)
    total_amount: int | None = Field(default=None, ge=0, le=100)
    line_items: int | None = Field(default=None, ge=0, le=100)


class ExtractedPurchaseOrder(TaxIdFields):
    po_number: str | None = None
    po_date: date | None = None
    customer_name: str | None = None
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. INR")
    total_amount: Decimal | None = None
    line_items: list[POLineItem] = Field(default_factory=list)
    confidence: POFieldConfidences = Field(default_factory=POFieldConfidences)

    @model_validator(mode="before")
    @classmethod
    def _read_old_gstin(cls, data):  # noqa: ANN001, ANN206
        return from_legacy(data)
