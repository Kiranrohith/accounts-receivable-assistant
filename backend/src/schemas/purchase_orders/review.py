import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from schemas.customers.tax_id import TaxIdType
from schemas.invoices.review import Reason
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ---------------------------------------------------------------- extraction review (uploader)
class POLineEdit(BaseModel):
    description: Text
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class POReviewEdits(BaseModel):
    """Fields the reviewer may correct. Anything left out keeps its extracted value.
    `line_items`, when sent, replaces the whole list."""

    po_number: Text | None = None
    po_date: date | None = None
    customer_name: Text | None = None
    tax_id_type: TaxIdType | None = None
    tax_id_value: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    line_items: list[POLineEdit] | None = None

    @field_validator("tax_id_value")
    @classmethod
    def _tax_id(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class POReviewApprove(BaseModel):
    reason: Reason
    customer_id: uuid.UUID | None = None
    # remember the customer name read from the PO as an alias of the customer (needs customers.alias.confirm)
    save_alias: bool = False
    edits: POReviewEdits = POReviewEdits()


class POReviewReject(BaseModel):
    reason: Reason


class POReviewSummary(BaseModel):
    review_id: uuid.UUID
    source_document_id: uuid.UUID | None
    file_name: str | None
    uploaded_at: datetime | None
    status: Literal["Pending", "Approved", "Rejected"]
    reasons: list[str]
    po_number: str | None = None
    customer_name: str | None = None
    total_amount: str | None = None
    matched_customer_id: uuid.UUID | None = None


class POReviewDetail(POReviewSummary):
    extraction: ExtractedPurchaseOrder
    matched_customer_name: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    purchase_order_id: uuid.UUID | None = None


class POReviewDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Approved", "Rejected"]
    purchase_order_id: uuid.UUID | None = None
    linked_invoices: list[dict] = []  # invoices linked and checked when the PO was created
    alias_saved: str | None = None  # the name now remembered as the customer's alias, when one was saved


# ---------------------------------------------------------------- po_mismatch review
class MismatchLine(BaseModel):
    invoice_line_item_id: str
    description: str
    po_line_item_id: str | None
    ordered: str | None
    already_invoiced: str
    this_invoice: str | None
    remaining_after: str | None  # negative = over the ordered quantity
    ok: bool
    reason: str | None = None


class MismatchSummary(BaseModel):
    review_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str | None
    customer_id: uuid.UUID | None
    po_number: str | None
    status: Literal["Pending", "Approved", "Rejected"]
    reasons: list[str]
    created_at: datetime | None


class MismatchDetail(MismatchSummary):
    lines: list[MismatchLine]
    po_hold: bool
    decision_reason: str | None = None
    decided_at: datetime | None = None


class OverrideBody(BaseModel):
    reason: Reason


class RecheckLine(BaseModel):
    description: Text
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    tax_rate: Decimal | None = None


class RecheckBody(BaseModel):
    """The corrected invoice lines (a full replacement set, saved as a new line-item version)."""

    lines: list[RecheckLine] = Field(min_length=1)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class MismatchDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Pending", "Approved"]  # re-check that still fails stays Pending
    invoice_id: uuid.UUID
    po_hold: bool
    reasons: list[str] = []
    lines: list[MismatchLine] = []
