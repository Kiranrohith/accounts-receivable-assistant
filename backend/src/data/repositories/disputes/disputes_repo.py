import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from data.models.postgres import Customer, Dispute, DisputeMessage, Invoice, Payment, Role, User
from data.models.postgres.enums import DisputeCategory, DisputeStatus
from data.repositories import unit_of_work

_OPEN = Dispute.status != DisputeStatus.RESOLVED
_IST = "Asia/Kolkata"


async def insert_dispute(db: AsyncSession, **fields: Any) -> Dispute:
    dispute = Dispute(**fields)
    db.add(dispute)
    await unit_of_work.flush(db)
    return dispute


async def insert_message(db: AsyncSession, **fields: Any) -> DisputeMessage:
    message = DisputeMessage(**fields)
    db.add(message)
    await unit_of_work.flush(db)
    return message


async def get_dispute(db: AsyncSession, dispute_id: uuid.UUID) -> Dispute | None:
    return await db.get(Dispute, dispute_id)


async def lock_dispute(db: AsyncSession, dispute_id: uuid.UUID) -> Dispute | None:
    return await db.scalar(
        select(Dispute)
        .where(Dispute.id == dispute_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def latest_in_thread(db: AsyncSession, thread_id: str) -> Dispute | None:
    """The newest dispute raised in this mail thread (open or resolved)."""
    return await db.scalar(
        select(Dispute)
        .where(Dispute.gmail_thread_id == thread_id)
        .order_by(Dispute.received_at.desc(), Dispute.created_at.desc())
        .limit(1)
    )


async def others_on_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, exclude_id: uuid.UUID
) -> list[tuple[Dispute, str | None]]:
    """Every other dispute on the invoice, any status, newest first, with the owner's name."""
    owner = aliased(User)
    rows = await db.execute(
        select(Dispute, owner.username)
        .outerjoin(owner, owner.id == Dispute.assigned_to)
        .where(Dispute.invoice_id == invoice_id, Dispute.id != exclude_id)
        .order_by(Dispute.received_at.desc(), Dispute.id)
    )
    return [(d, name) for d, name in rows]


async def open_count_on_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(Dispute).where(Dispute.invoice_id == invoice_id, _OPEN)
        )
    ) or 0


async def least_loaded_user(db: AsyncSession, role_code: str) -> User | None:
    """The active user of this role with the fewest open disputes (ties: the longest-serving)."""
    load = func.count(Dispute.id)
    return await db.scalar(
        select(User)
        .join(Role, Role.id == User.role_id)
        .outerjoin(Dispute, and_(Dispute.assigned_to == User.id, _OPEN))
        .where(Role.code == role_code, Role.is_active, User.is_active)
        .group_by(User.id)
        .order_by(load, User.created_at, User.id)
        .limit(1)
    )


def _named(query):  # noqa: ANN001, ANN202
    owner = aliased(User)
    return (
        query.add_columns(Customer.customer_name, Invoice.invoice_number, owner.username)
        .outerjoin(Customer, Customer.id == Dispute.customer_id)
        .outerjoin(Invoice, Invoice.id == Dispute.invoice_id)
        .outerjoin(owner, owner.id == Dispute.assigned_to)
    )


_SORTS = {
    "received_at": Dispute.received_at,
    "first_response_due": Dispute.first_response_due,
    "resolution_due": Dispute.resolution_due,
    "status": Dispute.status,
}


