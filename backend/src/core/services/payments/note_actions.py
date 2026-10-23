"""What a person can do with credit notes (FTM / ADM, notes.create).

  create   a credit note for a customer (a goodwill credit, a correction)
  spend    pay an invoice with a note: an exact internal transfer (a note-funded allocation, no
           tolerance), Open -> PartiallyUsed -> Closed
  convert  turn money still unapplied on a payment into a credit note (the payment's unapplied
           amount falls and its credited amount rises, in one transaction)

Locks are taken in the same order as the payment apply (payment, invoices, notes), so a person and
the matcher cannot deadlock. Every action is logged and committed here.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError, ValidationFailure
from core.services.invoices.amounts import money
from core.services.payments import holds
from core.services.payments.credit import note_status_after
from core.services.payments.notes import create_credit_note
from core.services.reminders import lifecycle as reminder_lifecycle
from data.models.postgres import User
from data.models.postgres.enums import InvoiceStatus, NoteStatus, PaymentState, TriggerType
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from schemas.payments.notes import (
    ConvertResult,
    ConvertUnapplied,
    NewNote,
    SpendNote,
    SpendResult,
)

_ZERO = Decimal("0.00")
_SPENDABLE = (NoteStatus.OPEN, NoteStatus.PARTIALLY_USED)


async def create_note(db: AsyncSession, user: User, body: NewNote) -> uuid.UUID:
    customer = await customers_repo.get_by_id(db, body.customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    if not customer.is_active:
        raise ValidationFailure(f"Customer '{customer.customer_name}' is deactivated")
    if body.linked_invoice_id is not None:
        invoice = await invoices_repo.get_invoice(db, body.linked_invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")
        if invoice.customer_id != customer.id:
            raise ValidationFailure(
                f"Invoice {invoice.invoice_number} does not belong to {customer.customer_name}"
            )
    note = await create_credit_note(
        db,
        customer_id=customer.id,
        amount=body.amount,
        reason=f"Created by {user.username}: {body.reason}",
        actor=user.id,
        linked_invoice_id=body.linked_invoice_id,
    )
    note_id = note.id
    await unit_of_work.commit(db)
    return note_id


async def spend_note(
    db: AsyncSession, user: User, note_id: uuid.UUID, body: SpendNote
) -> SpendResult:
    # locks: the invoice first, then the note (the apply step's order)
    locked = await invoices_repo.lock_invoices(db, [body.invoice_id])
    invoice = locked[0] if locked else None
    notes = await payments_repo.lock_notes(db, [note_id])
    note = notes[0] if notes else None
    if note is None:
        raise NotFoundError("Credit note not found")
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if note.status not in _SPENDABLE:
        raise ConflictError(f"Credit note {note.note_number} is {note.status.value}; it cannot be spent")
    if invoice.customer_id != note.customer_id:
        raise ValidationFailure(f"Invoice {invoice.invoice_number} belongs to a different customer than the note")
    if invoice.status is InvoiceStatus.PAID:
        raise ConflictError(f"Invoice {invoice.invoice_number} is already paid")
    if invoice.po_hold:
        raise ConflictError(f"Invoice {invoice.invoice_number} is on purchase-order hold; release the hold first")
    blocking = holds.holding_of((await holds.open_disputes_by_invoice(db, [invoice])).get(invoice.id))
    if blocking:
        raise ConflictError(
            f"Invoice {invoice.invoice_number} has a dispute that holds payments ({holds.names(blocking)}); "
            "classify or resolve it first"
        )

    balance = money(invoice.collectible_balance)
    remaining = money(note.remaining_amount)
    amount = money(body.amount) if body.amount is not None else min(remaining, balance)
    if amount > remaining:
        raise ValidationFailure(f"The note has only {remaining} left (asked for {amount})")
    if amount > balance:
        raise ValidationFailure(f"The invoice owes only {balance} (asked for {amount})")
    if amount <= 0:
        raise ValidationFailure("Nothing to spend")

    now = datetime.now(UTC)
    await payments_repo.insert_allocations(
        db,
        [
            {
                "payment_id": None,
                "note_id": note.id,
                "invoice_id": invoice.id,
                "allocated_amount": amount,
                "matched_layer": None,
                "tolerance_applied": None,
                "fx_rate_used": None,
                "tolerance_residual": None,
                "tds_deducted": None,
                "allocated_by": user.id,
                "created_by": user.id,
                "updated_by": user.id,
            }
        ],
    )
    before_note, before_invoice = note.status, invoice.status
    note.remaining_amount = remaining - amount
    note.status = NoteStatus(note_status_after(note.remaining_amount, note.amount))
    note.updated_by = user.id
    invoice.amount_paid = invoice.amount_paid + amount
    invoice.status = (
        InvoiceStatus.PAID if invoice.amount_paid >= invoice.collectible_total else InvoiceStatus.PARTLY_PAID
    )
    invoice.updated_by = user.id
    for entity, entity_id, was, now_state, text in (
        ("note", note.id, before_note.value, note.status.value,
         f"{amount} spent on invoice {invoice.invoice_number} by {user.username}: {body.reason}"),
        ("invoice", invoice.id, before_invoice.value, invoice.status.value,
         f"{amount} paid from credit note {note.note_number} by {user.username}: {body.reason}"),
    ):
        logs_repo.add_state_log(
            db, entity_type=entity, entity_id=entity_id, from_state=was, to_state=now_state,
            triggered_by_type=TriggerType.USER, triggered_by_id=user.id, reason=text, occurred_at=now,
        )
    await db.flush()
    await reminder_lifecycle.invoice_changed_by_money(db, [invoice.id], user_id=user.id)
    result = SpendResult(
        note_number=note.note_number,
        note_status=note.status.value,
        note_remaining=note.remaining_amount,
        invoice_number=invoice.invoice_number,
        invoice_status=invoice.status.value,
        invoice_balance=money(invoice.collectible_total - invoice.amount_paid),
        spent=amount,
    )
    await unit_of_work.commit(db)
    return result


async def convert_unapplied(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: ConvertUnapplied
) -> ConvertResult:
    payment = await payments_repo.lock_payment(db, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found")
    if payment.state not in (PaymentState.UNAPPLIED, PaymentState.PARTIALLY_APPLIED) or payment.unapplied_amount <= 0:
        raise ConflictError(
            f"This payment is {payment.state.value} with {payment.unapplied_amount} unapplied: "
            "there is nothing to convert"
        )
    if payment.customer_id is None:
        raise ValidationFailure("The payment has no customer, so a credit note cannot be made")
    available = money(payment.unapplied_amount)
    amount = money(body.amount) if body.amount is not None else available
    if amount > available:
        raise ValidationFailure(f"Only {available} is unapplied (asked for {amount})")

    reference = payment.reference_number or "(no reference)"
    note = await create_credit_note(
        db,
        customer_id=payment.customer_id,
        amount=amount,
        reason=f"Unapplied money on payment {reference} converted by {user.username}: {body.reason}",
        actor=user.id,
        linked_payment_id=payment.id,
    )
    before = payment.state
    payment.unapplied_amount = available - amount  # money exists exactly once
    payment.credited_amount = payment.credited_amount + amount
    if payment.unapplied_amount == 0 and payment.held_amount == 0:
        payment.state = PaymentState.APPLIED  # everything is now disposed of: allocations and/or credit
    payment.updated_by = user.id
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before.value,
        to_state=payment.state.value,
        triggered_by_type=TriggerType.USER,
        triggered_by_id=user.id,
        reason=f"{amount} converted to credit note {note.note_number}: {body.reason}",
        occurred_at=datetime.now(UTC),
    )
    await db.flush()
    result = ConvertResult(
        payment_id=payment.id,
        payment_state=payment.state.value,
        unapplied_amount=payment.unapplied_amount,
        credited_amount=payment.credited_amount,
        note_number=note.note_number,
        note_amount=note.amount,
    )
    await unit_of_work.commit(db)
    return result
