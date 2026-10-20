import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Fulfillment = Literal["Not invoiced", "Partly invoiced", "Fully invoiced", "Over-invoiced"]


class POListItem(BaseModel):
    purchase_order_id: uuid.UUID
    po_number: str
    customer_id: uuid.UUID
    customer_name: str
    po_date: date | None
    currency: str | None
    total_amount: Decimal | None
    line_count: int
    fulfillment: Fulfillment  # worked out from the quantity ledger, line by line
    linked_invoices: int
    invoices_on_hold: int  # linked invoices that do not match this PO (po_hold)
    source_document_id: uuid.UUID | None
    created_at: datetime | None


class POLineOut(BaseModel):
    line_id: uuid.UUID
    description: str
    quantity_ordered: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    quantity_invoiced: Decimal  # consumed by accepted invoices (ledger sum)
    quantity_remaining: Decimal | None  # negative after an override; None if no quantity ordered


class POLinkedInvoice(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    invoice_status: str
    invoice_date: date
    amount_inr: Decimal
    po_hold: bool
    match_direction: str | None  # po_first / invoice_first / manual
    quantity_counted: Decimal  # quantity this invoice consumed on the PO; 0 while on hold


class PODetail(POListItem):
    source_file_name: str | None
    lines: list[POLineOut]
    invoices: list[POLinkedInvoice]