async def list_disputes(
    db: AsyncSession,
    *,
    today: date,
    statuses: list[DisputeStatus] | None = None,
    open_only: bool = False,
    category: DisputeCategory | None = None,
    customer_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    assigned_to: uuid.UUID | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    received_from: datetime | None = None,
    received_to: datetime | None = None,
    sort: str = "received_at",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Dispute, str | None, str | None, str | None]]:
    query = _named(select(Dispute))
    if statuses:
        query = query.where(Dispute.status.in_(statuses))
    if open_only:
        query = query.where(_OPEN)
    if category is not None:
        query = query.where(Dispute.category == category)
    if customer_id is not None:
        query = query.where(Dispute.customer_id == customer_id)
    if invoice_id is not None:
        query = query.where(Dispute.invoice_id == invoice_id)
    if assigned_to is not None:
        query = query.where(Dispute.assigned_to == assigned_to)
    if unassigned:
        query = query.where(Dispute.assigned_to.is_(None))
    if overdue:
        query = query.where(
            _OPEN,
            or_(
                and_(Dispute.first_response_at.is_(None), Dispute.first_response_due < today),
                Dispute.resolution_due < today,
            ),
        )
    if received_from is not None:
        query = query.where(Dispute.received_at >= received_from)
    if received_to is not None:
        query = query.where(Dispute.received_at < received_to)
    column = _SORTS.get(sort, Dispute.received_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullsfirst()
    rows = await db.execute(query.order_by(ordered, Dispute.id).limit(limit).offset(offset))
    return [(d, c, i, o) for d, c, i, o in rows]


async def classify_queue(
    db: AsyncSession, *, limit: int, offset: int
) -> list[tuple[Dispute, str | None, str | None, str | None]]:
    rows = await db.execute(
        _named(select(Dispute))
        .where(Dispute.status == DisputeStatus.RAISED)
        .order_by(Dispute.received_at, Dispute.id)
        .limit(limit)
        .offset(offset)
    )
    return [(d, c, i, o) for d, c, i, o in rows]


async def get_with_names(
    db: AsyncSession, dispute_id: uuid.UUID
) -> tuple[Dispute, str | None, str | None, str | None] | None:
    row = (await db.execute(_named(select(Dispute)).where(Dispute.id == dispute_id))).first()
    return None if row is None else (row[0], row[1], row[2], row[3])


async def messages_for(
    db: AsyncSession, dispute_id: uuid.UUID
) -> list[tuple[DisputeMessage, str | None]]:
    rows = await db.execute(
        select(DisputeMessage, User.username)
        .outerjoin(User, User.id == DisputeMessage.created_by)
        .where(DisputeMessage.dispute_id == dispute_id)
        .order_by(DisputeMessage.created_at, DisputeMessage.id)
    )
    return [(m, name) for m, name in rows]


async def later_disputes(db: AsyncSession, dispute_id: uuid.UUID) -> list[Dispute]:
    result = await db.scalars(
        select(Dispute).where(Dispute.linked_dispute_id == dispute_id).order_by(Dispute.received_at)
    )
    return list(result)


async def held_payments(db: AsyncSession, dispute_id: uuid.UUID) -> list[Payment]:
    result = await db.scalars(
        select(Payment).where(Payment.held_for_dispute_id == dispute_id).order_by(Payment.created_at)
    )
    return list(result)


async def open_assigned_to(db: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await db.scalar(select(func.count()).select_from(Dispute).where(Dispute.assigned_to == user_id, _OPEN))
    ) or 0


# ---- dashboard -------------------------------------------------------------------------------------
async def counts_by_status(db: AsyncSession) -> list[tuple[str, int]]:
    rows = await db.execute(select(Dispute.status, func.count()).group_by(Dispute.status))
    return [(s.value, n) for s, n in rows]


async def counts_by_category(db: AsyncSession) -> list[tuple[str | None, int, int]]:
    open_n = func.count().filter(_OPEN)
    rows = await db.execute(
        select(Dispute.category, open_n, func.count()).group_by(Dispute.category)
    )
    return [(c.value if c else None, o, t) for c, o, t in rows]


async def open_rows(db: AsyncSession) -> list[Dispute]:
    result = await db.scalars(select(Dispute).where(_OPEN))
    return list(result)


async def average_days_to_resolve(db: AsyncSession) -> float | None:
    value = await db.scalar(
        select(func.avg(func.extract("epoch", Dispute.resolved_at - Dispute.received_at) / 86400.0)).where(
            Dispute.status == DisputeStatus.RESOLVED, Dispute.resolved_at.is_not(None)
        )
    )
    return None if value is None else float(value)


async def daily_volume(
    db: AsyncSession, since: datetime
) -> tuple[dict[date, int], dict[date, int]]:
    raised_day = func.date(func.timezone(_IST, Dispute.received_at))
    resolved_day = func.date(func.timezone(_IST, Dispute.resolved_at))
    raised = await db.execute(
        select(raised_day, func.count()).where(Dispute.received_at >= since).group_by(raised_day)
    )
    resolved = await db.execute(
        select(resolved_day, func.count())
        .where(Dispute.resolved_at.is_not(None), Dispute.resolved_at >= since)
        .group_by(resolved_day)
    )
    return {d: n for d, n in raised}, {d: n for d, n in resolved}


async def user_names(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
    return {i: n for i, n in rows}

