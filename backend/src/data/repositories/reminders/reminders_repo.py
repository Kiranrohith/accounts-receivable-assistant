"""Reminders and collection tasks: queries only (the services own the rules, callers commit)."""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from data.models.postgres import Customer, Dispute, Invoice, Payment, Reminder, Role, Task, User
from data.models.postgres.enums import (
    DisputeStatus,
    InvoiceStatus,
    ReminderState,
    TaskStatus,
    TaskType,
)
from data.repositories import unit_of_work

_PENDING = (ReminderState.DRAFTED, ReminderState.APPROVED)


# ------------------------------------------------------------------ reminders
async def insert_reminder(db: AsyncSession, **fields: Any) -> Reminder:
    row = Reminder(**fields)
    db.add(row)
    await unit_of_work.flush(db)
    return row


async def get_reminder(db: AsyncSession, reminder_id: uuid.UUID) -> Reminder | None:
    return await db.get(Reminder, reminder_id)


async def lock_reminder(db: AsyncSession, reminder_id: uuid.UUID) -> Reminder | None:
    return await db.scalar(
        select(Reminder).where(Reminder.id == reminder_id).with_for_update().execution_options(populate_existing=True)
    )


async def rows_for_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> list[Reminder]:
    result = await db.scalars(
        select(Reminder).where(Reminder.invoice_id == invoice_id).order_by(Reminder.created_at, Reminder.id)
    )
    return list(result)


async def pending_for_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> list[Reminder]:
    """Drafts waiting for a person or approved and not yet sent."""
    result = await db.scalars(
        select(Reminder).where(Reminder.invoice_id == invoice_id, Reminder.state.in_(_PENDING)).order_by(Reminder.created_at)
    )
    return list(result)


async def pending_with_invoices(db: AsyncSession) -> list[tuple[Reminder, Invoice, Customer]]:
    rows = await db.execute(
        select(Reminder, Invoice, Customer)
        .join(Invoice, Invoice.id == Reminder.invoice_id)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(Reminder.state.in_(_PENDING))
        .order_by(Reminder.created_at)
    )
    return [(r, i, c) for r, i, c in rows]


async def customers_holding_cash(db: AsyncSession, customer_ids: list[uuid.UUID] | None = None) -> set[uuid.UUID]:
    """The customers with money of theirs we hold unapplied (the suppression read: payments.unapplied_amount > 0)."""
    query = select(Payment.customer_id).where(Payment.unapplied_amount > 0, Payment.customer_id.is_not(None))
    if customer_ids is not None:
        query = query.where(Payment.customer_id.in_(customer_ids))
    return set(await db.scalars(query.distinct()))


async def chaseable_invoice_ids(db: AsyncSession, today: date) -> list[uuid.UUID]:
    """Open and partly paid invoices at least a day past due, most overdue first."""
    result = await db.scalars(
        select(Invoice.id)
        .where(Invoice.status.in_((InvoiceStatus.OPEN, InvoiceStatus.PARTLY_PAID)), Invoice.due_date < today)
        .order_by(Invoice.due_date, Invoice.invoice_number)
    )
    return list(result)


async def by_message_ids(db: AsyncSession, message_ids: list[str]) -> Reminder | None:
    """The reminder one of whose SENT mails carries one of these Message-IDs."""
    if not message_ids:
        return None
    return await db.scalar(
        select(Reminder).where(Reminder.message_id.in_(message_ids)).order_by(Reminder.sent_at.desc()).limit(1)
    )


async def by_thread(db: AsyncSession, thread_id: str) -> Reminder | None:
    """The newest reminder whose Gmail thread was learned from a reply."""
    return await db.scalar(
        select(Reminder).where(Reminder.gmail_thread_id == thread_id).order_by(Reminder.created_at.desc()).limit(1)
    )


async def by_reference(db: AsyncSession, prefix: str) -> Reminder | None:
    """The reminder whose reference ("REM-" + the first 8 characters of its id) this is; None unless exactly one fits."""
    found = list(
        await db.scalars(select(Reminder).where(cast(Reminder.id, String).like(prefix.lower() + "%")).limit(2))
    )
    return found[0] if len(found) == 1 else None


