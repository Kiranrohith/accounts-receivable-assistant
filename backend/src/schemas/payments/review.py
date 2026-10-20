import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class MatchLine(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0, description="In the payment's own currency")


class ManualMatch(BaseModel):
    """The reviewer says which invoices this payment pays, and how much of it each."""

    lines: list[MatchLine] = Field(min_length=1)
    customer_id: uuid.UUID | None = Field(
        default=None, description="Required when the payment has no customer yet; overrides it otherwise"
    )
    note: Reason | None = None
    confirm_repayment: bool = Field(
        default=False,
        description="Say yes to put money on an invoice that is already Paid: that amount becomes a "
        "credit note (a re-payment confirmed by you). Without it such a line is refused",
    )
    save_alias: Literal["name", "email"] | None = Field(
        default=None,
        description="Remember the advice's payer name (or sender e-mail) as this customer's alias "
        "(needs customers.alias.confirm)",
    )


class LeaveUnapplied(BaseModel):
    reason: Reason
    customer_id: uuid.UUID | None = Field(
        default=None, description="Required when the payment has no customer yet"
    )


class RejectPayment(BaseModel):
    reason: Reason


class EnterAmount(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str | None = Field(
        default=None, min_length=3, max_length=3, description="Only if the advice did not state one"
    )


class SaveAlias(BaseModel):
    alias_type: Literal["name", "email"]
    customer_id: uuid.UUID | None = None  # default: the payment's customer
    alias_value: Reason | None = None  # default: the advice's payer name / sender e-mail


class AllocationResult(BaseModel):
    invoice_number: str
    allocated: Decimal
    tolerance: str
    invoice_status: str


class ReviewResult(BaseModel):
    payment_id: uuid.UUID
    state: str
    message: str
    allocations: list[AllocationResult] = []
    credit_note_number: str | None = None
    unapplied_amount: Decimal = Decimal("0")
    credited_amount: Decimal = Decimal("0")
    reasons: list[str] = []


class AliasResult(BaseModel):
    alias_id: uuid.UUID
    customer_id: uuid.UUID
    alias_type: str
    alias_value: str
