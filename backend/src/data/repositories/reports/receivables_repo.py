"""Read-only figures over what is still to collect: the dashboard's numbers and, later, the aging report."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import InvoiceStatus, PaymentState

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


def _in_bucket(late, lo: int | None, hi: int | None):  # noqa: ANN001, ANN202
    if lo is None:
        return late <= hi
    if hi is None:
        return late >= lo
    return and_(late >= lo, late <= hi)


async def aging(db: AsyncSession, today: date) -> list[dict[str, Any]]:
    """Per bucket of days past due: how many invoices still to collect, what they owe, and how much of it is disputed."""
    late = literal(today, Date) - Invoice.due_date  # days past the due date, negative before it
    bucket = case(*[(_in_bucket(late, lo, hi), key) for key, _, lo, hi in BUCKETS], else_="current")
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
    return [
        {
            "key": key,
            "label": label,
            "count": found.get(key, (0, _ZERO, 0, _ZERO))[0],
            "amount": found.get(key, (0, _ZERO, 0, _ZERO))[1],
            "disputed_count": found.get(key, (0, _ZERO, 0, _ZERO))[2],
            "disputed_amount": found.get(key, (0, _ZERO, 0, _ZERO))[3],
        }
        for key, label, _, _ in BUCKETS
    ]


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