async def list_reminders(
    db: AsyncSession,
    *,
    states: list[ReminderState] | None,
    invoice_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[tuple[Reminder, Invoice, Customer, str | None]]:
    handler = aliased(User)
    query = (
        select(Reminder, Invoice, Customer, handler.username)
        .join(Invoice, Invoice.id == Reminder.invoice_id)
        .join(Customer, Customer.id == Invoice.customer_id)
        .outerjoin(handler, handler.id == Reminder.handled_by)
    )
    if states:
        query = query.where(Reminder.state.in_(states))
    if invoice_id is not None:
        query = query.where(Reminder.invoice_id == invoice_id)
    if customer_id is not None:
        query = query.where(Invoice.customer_id == customer_id)
    rows = await db.execute(query.order_by(Reminder.created_at.desc(), Reminder.id).limit(limit).offset(offset))
    return [(r, i, c, name) for r, i, c, name in rows]


# ------------------------------------------------------------------ tasks
async def insert_task(db: AsyncSession, **fields: Any) -> Task:
    row = Task(**fields)
    db.add(row)
    await unit_of_work.flush(db)
    return row


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await db.get(Task, task_id)


async def lock_task(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await db.scalar(
        select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
    )


async def open_task_for_invoice(db: AsyncSession, invoice_id: uuid.UUID, type_: TaskType) -> Task | None:
    return await db.scalar(
        select(Task).where(Task.invoice_id == invoice_id, Task.type == type_, Task.status == TaskStatus.OPEN)
    )


async def open_tasks_of_paid_invoices(db: AsyncSession) -> list[Task]:
    result = await db.scalars(
        select(Task)
        .join(Invoice, Invoice.id == Task.invoice_id)
        .where(Task.status == TaskStatus.OPEN, Invoice.status == InvoiceStatus.PAID)
    )
    return list(result)


async def list_tasks(
    db: AsyncSession,
    *,
    status: TaskStatus | None,
    assigned_to: uuid.UUID | None,
    type_: TaskType | None,
    limit: int,
    offset: int,
) -> list[tuple[Task, str | None, str | None, str | None]]:
    owner = aliased(User)
    query = (
        select(Task, Invoice.invoice_number, Customer.customer_name, owner.username)
        .outerjoin(Invoice, Invoice.id == Task.invoice_id)
        .outerjoin(Customer, Customer.id == Task.customer_id)
        .outerjoin(owner, owner.id == Task.assigned_to)
    )
    if status is not None:
        query = query.where(Task.status == status)
    if assigned_to is not None:
        query = query.where(Task.assigned_to == assigned_to)
    if type_ is not None:
        query = query.where(Task.type == type_)
    rows = await db.execute(query.order_by(Task.created_at.desc(), Task.id).limit(limit).offset(offset))
    return [(t, number, customer, name) for t, number, customer, name in rows]


async def task_with_names(db: AsyncSession, task_id: uuid.UUID) -> tuple[Task, str | None, str | None, str | None] | None:
    owner = aliased(User)
    row = (
        await db.execute(
            select(Task, Invoice.invoice_number, Customer.customer_name, owner.username)
            .outerjoin(Invoice, Invoice.id == Task.invoice_id)
            .outerjoin(Customer, Customer.id == Task.customer_id)
            .outerjoin(owner, owner.id == Task.assigned_to)
            .where(Task.id == task_id)
        )
    ).first()
    return (row[0], row[1], row[2], row[3]) if row else None


async def least_loaded_collector(db: AsyncSession, role_code: str) -> User | None:
    """The active user of this role with the least work of the combined kind (escalated disputes + open collection
    tasks). Ties are broken at random, so no one person always gets the next one."""
    disputes = (
        select(func.count(Dispute.id))
        .where(Dispute.assigned_to == User.id, Dispute.status == DisputeStatus.ESCALATED)
        .scalar_subquery()
    )
    tasks = (
        select(func.count(Task.id))
        .where(Task.assigned_to == User.id, Task.status == TaskStatus.OPEN, Task.type == TaskType.COLLECTION_60D)
        .scalar_subquery()
    )
    return await db.scalar(
        select(User)
        .join(Role, Role.id == User.role_id)
        .where(Role.code == role_code, Role.is_active, User.is_active)
        .order_by(disputes + tasks, func.random())
        .limit(1)
    )

