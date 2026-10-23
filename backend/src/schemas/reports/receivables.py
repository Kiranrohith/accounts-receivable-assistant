import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class AgingBucket(BaseModel):
    key: str  # current, 1-30, 31-60, 61-90, 90+
    label: str
    count: int
    amount: Decimal  # INR still to collect
    disputed_count: int  # of those, flagged as disputed (they stay in the aging, flagged)
    disputed_amount: Decimal


class OverdueCustomer(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    overdue_count: int
    overdue_amount: Decimal
    oldest_days_late: int


class ReceivablesSummary(BaseModel):
    as_of: date  # today in IST: "overdue" and the buckets are counted from it
    outstanding_count: int  # invoices still to collect (Open and PartlyPaid)
    outstanding_amount: Decimal
    overdue_count: int
    overdue_amount: Decimal
    due_soon_count: int  # falling due today or within the next 7 days
    due_soon_amount: Decimal
    disputed_count: int
    disputed_amount: Decimal
    po_hold_count: int  # held because they do not match their purchase order
    received_this_month_count: int
    received_this_month_amount: Decimal
    unapplied_amount: Decimal  # money received that is not against an invoice yet
    aging: list[AgingBucket]
    top_overdue: list[OverdueCustomer]
