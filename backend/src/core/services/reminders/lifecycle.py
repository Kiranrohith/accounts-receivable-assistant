"""What happens to reminders and collection tasks when the world changes under them. All of it flushes only: the caller
(the payment apply, the dispute creation, the scan, a person's action) commits it with its own change.

  cancel_pending   a payment arrived or a dispute opened: the drafts waiting for a person are no longer wanted
                   (AutoCancelled, so the tier can be drafted again once the cause is over: D3)
  close_tasks      the invoice was paid while its collection task was open: the task closes itself (Paid)
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Invoice, Reminder, Task
from data.models.postgres.enums import ReminderState, TaskClosedReason, TaskStatus, TaskType, TriggerType
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.reminders import reminders_repo


def _who(user_id: uuid.UUID | None, scheduler: bool) -> TriggerType:
    return TriggerType.USER if user_id else TriggerType.SCHEDULER if scheduler else TriggerType.SYSTEM


def timeline(
    db: AsyncSession, invoice: Invoice, text: str, *, user_id: uuid.UUID | None = None, scheduler: bool = False
) -> None:
    """A line on the invoice's history (its state does not change)."""
    logs_repo.add_state_log(
        db,
        entity_type="invoice",
        entity_id=invoice.id,
        from_state=invoice.status.value,
        to_state=invoice.status.value,
        triggered_by_type=_who(user_id, scheduler),
        triggered_by_id=user_id,
        reason=text,
        occurred_at=datetime.now(UTC),
    )


def log_reminder(
    db: AsyncSession,
    reminder: Reminder,
    before: ReminderState | None,
    reason: str,
    *,
    user_id: uuid.UUID | None = None,
    scheduler: bool = False,
) -> None:
    logs_repo.add_state_log(
        db,
        entity_type="reminder",
        entity_id=reminder.id,
        from_state=before.value if before else None,
        to_state=reminder.state.value,
        triggered_by_type=_who(user_id, scheduler),
        triggered_by_id=user_id,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def cancel_pending(
    db: AsyncSession, invoice_id: uuid.UUID, reason: str, *, user_id: uuid.UUID | None = None, scheduler: bool = False
) -> int:
    """Cancel every draft of this invoice that is waiting. Returns how many."""
    pending = await reminders_repo.pending_for_invoice(db, invoice_id)
    for reminder in pending:
        before = reminder.state
        reminder.state = ReminderState.AUTO_CANCELLED
        reminder.cancel_reason = reason
        log_reminder(db, reminder, before, f"Cancelled: {reason}", user_id=user_id, scheduler=scheduler)
    if pending:
        invoice = await invoices_repo.get_invoice(db, invoice_id)
        if invoice is not None:
            tiers = ", ".join(f"day {r.reminder_tier}" for r in pending)
            timeline(db, invoice, f"Reminder draft ({tiers}) cancelled: {reason}", user_id=user_id, scheduler=scheduler)
        await unit_of_work.flush(db)
    return len(pending)


async def close_task(
    db: AsyncSession,
    task: Task,
    reason: TaskClosedReason,
    *,
    user_id: uuid.UUID | None = None,
    note: str | None = None,
    scheduler: bool = False,
) -> None:
    task.status = TaskStatus.CLOSED
    task.closed_reason = reason
    task.closed_note = note
    task.closed_at = datetime.now(UTC)
    task.closed_by = user_id
    task.updated_by = user_id
    logs_repo.add_state_log(
        db,
        entity_type="task",
        entity_id=task.id,
        from_state=TaskStatus.OPEN.value,
        to_state=TaskStatus.CLOSED.value,
        triggered_by_type=_who(user_id, scheduler),
        triggered_by_id=user_id,
        reason=f"Closed: {reason.value}" + (f" ({note})" if note else ""),
        occurred_at=datetime.now(UTC),
    )
    await unit_of_work.flush(db)


async def close_tasks_for(db: AsyncSession, invoice: Invoice, *, scheduler: bool = False) -> int:
    """The invoice is paid: its open collection task closes itself."""
    task = await reminders_repo.open_task_for_invoice(db, invoice.id, TaskType.COLLECTION_60D)
    if task is None:
        return 0
    await close_task(db, task, TaskClosedReason.PAID, scheduler=scheduler)
    timeline(db, invoice, "Collection task closed: the invoice was paid", scheduler=scheduler)
    return 1


async def invoice_changed_by_money(db: AsyncSession, invoice_ids: list[uuid.UUID], *, user_id: uuid.UUID | None = None) -> None:
    """Money was applied to these invoices (in this transaction): drafts waiting for a person are cancelled, and a
    task whose invoice is now paid closes. Driven by the allocations written, not by the payment's own state."""
    for invoice_id in dict.fromkeys(invoice_ids):
        invoice = await invoices_repo.get_invoice(db, invoice_id)
        if invoice is None:
            continue
        await cancel_pending(db, invoice_id, "a payment was applied to the invoice", user_id=user_id)
        if invoice.status.value == "Paid":
            await close_tasks_for(db, invoice)
