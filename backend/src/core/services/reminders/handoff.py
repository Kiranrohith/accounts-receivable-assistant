"""The 60-day handoff: reminders stop and a person takes the invoice over.

An invoice still unpaid 60 days past its due date, and not in dispute, becomes ONE open collection task (COLLECTION_60D;
a second scan changes nothing: one open task per invoice), assigned to the finance team member with the least work of
the combined kind (escalated disputes + open collection tasks), at random among equals, an admin when no member is
active. The task carries the bundle the person needs: the invoice and what is left, the payments and reminders so far
and who to contact. Drafts still waiting are cancelled and the invoice's history records the handoff.
"""

from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import COLLECTION_HANDOFF_DAYS, COLLECTION_ROLE
from core.services import notifications
from core.services.invoices.amounts import format_money
from core.services.mail.sending import ADMIN_ROLE
from core.services.reminders import drafts, lifecycle
from data.models.postgres import Customer, Invoice, Task
from data.models.postgres.enums import NotificationType, TaskType, TriggerType
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from data.repositories.reminders import reminders_repo


async def bundle(db: AsyncSession, invoice: Invoice, customer: Customer, today: date) -> dict:
    allocations = await payments_repo.allocations_for_invoice(db, invoice.id)
    reminders = await reminders_repo.rows_for_invoice(db, invoice.id)
    return {
        "invoice": {
            "invoice_number": invoice.invoice_number,
            "type": invoice.type.value,
            "invoice_date": invoice.invoice_date.isoformat(),
            "due_date": invoice.due_date.isoformat(),
            "days_overdue": (today - invoice.due_date).days,
            "currency": invoice.invoice_currency,
            "collectible_total": str(invoice.collectible_total),
            "amount_paid": str(invoice.amount_paid),
            "balance": str(invoice.collectible_balance),
            "balance_text": drafts.balance_text(invoice),
            "status": invoice.status.value,
        },
        "customer": {
            "customer_id": str(customer.id),
            "name": customer.customer_name,
            "contact": customer.contact,
            "email": customer.email,
            "tax_id": customer.tax_id,
            "tax_id_type": customer.tax_id_type.value if customer.tax_id_type else None,
        },
        "payments": [
            {
                "payment_id": str(a.payment_id) if a.payment_id else None,
                "payment_reference": ref,
                "credit_note": number,
                "amount": str(a.allocated_amount),
                "status": a.status.value,
                "at": a.created_at.isoformat(),
            }
            for a, ref, number in allocations
        ],
        "reminders": [
            {
                "reminder_id": str(r.id),
                "tier": r.reminder_tier,
                "state": r.state.value,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "sent_to": r.sent_to,
            }
            for r in reminders
        ],
        "handed_over_on": today.isoformat(),
    }


async def hand_off(db: AsyncSession, invoice: Invoice, customer: Customer, today: date) -> Task | None:
    """Create the collection task for this invoice. None when one is already open. Flushes only."""
    if await reminders_repo.open_task_for_invoice(db, invoice.id, TaskType.COLLECTION_60D) is not None:
        return None
    owner = await reminders_repo.least_loaded_collector(db, COLLECTION_ROLE) or await reminders_repo.least_loaded_collector(
        db, ADMIN_ROLE
    )
    payload = await bundle(db, invoice, customer, today)
    try:
        async with db.begin_nested():  # a concurrent scan that got there first loses to the unique index, quietly
            task = await reminders_repo.insert_task(
                db,
                type=TaskType.COLLECTION_60D,
                customer_id=customer.id,
                invoice_id=invoice.id,
                assigned_to=owner.id if owner else None,
                payload=payload,
            )
    except IntegrityError:
        return None
    await lifecycle.cancel_pending(db, invoice.id, f"the invoice reached {COLLECTION_HANDOFF_DAYS} days overdue", scheduler=True)
    lifecycle.timeline(
        db, invoice,
        f"Handed to collections: {(today - invoice.due_date).days} days overdue, balance "
        f"{format_money(invoice.collectible_balance)}; assigned to {owner.username if owner else 'nobody (no active user)'}",
        scheduler=True,
    )
    logs_repo.add_state_log(
        db, entity_type="task", entity_id=task.id, from_state=None, to_state="Open", triggered_by_type=TriggerType.SCHEDULER,
        reason=f"Collection task for {invoice.invoice_number}", occurred_at=datetime.now(UTC),
    )
    if owner is not None:
        await notifications.create(
            db, owner.id, NotificationType.ASSIGNMENT,
            f"Collection task: invoice {invoice.invoice_number} ({customer.customer_name}) is "
            f"{(today - invoice.due_date).days} days overdue, balance {format_money(invoice.collectible_balance)}",
            entity_type="task", entity_id=task.id,
        )
    return task
