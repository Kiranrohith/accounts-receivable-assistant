"""Letting a dispute hold go (or move): the payment_rematch job for a payment with held money.

Queued by the dispute module whenever a dispute that touches the held invoice is classified or
resolved. The decision is SET-BASED, never read off the pinned dispute alone: under the payment's
and the invoice's locks the open disputes of the invoice are listed again, and

  a holding dispute is still there   the hold stays; when the pinned one is no longer holding, the
                                     hold is re-pinned to the oldest one still holding (logged)
  none is left                       the held slice is applied to the invoice like any other
                                     payment (tolerances, a credit note for an excess), or, when
                                     the invoice was paid or put on purchase-order hold meanwhile,
                                     goes to a person: nothing is applied onto a closed invoice

Lock order is the payment, then the invoice: the order the matcher's apply step takes them in.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.services import notifications
from core.services.disputes import holding
from core.services.disputes.outbound import reference_for
from core.services.invoices.amounts import money
from core.services.invoices.fx import is_foreign
from core.services.invoices.processing import finish_job, log_step
from core.services.jobs import queue
from core.services.payments import holds
from core.services.payments.engine import to_review
from core.services.payments.notes import create_credit_note
from core.services.payments.tolerance import Target, split_over_set
from core.services.reminders import lifecycle as reminder_lifecycle
from data.models.postgres import Dispute, Invoice, Job, Payment, SourceDocument
from data.models.postgres.enums import (
    InvoiceStatus,
    JobState,
    NotificationType,
    PaymentState,
    StepResult,
    TriggerType,
)
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo

_ZERO = Decimal("0.00")


@dataclass
class Result:
    action: str  # none | kept | moved | released | review
    detail: str = ""

    def as_dict(self) -> dict:
        return {"action": self.action, "detail": self.detail}


def is_held(payment: Payment | None) -> bool:
    return payment is not None and payment.held_amount > 0 and payment.held_for_dispute_id is not None


async def handle_held(db: AsyncSession, job: Job, document: SourceDocument, payment_id: uuid.UUID) -> None:
    """The job body for a payment with held money."""
    if not await queue.hold(db, job):
        return
    started = datetime.now(UTC)
    result = await reconcile(db, payment_id, job.created_by)
    log_step(
        db, job, document.id, started, step_name="hold_check",
        result=StepResult.WARNING if result.action == "review" else StepResult.SUCCESS,
        output_summary=result.as_dict(),
    )
    await unit_of_work.commit(db)
    await finish_job(db, job, JobState.SUCCEEDED)


def _log(db: AsyncSession, payment: Payment, before: str, actor: uuid.UUID | None, reason: str) -> None:
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before,
        to_state=payment.state.value,
        triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM,
        triggered_by_id=actor,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def reconcile(db: AsyncSession, payment_id: uuid.UUID, actor: uuid.UUID | None) -> Result:
    """Decide the held payment's fate now. Flushes; the caller commits."""
    peek = await payments_repo.get_payment(db, payment_id)
    first = await disputes_repo.get_dispute(db, peek.held_for_dispute_id) if is_held(peek) else None
    if first is None or first.invoice_id is None:
        return Result("none", "nothing is held for this payment")
    payment = await payments_repo.lock_payment(db, payment_id)
    if not is_held(payment):
        return Result("none", "the hold was already settled")
    pinned = await disputes_repo.get_dispute(db, payment.held_for_dispute_id)
    invoice = (await invoices_repo.lock_invoices(db, [pinned.invoice_id]))[0]

    open_disputes = await disputes_repo.open_on_invoices(db, [invoice.id])
    holding_now = holding.holding_in_order(open_disputes)
    pinned_ref = reference_for(pinned.id)
    if holding_now:
        if any(d.id == pinned.id for d in holding_now):
            return Result("kept", f"still held for {pinned_ref}")
        payment.held_for_dispute_id = holding_now[0].id
        payment.updated_by = actor
        _log(
            db, payment, payment.state.value, actor,
            f"The hold moved from dispute {pinned_ref} (no longer holding money) to dispute "
            f"{reference_for(holding_now[0].id)}, which still holds it",
        )
        await db.flush()
        return Result("moved", f"{pinned_ref} -> {reference_for(holding_now[0].id)}")

    # nothing holds the money any more
    held = money(payment.held_amount)
    problem = None
    if invoice.status is InvoiceStatus.PAID or money(invoice.collectible_balance) <= 0:
        problem = f"invoice {invoice.invoice_number} was paid in the meantime"
    elif invoice.po_hold:
        problem = f"invoice {invoice.invoice_number} is on purchase-order hold"
    if problem is not None:
        return await _to_a_person(db, payment, held, pinned_ref, problem, actor)
    return await _apply_held(db, payment, invoice, held, pinned_ref, actor, open_disputes)


