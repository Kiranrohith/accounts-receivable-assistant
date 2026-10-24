import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, StringConstraints, field_validator, model_validator

from schemas.customers.tax_id import TaxIdType


def _collapse_spaces(value: object) -> object:
    return " ".join(value.split()) if isinstance(value, str) else value


# Trimmed, inner whitespace collapsed ("Acme   Traders" -> "Acme Traders"), never empty.
Text = Annotated[
    str,
    BeforeValidator(_collapse_spaces),
    StringConstraints(min_length=1, max_length=200),
]


class CustomerCreate(BaseModel):
    """Every field is required."""

    customer_name: Text
    contact: Text
    email: EmailStr
    tax_id: str  # checked against its type and stored normalized by the service
    tax_id_type: TaxIdType = TaxIdType.GSTIN
    deducts_tds: bool
    billing_currency: str
    country: Text

    @field_validator("billing_currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("billing_currency must be a 3-letter ISO code, e.g. INR")
        return value


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_name: str
    contact: str | None
    email: str | None
    tax_id: str | None
    tax_id_type: TaxIdType | None
    deducts_tds: bool
    billing_currency: str | None
    country: str | None
    is_active: bool
    created_at: datetime | None


class CustomerListItem(CustomerOut):
    """A customer with the figures the list shows: what is owed and what is unapplied."""

    open_invoices: int = 0  # Open and PartlyPaid
    overdue_invoices: int = 0  # ... past their due date (IST)
    outstanding: Decimal = Decimal("0.00")  # INR still to collect on those
    unapplied_cash: Decimal = Decimal("0.00")  # INR received from them and not yet against an invoice


class CustomerUpdate(BaseModel):
    """Fields to change; anything left out stays as it is. `deducts_tds` only affects invoices created from now on:
    use the recompute action for the ones already saved."""

    customer_name: Text | None = None
    contact: Text | None = None
    email: EmailStr | None = None
    tax_id: str | None = None
    tax_id_type: TaxIdType | None = None
    deducts_tds: bool | None = None
    billing_currency: str | None = None
    country: Text | None = None

    @field_validator("billing_currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("billing_currency must be a 3-letter ISO code, e.g. INR")
        return value

    @model_validator(mode="after")
    def _something_to_change(self) -> "CustomerUpdate":
        if not self.model_fields_set or all(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("send at least one field to change")
        return self


class AliasCreate(BaseModel):
    alias_type: Literal["name", "email"]
    alias_value: Text


class AliasOut(BaseModel):
    alias_id: uuid.UUID
    alias_type: str
    alias_value: str
    source: str | None
    confirmed_by: str | None  # who confirmed it
    created_at: datetime | None


class CustomerDetail(CustomerListItem):
    aliases: list[AliasOut]
    # Non-paid invoices still carrying the OLD TDS treatment: what the recompute action would change.
    tds_out_of_date: int = 0


class TdsSkip(BaseModel):
    invoice_number: str
    reason: str


class TdsRecomputeResult(BaseModel):
    applied: int  # open invoices updated as a new version
    sent_to_review: int  # part-paid or disputed: a decision is waiting for you (Take Action)
    unchanged: int
    skipped: list[TdsSkip]
    message: str


class TdsReviewDetail(BaseModel):
    review_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str | None
    customer_name: str | None
    status: Literal["Pending", "Approved", "Rejected"]
    deducts_tds: bool  # the treatment the customer now has
    reasons: list[str]
    currency: str
    old_tds: Decimal
    new_tds: Decimal
    old_collectible: Decimal
    new_collectible: Decimal
    amount_paid: Decimal
    decision_reason: str | None = None
    decided_at: datetime | None = None


class TdsReviewDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Approved", "Rejected"]
    invoice_id: uuid.UUID
    version: int | None = None


class TdsDecide(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
