import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from data.models.postgres import Customer, Dispute, DisputeMessage, Invoice, Payment, Role, User
from data.models.postgres.enums import DisputeCategory, DisputeMessageKind, DisputeStatus
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


async def by_sent_message_ids(db: AsyncSession, message_ids: list[str]) -> Dispute | None:
    """The dispute one of whose OUTGOING mails carries one of these Message-IDs (the newest such dispute)."""
    if not message_ids:
        return None
    return await db.scalar(
        select(Dispute)
        .join(DisputeMessage, DisputeMessage.dispute_id == Dispute.id)
        .where(DisputeMessage.gmail_message_id.in_(message_ids), DisputeMessage.kind == DisputeMessageKind.SENT)
        .order_by(Dispute.received_at.desc(), Dispute.created_at.desc())
        .limit(1)
    )


async def by_reference(db: AsyncSession, prefix: str) -> Dispute | None:
    """The dispute whose reference ("DSP-" + the first 8 characters of its id) this is; None unless exactly one fits."""
    found = list(
        await db.scalars(
            select(Dispute).where(cast(Dispute.id, String).like(prefix.lower() + "%")).limit(2)
        )
    )
    return found[0] if len(found) == 1 else None


async def message_exists(db: AsyncSession, dispute_id: uuid.UUID, gmail_message_id: str) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(DisputeMessage)
            .where(DisputeMessage.dispute_id == dispute_id, DisputeMessage.gmail_message_id == gmail_message_id)
        )
    )


async def disputes_of_mail(db: AsyncSession, email_id: uuid.UUID) -> list[Dispute]:
    """The disputes this mail raised."""
    return list(await db.scalars(select(Dispute).where(Dispute.ingested_email_id == email_id).order_by(Dispute.created_at)))


async def messages_of_mail(db: AsyncSession, email_id: uuid.UUID) -> list[DisputeMessage]:
    """Every message of any dispute that came from this mail (its own first message, or a follow-up added to another)."""
    return list(
        await db.scalars(
            select(DisputeMessage).where(DisputeMessage.ingested_email_id == email_id).order_by(DisputeMessage.created_at)
        )
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
    "category": Dispute.category,
    "customer": Customer.customer_name,
    "invoice": Invoice.invoice_number,
}


def _filtered(
    query,  # noqa: ANN001
    *,
    today: date,
    statuses: list[DisputeStatus] | None,
    open_only: bool,
    category: DisputeCategory | None,
    customer_id: uuid.UUID | None,
    invoice_id: uuid.UUID | None,
    assigned_to: uuid.UUID | None,
    unassigned: bool,
    overdue: bool,
    received_from: datetime | None,
    received_to: datetime | None,
):  # noqa: ANN202
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
    return query


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
    query = _filtered(
        _named(select(Dispute)), today=today, statuses=statuses, open_only=open_only, category=category,
        customer_id=customer_id, invoice_id=invoice_id, assigned_to=assigned_to, unassigned=unassigned,
        overdue=overdue, received_from=received_from, received_to=received_to,
    )
    column = _SORTS.get(sort, Dispute.received_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullsfirst()
    rows = await db.execute(query.order_by(ordered, Dispute.id).limit(limit).offset(offset))
    return [(d, c, i, o) for d, c, i, o in rows]


async def search_disputes(
    db: AsyncSession,
    *,
    today: date,
    statuses: list[DisputeStatus] | None,
    open_only: bool,
    category: DisputeCategory | None,
    customer_id: uuid.UUID | None,
    assigned_to: uuid.UUID | None,
    unassigned: bool,
    overdue: bool,
    search: str | None,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Dispute, str | None, str | None, str | None]], int]:
    """(rows, total) for the disputes page: filtered, searched, sorted and cut to one page by the database."""
    query = _filtered(
        _named(select(Dispute)), today=today, statuses=statuses, open_only=open_only, category=category,
        customer_id=customer_id, invoice_id=None, assigned_to=assigned_to, unassigned=unassigned,
        overdue=overdue, received_from=None, received_to=None,
    )
    if search and search.strip():
        # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
        escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        query = query.where(
            or_(
                Dispute.summary.ilike(like, escape="\\"),
                Customer.customer_name.ilike(like, escape="\\"),
                Invoice.invoice_number.ilike(like, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    column = _SORTS.get(sort, Dispute.received_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    rows = await db.execute(query.order_by(ordered, Dispute.id).limit(limit).offset(offset))
    return [(d, c, i, o) for d, c, i, o in rows], total or 0


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


async def open_on_invoices(db: AsyncSession, invoice_ids: list[uuid.UUID]) -> list[Dispute]:
    """Every open dispute on these invoices (any category), oldest first."""
    if not invoice_ids:
        return []
    result = await db.scalars(
        select(Dispute)
        .where(Dispute.invoice_id.in_(invoice_ids), _OPEN)
        .order_by(Dispute.received_at, Dispute.created_at, Dispute.id)
        .execution_options(populate_existing=True)  # a hold is judged on the state as it is now
    )
    return list(result)


async def payments_held_on_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> list[Payment]:
    """Payments with money held for any dispute (open or resolved) of this invoice."""
    result = await db.scalars(
        select(Payment)
        .join(Dispute, Dispute.id == Payment.held_for_dispute_id)
        .where(Dispute.invoice_id == invoice_id, Payment.held_amount > 0)
        .order_by(Payment.created_at, Payment.id)
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



async def disputes_by_documents(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Dispute]]:
    """The disputes each uploaded document produced (a complaint can name several invoices)."""
    if not document_ids:
        return {}
    result = await db.scalars(
        select(Dispute)
        .where(Dispute.source_document_id.in_(document_ids))
        .order_by(Dispute.created_at, Dispute.id)
    )
    found: dict[uuid.UUID, list[Dispute]] = {}
    for dispute in result:
        found.setdefault(dispute.source_document_id, []).append(dispute)
    return found


async def has_outgoing(db: AsyncSession, dispute_id: uuid.UUID) -> bool:
    """A draft or a sent message already exists: the first clarification has been written."""
    found = await db.scalar(
        select(DisputeMessage.id)
        .where(
            DisputeMessage.dispute_id == dispute_id,
            DisputeMessage.kind.in_([DisputeMessageKind.DRAFT, DisputeMessageKind.SENT]),
        )
        .limit(1)
    )
    return found is not None


async def get_message(db: AsyncSession, message_id: uuid.UUID) -> DisputeMessage | None:
    return await db.get(DisputeMessage, message_id, populate_existing=True)


async def lock_message(db: AsyncSession, message_id: uuid.UUID) -> DisputeMessage | None:
    return await db.scalar(
        select(DisputeMessage)
        .where(DisputeMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def scan_candidates(db: AsyncSession) -> list[uuid.UUID]:
    """Open disputes someone is working (classified): the deadline scan looks at these."""
    result = await db.scalars(
        select(Dispute.id)
        .where(
            Dispute.status.in_(
                [
                    DisputeStatus.CATEGORISED,
                    DisputeStatus.ASSIGNED,
                    DisputeStatus.CLARIFICATION_SENT,
                    DisputeStatus.ESCALATED,
                ]
            )
        )
        .order_by(Dispute.received_at, Dispute.id)
    )
    return list(result)


async def active_users_of_role(db: AsyncSession, role_code: str) -> list[User]:
    result = await db.scalars(
        select(User)
        .join(Role, Role.id == User.role_id)
        .where(Role.code == role_code, Role.is_active, User.is_active)
        .order_by(User.created_at, User.id)
    )
    return list(result)
