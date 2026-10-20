"""Reverse a payment's application (C4, FTM / ADM: payments.correction.rerun).

In ONE transaction, under row locks (payment, then the invoices in id order, then the notes in id
order: the same order as the apply step, so nobody deadlocks):

- every Active allocation the payment funded goes to Reversed, and each invoice's amount_paid falls
  by it; the invoice's status is recomputed (a Paid invoice can reopen);
- credit spends this payment's own "use our credit" instruction caused are undone too, so a
  re-match cannot spend the same credit twice: the note gets the amount back (a Closed note
  reopens). Manual spends are never touched;
- a credit note born from this payment (an overpayment, or converted unapplied money) is Voided
  when it is unspent; the payment's credited amount returns to zero;
- the payment's buckets are cleared and it re-enters matching, or waits for a person.

A credit note that has already been spent BLOCKS the reversal: nothing is changed and the caller
gets a 409 that names the note. (A payment waiting for a person must hold no money, so it cannot
sit in review with spent credit behind it.)
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError
from core.services.invoices.amounts import money
from core.services.payments.credit import note_status_after
from core.services.payments.engine import Status, run_matching
from core.services.payments.status import invoice_status_after
from data.models.postgres import User
from data.models.postgres.enums import AllocationStatus, NoteStatus, PaymentState, TriggerType
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from schemas.payments.reversal import (
    AllocationResult,
    ReversalResult,
    ReversedAllocation,
    ReversePayment,
)

_ZERO = Decimal("0.00")
REVERSIBLE = (PaymentState.APPLIED, PaymentState.PARTIALLY_APPLIED, PaymentState.UNAPPLIED)


def _log(db: AsyncSession, entity: str, entity_id: uuid.UUID, before: str | None, after: str, user: User, reason: str) -> None:
    logs_repo.add_state_log(
        db, entity_type=entity, entity_id=entity_id, from_state=before, to_state=after,
        triggered_by_type=TriggerType.USER, triggered_by_id=user.id, reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def reverse_payment(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: ReversePayment
) -> ReversalResult:
    payment = await payments_repo.lock_payment(db, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found")
    if payment.state not in REVERSIBLE:
        raise ConflictError(
            f"This payment is {payment.state.value}: only an Applied, PartiallyApplied or "
            "Unapplied payment can be reversed"
        )

    allocations = await payments_repo.active_allocations_of(db, payment.id)
    born = await payments_repo.notes_for_payment(db, payment.id)
    invoices = {
        i.id: i
        for i in await invoices_repo.lock_invoices(db, sorted({a.invoice_id for a in allocations}))
    }
    note_ids = sorted({a.note_id for a in allocations if a.note_id} | {n.id for n in born})
    notes = {n.id: n for n in await payments_repo.lock_notes(db, note_ids)}

    # --- blocked when credit born from this payment has been used ---
    consumed = []
    for n in born:
        note = notes[n.id]
        if note.status is NoteStatus.VOIDED:
            continue
        used = money(note.amount - note.remaining_amount)
        if note.status is not NoteStatus.OPEN or used != 0:
            consumed.append(f"{note.note_number} ({used} of {note.amount} already used)")
    if consumed:
        await db.rollback()
        raise ConflictError(
            "The credit already consumed cannot be taken back: credit note "
            + ", ".join(consumed)
            + " was created from this payment and has been spent, so the payment cannot be "
            "reversed. Nothing was changed"
        )

    reference = payment.reference_number or "(no reference)"
    why = f"by {user.username}: {body.reason}"
    reversed_rows: list[ReversedAllocation] = []
    credit_restored: list[str] = []
    voided: list[str] = []
    paid_back: dict[uuid.UUID, Decimal] = {}

    # --- allocations (payment-funded, and the spends this payment's instruction caused) ---
    for a in allocations:
        a.status = AllocationStatus.REVERSED
        a.updated_by = user.id
        paid_back[a.invoice_id] = paid_back.get(a.invoice_id, _ZERO) + a.allocated_amount
        note_number = None
        if a.note_id:
            note = notes[a.note_id]
            was = note.status
            note.remaining_amount = note.remaining_amount + a.allocated_amount
            note.status = NoteStatus(note_status_after(note.remaining_amount, note.amount))
            note.updated_by = user.id
            note_number = note.note_number
            credit_restored.append(note.note_number)
            _log(db, "note", note.id, was.value, note.status.value, user,
                 f"{a.allocated_amount} given back: payment {reference} was reversed {why}")
        reversed_rows.append(
            ReversedAllocation(
                invoice_number=invoices[a.invoice_id].invoice_number,
                amount=a.allocated_amount,
                funded_by="credit_note" if a.note_id else "payment",
                note_number=note_number,
                invoice_status_after="",  # filled below, once the invoice is recomputed
            )
        )

    # --- invoices: less paid, status recomputed ---
    for invoice_id, back in paid_back.items():
        invoice = invoices[invoice_id]
        before = invoice.status
        new_paid = invoice.amount_paid - back
        if new_paid < 0:
            raise AssertionError(f"invoice {invoice.invoice_number}: reversing {back} would go below zero")
        invoice.amount_paid = new_paid
        invoice.status = invoice_status_after(new_paid, invoice.collectible_total)
        invoice.updated_by = user.id
        _log(db, "invoice", invoice.id, before.value, invoice.status.value, user,
             f"{back} taken back: payment {reference} was reversed {why}")
    for row in reversed_rows:
        row.invoice_status_after = next(
            i.status.value for i in invoices.values() if i.invoice_number == row.invoice_number
        )

    # --- credit notes born from this payment: Voided (they were unspent) ---
    for n in born:
        note = notes[n.id]
        if note.status is NoteStatus.VOIDED:
            continue
        was = note.status
        note.status = NoteStatus.VOIDED
        note.updated_by = user.id
        voided.append(note.note_number)
        _log(db, "note", note.id, was.value, NoteStatus.VOIDED.value, user,
             f"Voided: the payment it came from ({reference}) was reversed {why}")

    # --- the payment: clean slate ---
    before_state = payment.state
    payment.unapplied_amount = _ZERO
    payment.held_amount = _ZERO
    payment.credited_amount = _ZERO
    payment.confidence_score = None
    payment.rematch_count = 0
    payment.updated_by = user.id
    if body.after == "review":
        payment.state = PaymentState.NEEDS_REVIEW
        payment.review_reasons = [
            f"The payment's earlier match was reversed {why}. Decide where the money goes"
        ]
        payment.match_reason = "Waiting for a person: " + payment.review_reasons[0]
    else:
        payment.state = PaymentState.MATCHING
        payment.review_reasons = None
        payment.match_reason = f"Reversed {why}; matching again"
    _log(db, "payment", payment.id, before_state.value, payment.state.value, user, payment.match_reason)
    await db.flush()

    result = ReversalResult(
        payment_id=payment.id,
        state=payment.state.value,
        message=payment.match_reason,
        reversed=reversed_rows,
        voided_notes=voided,
        credit_restored=sorted(set(credit_restored)),
    )
    await unit_of_work.commit(db)

    if body.after == "rematch":
        outcome = await run_matching(db, payment_id, user.id)  # commits
        result.state = outcome.state.value if outcome.state else result.state
        if outcome.status is Status.APPLIED:
            result.new_allocations = [
                AllocationResult(
                    invoice_number=a["invoice_number"], allocated=Decimal(a["allocated"]),
                    tolerance=a["tolerance"], invoice_status=a["invoice_status"],
                )
                for a in outcome.allocations
            ]
            result.message += "; applied again"
        else:
            result.rematch_reasons = outcome.reasons
            result.message += (
                "; waiting for a person" if outcome.status is Status.REVIEW else "; parked"
            )
    return result
