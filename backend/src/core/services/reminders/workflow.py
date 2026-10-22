"""What a finance associate does with a reminder: read it, edit its wording, approve it (which sends it) or reject it.

Reading needs reminders.view_sent (anyone who may ask "was this client notified?"), acting needs reminders.manage.
Every action takes the reminder's row lock first, so of two people acting at once one wins and the other gets 409.

Approving is sending: the figures are filled in with the true balance and day count at that moment (D4), the recipient is
the customer's address and cannot be changed (D8), the debit note's PDF rides along for a debit note, and the mail goes
out through the one shared sender. If it cannot go, the reminder stays a draft (the reason is on the screen and in the
step log, the approver is told) and can be approved again once the cause, such as a bad address, is fixed (D7).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationFailure
from core.services.invoices import debit_note_document
from core.services.mail import sending, smtp
from core.services.reminders import drafts, lifecycle, scan
from data.models.postgres import Customer, Invoice, Reminder, User
from data.models.postgres.enums import InvoiceType, ReminderState
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.reminders import reminders_repo
from schemas.reminders.reminders import (
    EditReminder,
    HistoryEntry,
    ReminderDetail,
    ReminderItem,
    ReminderResult,
)

SEND_STEP = "send_reminder_email"
_PENDING = (ReminderState.DRAFTED, ReminderState.APPROVED)


def _item(reminder: Reminder, invoice: Invoice, customer: Customer, handler: str | None, today) -> ReminderItem:  # noqa: ANN001
    waiting = reminder.state in _PENDING
    return ReminderItem(
        reminder_id=reminder.id,
        reference=drafts.reference_for(reminder.id),
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        customer_id=customer.id,
        customer_name=customer.customer_name,
        tier=reminder.reminder_tier,
        state=reminder.state.value,
        scheduled_date=reminder.scheduled_date,
        weekend_shift_days=reminder.weekend_shift_days,
        days_overdue=(today - invoice.due_date).days if waiting else reminder.days_overdue_at_send,
        balance=drafts.balance_text(invoice) if waiting else None,
        recipient=sending.clean_address(customer.email),
        created_at=reminder.created_at,
        sent_at=reminder.sent_at,
        handled_by=handler,
        note=reminder.cancel_reason,
    )


async def list_reminders(
    db: AsyncSession,
    *,
    states: list[ReminderState] | None,
    invoice_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[ReminderItem]:
    today = scan.today_ist(datetime.now(UTC))
    rows = await reminders_repo.list_reminders(
        db, states=states, invoice_id=invoice_id, customer_id=customer_id, limit=limit, offset=offset
    )
    return [_item(r, i, c, name, today) for r, i, c, name in rows]


async def _loaded(db: AsyncSession, reminder_id: uuid.UUID, *, lock: bool) -> tuple[Reminder, Invoice, Customer]:
    reminder = await (reminders_repo.lock_reminder if lock else reminders_repo.get_reminder)(db, reminder_id)
    if reminder is None:
        raise NotFoundError("Reminder not found")
    invoice = await (invoices_repo.get_invoice_for_update if lock else invoices_repo.get_invoice)(db, reminder.invoice_id)
    customer = await customers_repo.get_by_id(db, invoice.customer_id)
    return reminder, invoice, customer


async def get_reminder(db: AsyncSession, reminder_id: uuid.UUID) -> ReminderDetail:
    reminder, invoice, customer = await _loaded(db, reminder_id, lock=False)
    today = scan.today_ist(datetime.now(UTC))
    handler = None
    if reminder.handled_by:
        from data.repositories.users import users_repo

        person = await users_repo.get_user_by_id(db, reminder.handled_by)
        handler = person.username if person else None
    subject, body = drafts.split(reminder.draft_body or "")
    values = drafts.figures(invoice, today)
    blocked = None
    if reminder.state in _PENDING:
        blocked = await scan.reason_to_hold_back(db, invoice)
        if blocked is None and sending.clean_address(customer.email) is None:
            blocked = f"{customer.customer_name} has no valid e-mail address on file"
    history = [
        HistoryEntry(at=e.occurred_at, from_state=e.from_state, to_state=e.to_state, reason=e.reason)
        for e in await logs_repo.list_state_log(db, "reminder", reminder.id)
    ]
    return ReminderDetail(
        **_item(reminder, invoice, customer, handler, today).model_dump(),
        subject=subject,
        body=body,
        preview_subject=drafts.with_reference(drafts.fill(subject, values), reminder.id) if subject else "",
        preview_body=drafts.fill(body, values),
        can_send=reminder.state in _PENDING and blocked is None,
        blocked_reason=blocked,
        sent_subject=reminder.sent_subject,
        sent_body=reminder.sent_body,
        history=history,
    )


def _apply_edit(reminder: Reminder, edit: EditReminder | None) -> bool:
    if edit is None or (edit.subject is None and edit.body is None):
        return False
    subject, body = drafts.split(reminder.draft_body or "")
    reminder.draft_body = drafts.join(edit.subject if edit.subject is not None else subject, edit.body if edit.body is not None else body)
    return True


async def edit_reminder(db: AsyncSession, user: User, reminder_id: uuid.UUID, edit: EditReminder) -> ReminderResult:
    if edit.subject is None and edit.body is None:
        raise ValidationFailure("Nothing to change: give a subject, a body, or both")
    reminder, _, _ = await _loaded(db, reminder_id, lock=True)
    if reminder.state is not ReminderState.DRAFTED:
        raise ConflictError(f"This reminder is {reminder.state.value}: only a draft can be edited")
    _apply_edit(reminder, edit)
    reminder.updated_by = user.id
    lifecycle.log_reminder(db, reminder, reminder.state, f"Wording edited by {user.username}", user_id=user.id)
    await unit_of_work.commit(db)
    return ReminderResult(reminder_id=reminder.id, state=reminder.state.value, message="Saved.")


async def reject(db: AsyncSession, user: User, reminder_id: uuid.UUID, reason: str) -> ReminderResult:
    reminder, invoice, _ = await _loaded(db, reminder_id, lock=True)
    if reminder.state is not ReminderState.DRAFTED:
        raise ConflictError(f"This reminder is {reminder.state.value}: only a draft can be rejected")
    before = reminder.state
    reminder.state = ReminderState.REJECTED
    reminder.cancel_reason = reason
    reminder.handled_by = user.id
    reminder.updated_by = user.id
    lifecycle.log_reminder(db, reminder, before, f"Rejected by {user.username}: {reason}", user_id=user.id)
    lifecycle.timeline(db, invoice, f"Reminder (day {reminder.reminder_tier}) rejected by {user.username}: {reason}", user_id=user.id)
    await unit_of_work.commit(db)
    return ReminderResult(reminder_id=reminder.id, state=reminder.state.value, message="Rejected. It can be drafted again by a later scan.")


async def approve(db: AsyncSession, user: User, reminder_id: uuid.UUID, edit: EditReminder | None) -> ReminderResult:
    """Approve (optionally with last edits) and send. Raises 409 when it is no longer needed, 422 without an address and
    502 when the mail server would not take it (the reminder stays a draft)."""
    reminder, invoice, customer = await _loaded(db, reminder_id, lock=True)
    if reminder.state is not ReminderState.DRAFTED:
        raise ConflictError(f"This reminder is {reminder.state.value}: only a draft can be approved")
    now = datetime.now(UTC)
    today = scan.today_ist(now)

    reason = await scan.reason_to_hold_back(db, invoice)
    if reason:  # the world moved on since the draft was written: it is cancelled, not sent
        reminder.state = ReminderState.AUTO_CANCELLED
        reminder.cancel_reason = reason
        lifecycle.log_reminder(db, reminder, ReminderState.DRAFTED, f"Cancelled at approval: {reason}", user_id=user.id)
        lifecycle.timeline(db, invoice, f"Reminder draft (day {reminder.reminder_tier}) cancelled at approval: {reason}", user_id=user.id)
        await unit_of_work.commit(db)
        raise ConflictError(f"This reminder is no longer needed ({reason}). It was cancelled")
    to = sending.clean_address(customer.email)
    if to is None:
        raise ValidationFailure(
            f"{customer.customer_name} has no valid e-mail address on file: correct the customer first, then approve again"
        )

    _apply_edit(reminder, edit)
    subject, text = drafts.split(reminder.draft_body or "")
    values = drafts.figures(invoice, today)
    subject = drafts.with_reference(drafts.fill(subject, values), reminder.id)
    text = drafts.fill(text, values)
    attachments: list[tuple[str, bytes, str]] = []
    if invoice.type is InvoiceType.DEBIT_NOTE:
        pdf = await debit_note_document.ensure_pdf(db, invoice, customer)
        attachments.append((debit_note_document.file_name(invoice), pdf, "application/pdf"))
    message = smtp.build_message(to=to, subject=subject, body=text, sender=get_settings().smtp_from, attachments=attachments)
    delivery = await sending.deliver(db, message, entity_type="reminder", entity_id=reminder.id, step_name=SEND_STEP)
    if not delivery.ok:
        await sending.tell_failure(
            db, [user.id],
            f"The reminder for invoice {invoice.invoice_number} could not be sent to {to}: {delivery.reason}. It stays a draft.",
            entity_type="reminder", entity_id=reminder.id, actor=user.id,
        )
        await unit_of_work.commit(db)
        raise ExternalServiceError(f"The reminder could not be sent: {delivery.reason}")

    reminder.state = ReminderState.SENT
    reminder.handled_by = user.id
    reminder.updated_by = user.id
    reminder.sent_at = now
    reminder.sent_date = today
    reminder.sent_to = to
    reminder.sent_subject = subject
    reminder.sent_body = text
    reminder.message_id = delivery.sent.message_id
    reminder.days_overdue_at_send = (today - invoice.due_date).days
    lifecycle.log_reminder(db, reminder, ReminderState.DRAFTED, f"Approved and sent by {user.username} to {to}", user_id=user.id)
    lifecycle.timeline(
        db, invoice,
        f"Reminder (day {reminder.reminder_tier}) sent to {to} by {user.username}: {reminder.days_overdue_at_send} days overdue, balance {values['balance']}",
        user_id=user.id,
    )
    await unit_of_work.commit(db)
    return ReminderResult(reminder_id=reminder.id, state=reminder.state.value, message=f"Sent to {to}.")


async def run_scan(as_of: datetime | None) -> dict[str, Any]:
    moment = as_of or datetime.now(UTC)
    found = await scan.scan(moment)
    return {"as_of": moment, **found.as_dict()}
