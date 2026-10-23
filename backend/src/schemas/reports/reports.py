import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from schemas.reports.receivables import AgingBucket


class AgingRow(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    status: str  # Open or PartlyPaid
    due_date: date
    days_late: int  # negative before the due date
    bucket: str  # current, 1-30, 31-60, 61-90, 90+
    balance: Decimal  # INR still to collect
    is_disputed: bool  # a disputed invoice stays in its bucket, flagged


class AgingReport(BaseModel):
    as_of: date  # today in IST: days late are counted from it
    buckets: list[AgingBucket]  # always every bucket, whatever the filter on the rows
    total_count: int
    total_amount: Decimal
    items: list[AgingRow]  # one page of the invoices, the most late first
    total: int  # how many invoices match the filter


class PaidRow(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    invoice_date: date
    due_date: date
    paid_on: date  # the day the last money was applied (IST)
    days_to_pay: int  # from the invoice date to paid_on
    paid_late_days: int  # days after the due date it was paid; 0 when on time
    amount_inr: Decimal  # gross
    tds_amount: Decimal | None
    amount_paid: Decimal  # what was actually received against it


class PaidReport(BaseModel):
    paid_from: date | None
    paid_to: date | None
    count: int
    amount_received: Decimal
    items: list[PaidRow]
    total: int


class StatusRow(BaseModel):
    status: str  # Open, PartlyPaid or Paid
    count: int
    invoiced_amount: Decimal
    paid_amount: Decimal
    balance: Decimal


class StatusReport(BaseModel):
    as_of: date
    rows: list[StatusRow]
    total_count: int
    total_invoiced: Decimal
    total_paid: Decimal
    total_balance: Decimal
