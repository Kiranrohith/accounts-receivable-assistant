"""Write a decided match to the books, in ONE transaction (Flow 2, D7 "APPLY").

Under row locks (the payment, then its invoices in id order, then any credit notes to spend):
re-check that nothing moved since the plan was made, write the allocations, move each invoice's
amount_paid and status, create the credit note for an overpayment, spend the credit notes the
advice told us to use (a note-funded allocation each), update the payment's buckets and state, and
log every change.
The money identity is checked before returning, so a payment that does not add up is never written.

Callers commit (or roll back on Contention and re-plan).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.amounts import money
from core.services.payments.credit import Spend, note_status_after
from core.services.payments.notes import create_credit_note
from core.services.payments.tolerance import Plan
from data.models.postgres import Invoice, Note, Payment
from data.models.postgres.enums import (
    InvoiceStatus,
    MatchedLayer,
    NoteStatus,
    PaymentState,
    Tolerance,
    TriggerType,
)
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo

_ZERO = Decimal("0.00")


class Contention(Exception):
    """An invoice changed (or was paid by someone else) between planning and applying."""


class NotMatching(Exception):
    """The payment is no longer waiting to be matched: someone else decided it."""


@dataclass
class Application:
    plan: Plan
    layer: MatchedLayer
    score: int | None  # clamped, as stored; None for a person's own decision
    reason: str
    received_inr: Decimal
    expected_balance: dict[uuid.UUID, Decimal]  # what each invoice owed when the plan was made
    fx_rate: dict[uuid.UUID, Decimal | None] = field(default_factory=dict)
    spends: list[Spend] = field(default_factory=list)  # credit notes the advice said to use
    expected_note_remaining: dict[uuid.UUID, Decimal] = field(default_factory=dict)


@dataclass
class Applied:
    payment: Payment
    invoices: list[Invoice]
    note: Note | None  # the credit note born from an overpayment, if any
    state: PaymentState
    spent: list[tuple[str, Decimal, str]] = field(default_factory=list)  # (note number, amount, invoice)


def _tolerance_note(plan: Plan, note: Note | None) -> str:
    if plan.overall is Tolerance.T4:
        return "short payment: the invoice stays partly paid"
    if plan.overall is Tolerance.T3:
        if note is not None:
            return f"overpaid by {plan.credit}: credit note {note.note_number}"
        return f"{plan.unapplied} could not be placed and stays unapplied"
    if plan.overall is Tolerance.T2:
        return "within the FX band: the difference is recorded as a residual"
    return "within the rounding tolerance"


async def apply(
    db: AsyncSession,
    payment_id: uuid.UUID,
    app: Application,
    actor: uuid.UUID | None,
    *,
    expected_states: tuple[PaymentState, ...] = (PaymentState.MATCHING,),
) -> Applied:
    """`expected_states`: the payment must be in one of these (Matching for the matcher; a
    reviewer's decision applies a NeedsReview or Unapplied payment)."""
    payment = await payments_repo.lock_payment(db, payment_id)
    if payment is None or payment.state not in expected_states:
        raise NotMatching()
    before_state = payment.state

    plan = app.plan
    invoices = await invoices_repo.lock_invoices(db, [s.invoice_id for s in plan.slices])
    by_id = {i.id: i for i in invoices}
    for s in plan.slices:
        invoice = by_id.get(s.invoice_id)
        if (
            invoice is None
            or invoice.status is InvoiceStatus.PAID
            or invoice.is_disputed
            or invoice.po_hold
            or money(invoice.collectible_balance) != app.expected_balance[s.invoice_id]
        ):
            raise Contention(f"invoice {s.invoice_id} changed while the payment was being matched")

    spend_notes: dict[uuid.UUID, Note] = {}
    if app.spends:
        locked = await payments_repo.lock_notes(db, sorted({sp.note_id for sp in app.spends}))
        spend_notes = {n.id: n for n in locked}
        for note_id, expected in app.expected_note_remaining.items():
            n = spend_notes.get(note_id)
            if (
                n is None
                or n.status not in (NoteStatus.OPEN, NoteStatus.PARTIALLY_USED)
                or money(n.remaining_amount) != expected
            ):
                raise Contention(f"credit note {note_id} changed while the payment was being matched")
        for sp in app.spends:
            if sp.invoice_id not in by_id:
                raise Contention("a credit spend names an invoice that is not part of the match")

    now = datetime.now(UTC)
    reference = payment.reference_number or "(no reference)"

    # --- credit note first: its number goes in the allocation / state reasons ---
    note: Note | None = None
    unapplied = money(plan.unapplied)
    credited = _ZERO
    if plan.credit > 0:
        linked = plan.credit_invoice_id or (plan.slices[-1].invoice_id if plan.slices else None)
        note = await create_credit_note(
            db,
            customer_id=payment.customer_id,
            amount=plan.credit,
            reason=f"Overpayment on payment {reference}: {plan.credit} more than the invoice(s) owed",
            actor=actor,
            linked_invoice_id=linked,
            linked_payment_id=payment.id,
        )
        credited = money(plan.credit)

    # --- allocations and invoices ---
    allocations = []
    for s in plan.slices:
        invoice = by_id[s.invoice_id]
        clears = invoice.amount_paid + s.allocated >= invoice.collectible_total
        allocations.append(
            {
                "payment_id": payment.id,
                "invoice_id": invoice.id,
                "allocated_amount": s.allocated,
                "matched_layer": app.layer,
                "tolerance_applied": s.tolerance,
                "fx_rate_used": app.fx_rate.get(invoice.id),
                "tolerance_residual": s.residual,
                "tds_deducted": invoice.tds_amount if clears and invoice.tds_amount else None,
                "allocated_by": None,
                "created_by": actor,
                "updated_by": actor,
            }
        )
    for sp in app.spends:  # a credit spend is an exact internal transfer: no tolerance, no residual
        allocations.append(
            {
                "payment_id": None,
                "note_id": sp.note_id,
                "spent_for_payment_id": payment.id,  # so a reversal of this payment undoes the spend
                "invoice_id": sp.invoice_id,
                "allocated_amount": sp.amount,
                "matched_layer": None,
                "tolerance_applied": None,
                "fx_rate_used": None,
                "tolerance_residual": None,
                "tds_deducted": None,
                "allocated_by": None,
                "created_by": actor,
                "updated_by": actor,
            }
        )
    await payments_repo.insert_allocations(db, allocations)

    spent_on: dict[uuid.UUID, Decimal] = {}
    spent: list[tuple[str, Decimal, str]] = []
    for sp in app.spends:
        spent_on[sp.invoice_id] = spent_on.get(sp.invoice_id, _ZERO) + sp.amount
        spent.append((sp.note_number, sp.amount, by_id[sp.invoice_id].invoice_number))
        n = spend_notes[sp.note_id]
        before_note = n.status
        n.remaining_amount = n.remaining_amount - sp.amount
        n.status = NoteStatus(note_status_after(n.remaining_amount, n.amount))
        n.updated_by = actor
        logs_repo.add_state_log(
            db,
            entity_type="note",
            entity_id=n.id,
            from_state=before_note.value,
            to_state=n.status.value,
            triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM,
            triggered_by_id=actor,
            reason=(
                f"Payment {reference}: {sp.amount} spent on invoice {by_id[sp.invoice_id].invoice_number} "
                "as the advice instructed"
            ),
            occurred_at=now,
        )

    for s in plan.slices:
        invoice = by_id[s.invoice_id]
        before = invoice.status
        credit_spent = spent_on.get(invoice.id, _ZERO)
        invoice.amount_paid = invoice.amount_paid + s.allocated + credit_spent
        invoice.status = (
            InvoiceStatus.PAID
            if invoice.amount_paid >= invoice.collectible_total
            else InvoiceStatus.PARTLY_PAID
        )
        invoice.updated_by = actor
        logs_repo.add_state_log(
            db,
            entity_type="invoice",
            entity_id=invoice.id,
            from_state=before.value,
            to_state=invoice.status.value,
            triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM,
            triggered_by_id=actor,
            reason=(
                f"Payment {reference}: {s.allocated} applied ({s.tolerance.value}, "
                f"{_tolerance_note(plan, note)})"
                + (f"; plus {credit_spent} from credit note(s) as the advice instructed" if credit_spent else "")
            ),
            occurred_at=now,
        )
    await db.flush()

    # --- the payment itself ---
    residuals = sum((s.residual or _ZERO for s in plan.slices), _ZERO)
    received = money(app.received_inr)
    if plan.allocated_total + residuals + unapplied + credited != received:
        # never write money that does not add up
        raise AssertionError(
            f"payment {payment.id}: {received} received != {plan.allocated_total} allocated + "
            f"{residuals} residual + {unapplied} unapplied + {credited} credited"
        )
    state = PaymentState.APPLIED if unapplied == 0 else PaymentState.PARTIALLY_APPLIED
    payment.amount_received_inr = received
    payment.unapplied_amount = unapplied
    payment.held_amount = _ZERO
    payment.credited_amount = credited
    payment.confidence_score = app.score
    payment.matched_layer = app.layer
    payment.match_reason = f"{app.reason}; {_tolerance_note(plan, note)}"
    payment.review_reasons = None
    payment.state = state
    payment.updated_by = actor
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before_state.value,
        to_state=state.value,
        triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM,
        triggered_by_id=actor,
        reason=payment.match_reason,
        occurred_at=now,
    )
    await db.flush()
    return Applied(payment, invoices, note, state, spent)
