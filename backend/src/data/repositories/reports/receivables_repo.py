"""Read-only figures over what is still to collect and what was collected: the dashboard's numbers and the reports."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, Invoice, Payment, PaymentAllocation
from data.models.postgres.enums import AllocationStatus, InvoiceStatus, PaymentState

_TO_COLLECT = Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTLY_PAID])
_ZERO = Decimal("0")

# (key, label, first day past due, last day past due); None = no limit on that side.
BUCKETS: list[tuple[str, str, int | None, int | None]] = [
    ("current", "Not yet due", None, 0),
    ("1-30", "1 to 30 days late", 1, 30),
    ("31-60", "31 to 60 days late", 31, 60),
    ("61-90", "61 to 90 days late", 61, 90),
    ("90+", "Over 90 days late", 91, None),
]
BUCKET_KEYS = [key for key, *_ in BUCKETS]


def _in_bucket(late, lo: int | None, hi: int | None):  # noqa: ANN001, ANN202
    if lo is None:
        return late <= hi
    if hi is None:
        return late >= lo
    return and_(late >= lo, late <= hi)


def _days_late(today: date):  # noqa: ANN202
    """Days past the due date as the database counts them: negative before it."""
    return literal(today, Date) - Invoice.due_date


def _bucket(today: date):  # noqa: ANN202
    late = _days_late(today)
    return case(*[(_in_bucket(late, lo, hi), key) for key, _, lo, hi in BUCKETS], else_="current")


async def aging(db: AsyncSession, today: date) -> list[dict[str, Any]]:
    """Per bucket of days past due: how many invoices still to collect, what they owe, and how much of it is disputed."""
    bucket = _bucket(today)
    rows = await db.execute(
        select(
            bucket.label("bucket"),
            func.count(),
            func.coalesce(func.sum(Invoice.collectible_balance), 0),
            func.count().filter(Invoice.is_disputed.is_(True)),
            func.coalesce(func.sum(Invoice.collectible_balance).filter(Invoice.is_disputed.is_(True)), 0),
        )
        .where(_TO_COLLECT)
        .group_by(bucket)
    )
    found = {key: (count, Decimal(amount), disputed, Decimal(disputed_amount)) for key, count, amount, disputed, disputed_amount in rows}
    empty = (0, _ZERO, 0, _ZERO)
    return [
        {
            "key": key,
            "label": label,
            "count": found.get(key, empty)[0],
            "amount": found.get(key, empty)[1],
            "disputed_count": found.get(key, empty)[2],
            "disputed_amount": found.get(key, empty)[3],
        }
        for key, label, _, _ in BUCKETS
    ]


_AGING_SORTS = {
    "days_late": lambda today: _days_late(today),
    "balance": lambda today: Invoice.collectible_balance,
    "customer": lambda today: Customer.customer_name,
    "invoice_number": lambda today: Invoice.invoice_number,
    "due_date": lambda today: Invoice.due_date,
}


async def aging_rows(
    db: AsyncSession,
    today: date,
    *,
    bucket: str | None,
    customer_id: Any,
    sort: str,
    descending: bool,
    limit: int | None,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """(rows, total): the invoices still to collect with their days late and bucket, optionally one bucket or one customer."""
    late = _days_late(today)
    which = _bucket(today)
    base = select(Invoice, Customer.customer_name, late.label("days_late"), which.label("bucket")).join(Customer, Customer.id == Invoice.customer_id).where(_TO_COLLECT)
    if bucket is not None:
        base = base.where(which == bucket)
    if customer_id is not None:
        base = base.where(Invoice.customer_id == customer_id)
    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    column = _AGING_SORTS.get(sort, _AGING_SORTS["days_late"])(today)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    query = base.order_by(ordered, Invoice.invoice_number)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return [
        {
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer_id,
            "customer_name": name,
            "status": invoice.status.value,
            "due_date": invoice.due_date,
            "days_late": int(days),
            "bucket": key,
            "balance": invoice.collectible_balance,
            "is_disputed": invoice.is_disputed,
        }
        for invoice, name, days, key in result
    ], total or 0


async def status_summary(db: AsyncSession) -> list[dict[str, Any]]:
    """Every invoice by status: how many, what they were worth, what was paid on them and what is left."""
    rows = await db.execute(
        select(
            Invoice.status,
            func.count(),
            func.coalesce(func.sum(Invoice.amount_inr), 0),
            func.coalesce(func.sum(Invoice.amount_paid), 0),
            func.coalesce(func.sum(Invoice.collectible_balance), 0),
        ).group_by(Invoice.status)
    )
    found = {status: (count, Decimal(total), Decimal(paid), Decimal(balance)) for status, count, total, paid, balance in rows}
    empty = (0, _ZERO, _ZERO, _ZERO)
    return [
        {"status": s.value, "count": found.get(s, empty)[0], "invoiced_amount": found.get(s, empty)[1], "paid_amount": found.get(s, empty)[2], "balance": found.get(s, empty)[3]}
        for s in (InvoiceStatus.OPEN, InvoiceStatus.PARTLY_PAID, InvoiceStatus.PAID)
    ]


_PAID_SORTS = {
    "paid_on": lambda paid_on: paid_on,
    "invoice_number": lambda paid_on: Invoice.invoice_number,
    "customer": lambda paid_on: Customer.customer_name,
    "amount": lambda paid_on: Invoice.amount_inr,
    "days_to_pay": lambda paid_on: paid_on - Invoice.invoice_date,
}


async def paid_invoices(
    db: AsyncSession,
    *,
    paid_from: date | None,
    paid_to: date | None,
    customer_id: Any,
    sort: str,
    descending: bool,
    limit: int | None,
    offset: int,
) -> tuple[list[dict[str, Any]], int, Decimal]:
    """(rows, total, money received): paid invoices with the day the last money was applied, in a window of days (IST)."""
    last_applied = (
        select(
            PaymentAllocation.invoice_id.label("invoice_id"),
            cast(func.timezone("Asia/Kolkata", func.max(PaymentAllocation.created_at)), Date).label("paid_on"),
        )
        .where(PaymentAllocation.status == AllocationStatus.ACTIVE)
        .group_by(PaymentAllocation.invoice_id)
        .subquery()
    )
    paid_on = func.coalesce(last_applied.c.paid_on, cast(Invoice.updated_at, Date))
    filters = [Invoice.status == InvoiceStatus.PAID]
    if paid_from is not None:
        filters.append(paid_on >= paid_from)
    if paid_to is not None:
        filters.append(paid_on <= paid_to)
    if customer_id is not None:
        filters.append(Invoice.customer_id == customer_id)
    base = (
        select(Invoice, Customer.customer_name, paid_on.label("paid_on"))
        .join(Customer, Customer.id == Invoice.customer_id)
        .outerjoin(last_applied, last_applied.c.invoice_id == Invoice.id)
        .where(*filters)
    )
    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    received = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount_paid), 0))
        .select_from(Invoice)
        .outerjoin(last_applied, last_applied.c.invoice_id == Invoice.id)
        .where(*filters)
    )
    column = _PAID_SORTS.get(sort, _PAID_SORTS["paid_on"])(paid_on)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    query = base.order_by(ordered, Invoice.invoice_number)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return [
        {
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer_id,
            "customer_name": name,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "paid_on": day,
            "days_to_pay": (day - invoice.invoice_date).days,
            "paid_late_days": max((day - invoice.due_date).days, 0),
            "amount_inr": invoice.amount_inr,
            "tds_amount": invoice.tds_amount,
            "amount_paid": invoice.amount_paid,
        }
        for invoice, name, day in result
    ], total or 0, Decimal(received or 0)


async def totals(db: AsyncSession, today: date) -> dict[str, Any]:
    """Everything still to collect in one pass: all of it, the overdue part, what falls due within a week, disputed, on PO hold."""
    week = today + timedelta(days=7)
    balance = Invoice.collectible_balance
    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(balance), 0),
                func.count().filter(Invoice.due_date < today),
                func.coalesce(func.sum(balance).filter(Invoice.due_date < today), 0),
                func.count().filter(Invoice.due_date >= today, Invoice.due_date <= week),
                func.coalesce(func.sum(balance).filter(Invoice.due_date >= today, Invoice.due_date <= week), 0),
                func.count().filter(Invoice.is_disputed.is_(True)),
                func.coalesce(func.sum(balance).filter(Invoice.is_disputed.is_(True)), 0),
                func.count().filter(Invoice.po_hold.is_(True)),
            ).where(_TO_COLLECT)
        )
    ).one()
    return {
        "outstanding_count": row[0],
        "outstanding_amount": Decimal(row[1]),
        "overdue_count": row[2],
        "overdue_amount": Decimal(row[3]),
        "due_soon_count": row[4],
        "due_soon_amount": Decimal(row[5]),
        "disputed_count": row[6],
        "disputed_amount": Decimal(row[7]),
        "po_hold_count": row[8],
    }


async def top_overdue(db: AsyncSession, today: date, limit: int) -> list[dict[str, Any]]:
    """The customers owing the most past their due dates."""
    owed = func.sum(Invoice.collectible_balance)
    rows = await db.execute(
        select(Customer.id, Customer.customer_name, func.count(), owed, func.min(Invoice.due_date))
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(_TO_COLLECT, Invoice.due_date < today)
        .group_by(Customer.id, Customer.customer_name)
        .order_by(owed.desc(), Customer.customer_name)
        .limit(limit)
    )
    return [
        {
            "customer_id": cid,
            "customer_name": name,
            "overdue_count": count,
            "overdue_amount": Decimal(amount),
            "oldest_days_late": (today - oldest).days,
        }
        for cid, name, count, amount, oldest in rows
    ]


async def cash(db: AsyncSession, month_start: date) -> dict[str, Any]:
    """Money received since the month began, and money received that is not against an invoice yet."""
    paid_on = func.coalesce(Payment.payment_date, cast(Payment.created_at, Date))
    received = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Payment.amount_received_inr), 0)).where(
                Payment.state.notin_([PaymentState.REJECTED, PaymentState.FAILED]),
                Payment.amount_received_inr.is_not(None),
                paid_on >= month_start,
            )
        )
    ).one()
    unapplied = await db.scalar(
        select(func.coalesce(func.sum(Payment.unapplied_amount), 0)).where(
            Payment.state.in_([PaymentState.UNAPPLIED, PaymentState.PARTIALLY_APPLIED])
        )
    )
    return {
        "received_count": received[0],
        "received_amount": Decimal(received[1]),
        "unapplied_amount": Decimal(unapplied or 0),
    }
