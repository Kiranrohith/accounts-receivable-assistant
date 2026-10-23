"""The three reports: invoices by status, the aging of what is still to collect, and what was paid in a window of days.

Each is read on screen (JSON, a page of rows at a time) and downloaded as a PDF (every row).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.disputes.calendar import today_ist
from core.services.reports.pdf import inr, table_pdf
from data.repositories.reports import receivables_repo
from schemas.reports.receivables import AgingBucket
from schemas.reports.reports import AgingReport, AgingRow, PaidReport, PaidRow, StatusReport, StatusRow

_STATUS_NAME = {"Open": "Open", "PartlyPaid": "Part-paid", "Paid": "Paid"}
_BUCKET_NAME = {key: label for key, label, _, _ in receivables_repo.BUCKETS}


def _date(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else "-"


# ------------------------------------------------------------------------------------------------- invoice status
async def status_report(db: AsyncSession) -> StatusReport:
    rows = [StatusRow(**r) for r in await receivables_repo.status_summary(db)]
    return StatusReport(
        as_of=today_ist(),
        rows=rows,
        total_count=sum(r.count for r in rows),
        total_invoiced=sum((r.invoiced_amount for r in rows), Decimal(0)),
        total_paid=sum((r.paid_amount for r in rows), Decimal(0)),
        total_balance=sum((r.balance for r in rows), Decimal(0)),
    )


async def status_pdf(db: AsyncSession) -> bytes:
    report = await status_report(db)
    return table_pdf(
        title="Invoices by status",
        lines=[f"As of {_date(report.as_of)}. Amounts are in rupees."],
        headers=["Status", "Invoices", "Invoiced", "Received", "Still to collect"],
        widths=[60, 30, 60, 60, 60],
        right=[1, 2, 3, 4],
        rows=[[_STATUS_NAME[r.status], str(r.count), inr(r.invoiced_amount), inr(r.paid_amount), inr(r.balance)] for r in report.rows],
        summary=[("All invoices", str(report.total_count)), ("Invoiced", inr(report.total_invoiced)), ("Received", inr(report.total_paid)), ("Still to collect", inr(report.total_balance))],
    )


# ------------------------------------------------------------------------------------------------------- aging
async def aging_report(
    db: AsyncSession, *, bucket: str | None, customer_id: uuid.UUID | None, sort: str, descending: bool, limit: int | None, offset: int
) -> AgingReport:
    today = today_ist()
    buckets = [AgingBucket(**b) for b in await receivables_repo.aging(db, today)]
    rows, total = await receivables_repo.aging_rows(
        db, today, bucket=bucket, customer_id=customer_id, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return AgingReport(
        as_of=today,
        buckets=buckets,
        total_count=sum(b.count for b in buckets),
        total_amount=sum((b.amount for b in buckets), Decimal(0)),
        items=[AgingRow(**r) for r in rows],
        total=total,
    )


async def aging_pdf(db: AsyncSession, *, bucket: str | None, customer_id: uuid.UUID | None) -> bytes:
    report = await aging_report(db, bucket=bucket, customer_id=customer_id, sort="days_late", descending=True, limit=None, offset=0)
    scope = _BUCKET_NAME[bucket] if bucket else "all invoices still to collect"
    summary = [(f"{b.label}", f"{inr(b.amount)}  ({b.count} invoices{f', {b.disputed_count} disputed' if b.disputed_count else ''})") for b in report.buckets]
    summary.append(("Total still to collect", f"{inr(report.total_amount)}  ({report.total_count} invoices)"))
    return table_pdf(
        title="Aging report",
        lines=[f"As of {_date(report.as_of)}. Days late are counted from the due date. Showing: {scope}. Disputed invoices stay in their band and are marked."],
        headers=["Invoice", "Customer", "Due", "Days late", "Band", "Still to collect", "Disputed"],
        widths=[40, 85, 30, 25, 40, 40, 17],
        right=[3, 5],
        rows=[
            [r.invoice_number, r.customer_name, _date(r.due_date), str(r.days_late) if r.days_late > 0 else "not yet due", _BUCKET_NAME[r.bucket], inr(r.balance), "yes" if r.is_disputed else ""]
            for r in report.items
        ],
        summary=summary,
        empty="No invoice is waiting to be collected.",
    )


# ------------------------------------------------------------------------------------------------ paid invoices
async def paid_report(
    db: AsyncSession,
    *,
    paid_from: date | None,
    paid_to: date | None,
    customer_id: uuid.UUID | None,
    sort: str,
    descending: bool,
    limit: int | None,
    offset: int,
) -> PaidReport:
    rows, total, received = await receivables_repo.paid_invoices(
        db, paid_from=paid_from, paid_to=paid_to, customer_id=customer_id, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return PaidReport(paid_from=paid_from, paid_to=paid_to, count=total, amount_received=received, items=[PaidRow(**r) for r in rows], total=total)


async def paid_pdf(db: AsyncSession, *, paid_from: date | None, paid_to: date | None, customer_id: uuid.UUID | None) -> bytes:
    report = await paid_report(db, paid_from=paid_from, paid_to=paid_to, customer_id=customer_id, sort="paid_on", descending=True, limit=None, offset=0)
    window = f"{_date(paid_from)} to {_date(paid_to)}" if paid_from and paid_to else f"from {_date(paid_from)}" if paid_from else f"up to {_date(paid_to)}" if paid_to else "all time"
    return table_pdf(
        title="Paid invoices",
        lines=[f"Invoices paid in full, by the day the last money was applied: {window}."],
        headers=["Invoice", "Customer", "Invoiced", "Due", "Paid on", "Days to pay", "Days late", "Received"],
        widths=[38, 75, 30, 28, 28, 22, 20, 36],
        right=[5, 6, 7],
        rows=[
            [r.invoice_number, r.customer_name, _date(r.invoice_date), _date(r.due_date), _date(r.paid_on), str(r.days_to_pay), str(r.paid_late_days) if r.paid_late_days else "on time", inr(r.amount_paid)]
            for r in report.items
        ],
        summary=[("Invoices", str(report.count)), ("Money received", inr(report.amount_received))],
        empty="No invoice was paid in this period.",
    )
