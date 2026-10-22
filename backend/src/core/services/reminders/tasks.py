"""Collection tasks: the 60-day handoff as a person sees it. Anyone who may work them (tasks.collection.work: the finance
team and admins) can list them, read the bundle, and close one by hand. A task also closes by itself when its invoice
is paid (lifecycle.close_tasks_for / the scan)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError
from core.services.reminders import lifecycle
from data.models.postgres import Task, User
from data.models.postgres.enums import TaskClosedReason, TaskStatus, TaskType
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.reminders import reminders_repo
from schemas.reminders.reminders import CloseTask, HistoryEntry, TaskDetail, TaskItem


def _item(task: Task, number: str | None, customer: str | None, owner: str | None) -> TaskItem:
    return TaskItem(
        task_id=task.id,
        type=task.type.value,
        status=task.status.value,
        invoice_id=task.invoice_id,
        invoice_number=number,
        customer_id=task.customer_id,
        customer_name=customer,
        assigned_to=task.assigned_to,
        assigned_to_name=owner,
        created_at=task.created_at,
        closed_reason=task.closed_reason.value if task.closed_reason else None,
        closed_at=task.closed_at,
    )


async def list_tasks(
    db: AsyncSession, *, status: TaskStatus | None, assigned_to: uuid.UUID | None, limit: int, offset: int
) -> list[TaskItem]:
    rows = await reminders_repo.list_tasks(
        db, status=status, assigned_to=assigned_to, type_=TaskType.COLLECTION_60D, limit=limit, offset=offset
    )
    return [_item(t, number, customer, owner) for t, number, customer, owner in rows]


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> TaskDetail:
    found = await reminders_repo.task_with_names(db, task_id)
    if found is None:
        raise NotFoundError("Task not found")
    task, number, customer, owner = found
    history = [
        HistoryEntry(at=e.occurred_at, from_state=e.from_state, to_state=e.to_state, reason=e.reason)
        for e in await logs_repo.list_state_log(db, "task", task.id)
    ]
    return TaskDetail(**_item(task, number, customer, owner).model_dump(), payload=task.payload, closed_note=task.closed_note, history=history)


async def close(db: AsyncSession, user: User, task_id: uuid.UUID, body: CloseTask) -> TaskDetail:
    task = await reminders_repo.lock_task(db, task_id)
    if task is None:
        raise NotFoundError("Task not found")
    if task.status is not TaskStatus.OPEN:
        raise ConflictError(f"This task is already closed ({task.closed_reason.value if task.closed_reason else 'closed'})")
    await lifecycle.close_task(db, task, TaskClosedReason(body.reason), user_id=user.id, note=body.note)
    invoice = await invoices_repo.get_invoice(db, task.invoice_id) if task.invoice_id else None
    if invoice is not None:
        lifecycle.timeline(db, invoice, f"Collection task closed by {user.username}: {body.reason} ({body.note})", user_id=user.id)
    await unit_of_work.commit(db)
    return await get_task(db, task_id)