async def _to_a_person(
    db: AsyncSession, payment: Payment, held: Decimal, ref: str, problem: str, actor: uuid.UUID | None
) -> Result:
    reason = f"The hold for dispute {ref} ended, but {problem}; a person must decide where {held} goes"
    before = payment.state
    if not await payments_repo.active_allocations_of(db, payment.id):
        await to_review(db, payment, [reason])  # nothing applied: the whole payment waits for a person
    else:
        # part of the payment is already applied: the held money becomes unapplied cash for a person
        payment.held_amount = _ZERO
        payment.held_for_dispute_id = None
        payment.unapplied_amount = money(payment.unapplied_amount + held)
        payment.state = PaymentState.PARTIALLY_APPLIED
        payment.review_reasons = [reason]
        payment.match_reason = f"{payment.match_reason or ''}; {reason}".strip("; ")
    payment.updated_by = actor
    _log(db, payment, before.value, actor, reason)
    await db.flush()
    if actor is not None:  # the person whose action ended the hold
        await notifications.create(
            db, actor, NotificationType.PAYMENT_DURING_DISPUTE,
            f"Payment {payment.reference_number or '(no reference)'}: {reason}",
            entity_type="payment", entity_id=payment.id, actor=actor,
        )
    return Result("review", reason)


async def _apply_held(
    db: AsyncSession, payment: Payment, invoice: Invoice, held: Decimal, ref: str,
    actor: uuid.UUID | None, open_disputes: list[Dispute],
) -> Result:
    reference = payment.reference_number or "(no reference)"
    plan = split_over_set(
        [Target(invoice.id, money(invoice.collectible_balance))], held, foreign=is_foreign(payment.payment_currency)
    )
    sliced = plan.slices[0]
    now = datetime.now(UTC)

    note = None
    credited = _ZERO
    if plan.credit > 0:
        note = await create_credit_note(
            db,
            customer_id=payment.customer_id,
            amount=plan.credit,
            reason=f"Overpayment on payment {reference}: {plan.credit} more than invoice "
            f"{invoice.invoice_number} owed when its dispute hold ended",
            actor=actor,
            linked_invoice_id=invoice.id,
            linked_payment_id=payment.id,
        )
        credited = money(plan.credit)

    clears = invoice.amount_paid + sliced.allocated >= invoice.collectible_total
    await payments_repo.insert_allocations(
        db,
        [
            {
                "payment_id": payment.id,
                "invoice_id": invoice.id,
                "allocated_amount": sliced.allocated,
                "matched_layer": payment.matched_layer,
                "tolerance_applied": sliced.tolerance,
                "fx_rate_used": invoice.fx_rate_invoice if is_foreign(invoice.invoice_currency) else None,
                "tolerance_residual": sliced.residual,
                "tds_deducted": invoice.tds_amount if clears and invoice.tds_amount else None,
                "allocated_by": None,
                "created_by": actor,
                "updated_by": actor,
            }
        ],
    )
    before_invoice = invoice.status
    invoice.amount_paid = invoice.amount_paid + sliced.allocated
    invoice.status = (
        InvoiceStatus.PAID if invoice.amount_paid >= invoice.collectible_total else InvoiceStatus.PARTLY_PAID
    )
    invoice.updated_by = actor
    logs_repo.add_state_log(
        db, entity_type="invoice", entity_id=invoice.id,
        from_state=before_invoice.value, to_state=invoice.status.value,
        triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM, triggered_by_id=actor,
        reason=(
            f"Payment {reference}: {sliced.allocated} applied when the hold for dispute {ref} ended "
            f"({sliced.tolerance.value})"
        ),
        occurred_at=now,
    )

    residual = sliced.residual or _ZERO
    if sliced.allocated + residual + credited + plan.unapplied != held:
        raise AssertionError(f"payment {payment.id}: releasing {held} does not add up")
    before = payment.state
    payment.held_amount = _ZERO
    payment.held_for_dispute_id = None
    payment.credited_amount = money(payment.credited_amount + credited)
    payment.unapplied_amount = money(payment.unapplied_amount + plan.unapplied)
    payment.state = PaymentState.APPLIED if payment.unapplied_amount == 0 else PaymentState.PARTIALLY_APPLIED
    payment.match_reason = (
        f"{payment.match_reason or ''}; the hold for dispute {ref} ended: {sliced.allocated} applied to "
        f"invoice {invoice.invoice_number}" + (f", credit note {note.note_number} for the excess" if note else "")
    ).strip("; ")
    payment.updated_by = actor
    _log(db, payment, before.value, actor, payment.match_reason)
    await db.flush()
    # money reached the invoice: reminder drafts waiting for a person are no longer wanted, and a collection task
    # whose invoice is now paid closes (the same as when a payment is applied the normal way)
    await reminder_lifecycle.invoice_changed_by_money(db, [invoice.id], user_id=actor)
    if open_disputes:  # other (non-holding) disputes are still open on the invoice: their owners hear of it
        await holds.tell_owners(db, payment, [holds.Touch(invoice, open_disputes, f"{held} released and applied")], actor)
    return Result("released", f"{sliced.allocated} applied to {invoice.invoice_number}")
