import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class InvoiceListItem(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    type: Literal["INVOICE", "DEBIT_NOTE"]
    status: Literal["Open", "PartlyPaid", "Paid"]
    customer_id: uuid.UUID
    customer_name: str
    invoice_date: date
    due_date: date
    invoice_currency: str
    amount_foreign: Decimal | None  # the total in its own currency; None for INR invoices
    fx_rate: Decimal | None  # INR per unit, frozen at creation (1 for INR)
    amount_inr: Decimal
    tds_amount: Decimal | None
    collectible_total: Decimal  # what we expect to receive (INR, after TDS)
    amount_paid: Decimal
    collectible_balance: Decimal  # still to collect
    is_disputed: bool
    po_hold: bool  # held: it does not match its purchase order (see the mismatch reviews)
    po_number: str | None
    version: int
    source_document_id: uuid.UUID | None
    created_at: datetime | None


class InvoiceLineOut(BaseModel):
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    tax_rate: Decimal | None


class LinkedPO(BaseModel):
    purchase_order_id: uuid.UUID
    po_number: str
    match_direction: str | None


class InvoiceDetail(InvoiceListItem):
    subtotal: Decimal | None
    tax_total: Decimal | None
    tax_breakdown: list | None
    line_items: list[InvoiceLineOut]
    source_file_name: str | None
    linked_po: LinkedPO | None = None
    po_mismatch_review_id: uuid.UUID | None = None  # set while the PO hold is waiting for a decision


class RejectedItem(BaseModel):
    rejected_id: uuid.UUID
    source_document_id: uuid.UUID | None
    kind: Literal["invoice", "purchase_order", "other"]
    file_name: str | None
    rejected_at: datetime | None
    reasons: list[str]  # all of them, in plain English
    reason_codes: list[str]
    resolution: str  # None, Reuploaded, CustomerAdded, Discarded
    file_kept: bool  # false only if the file is no longer on disk
    can_send_to_review: bool = False  # a "neither" rejection its uploader can send back for a type decision
    invoice_number: str | None = None  # what the reader found, when it found one
    po_number: str | None = None
    customer_name: str | None = None
    total_amount: str | None = None
