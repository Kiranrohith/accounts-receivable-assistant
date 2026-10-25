"""Payments waiting for a person (D8): decide a NeedsReview payment, or apply an Unapplied one.

  match      the reviewer says which invoices the payment pays and how much of each; it goes through
             the SAME apply as the matcher (locks, re-check, identity), so nothing here can write
             money that does not add up. A line on an invoice that is already Paid is confirmed as
             a re-payment: it becomes a credit note and the payment is NOT rejected.
  unapplied  keep the money on the customer's account to apply later (state Unapplied)
  reject     the payment is not ours / not valid (Rejected, every bucket zero)
  amount     the advice did not state the amount: the reviewer enters it and matching runs again
  alias      remember the advice's payer name or sender e-mail for a customer

Every decision is row-locked: of two simultaneous decisions one wins and the other gets 409.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import BUSINESS_TIMEZONE
from core.exceptions.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailure,
)
from core.services.invoices.amounts import money
from core.services.invoices.fx import is_foreign, rate_for_invoice
from core.services.payments import holds
from core.services.payments.apply import Application, Contention, NotMatching, apply
from core.services.payments.engine import Status, run_matching
from core.services.payments.listing import DECIDABLE
from core.services.payments.reference import reference_key
from core.services.payments.tolerance import Target, split_by_statement
from core.services.users.users import permission_codes_for
from data.models.postgres import Customer, Payment, User
from data.models.postgres.enums import (
    AliasType,
    InvoiceStatus,
    MatchedLayer,
    PaymentState,
    Tolerance,
    TriggerType,
)
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from schemas.payments.review import (
    AliasResult,
    AllocationResult,
    EnterAmount,
    LeaveUnapplied,
    ManualMatch,
    RejectPayment,
    ReviewResult,
    SaveAlias,
)

_ZERO = Decimal("0.00")


def _log(db: AsyncSession, payment: Payment, before: PaymentState, user: User, reason: str) -> None:
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before.value,
        to_state=payment.state.value,
        triggered_by_type=TriggerType.USER,
        triggered_by_id=user.id,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def _lock(
    db: AsyncSession, payment_id: uuid.UUID, allowed: tuple[PaymentState, ...] = DECIDABLE
) -> Payment:
    payment = await payments_repo.lock_payment(db, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found")
    if payment.state not in allowed:
        raise ConflictError(f"This payment is {payment.state.value}; it cannot be decided that way now")
    return payment


async def _customer(db: AsyncSession, payment: Payment, chosen: uuid.UUID | None) -> Customer:
    customer_id = chosen or payment.customer_id
    if customer_id is None:
        raise ValidationFailure("This payment has no customer yet: say which customer paid (customer_id)")
    customer = await customers_repo.get_by_id(db, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    if not customer.is_active:
        raise ValidationFailure(f"Customer '{customer.customer_name}' is deactivated")
    return customer


def _own_amount(payment: Payment) -> Decimal | None:
    foreign = is_foreign(payment.payment_currency)
    return payment.amount_received_foreign if foreign else payment.amount_received_inr


def _name_value(payment: Payment, kind: str, given: str | None) -> str:
    value = given or (payment.payee_name if kind == "name" else payment.from_email)
    value = " ".join(value.split()) if value else ""
    if not value:
        what = "payer name" if kind == "name" else "sender e-mail"
        raise ValidationFailure(f"The advice has no {what} to save; give alias_value")
    return value.lower() if kind == "email" else value


async def _save_alias(
    db: AsyncSession, user: User, customer: Customer, payment: Payment, kind: str, given: str | None
) -> tuple[object, bool]:
    alias_type = AliasType(kind)
    value = _name_value(payment, kind, given)
    existing = await customers_repo.get_alias(db, customer.id, alias_type, value)
    if existing is not None:
        return existing, False
    alias = await customers_repo.insert_alias(
        db, customer_id=customer.id, alias_type=alias_type, value=value, actor_id=user.id
    )
    return alias, True


# ----------------------------------------------------------------------------------------------


async def manual_match(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: ManualMatch
) -> ReviewResult:
    if body.save_alias and "customers.alias.confirm" not in await permission_codes_for(db, user):
        raise PermissionDeniedError("Missing permission: customers.alias.confirm (to save an alias)")
    payment = await _lock(db, payment_id)
    customer = await _customer(db, payment, body.customer_id)
    foreign = is_foreign(payment.payment_currency)
    received_own = _own_amount(payment)
    if received_own is None or received_own <= 0:
        raise ValidationFailure("This payment has no amount yet: enter it first (POST .../amount)")

    ids = [line.invoice_id for line in body.lines]
    if len(set(ids)) != len(ids):
        raise ValidationFailure("An invoice can appear only once in the match")
    invoices = {i.id: i for i in await invoices_repo.lock_invoices(db, ids)}
    for line in body.lines:
        invoice = invoices.get(line.invoice_id)
        if invoice is None:
            raise NotFoundError(f"Invoice {line.invoice_id} not found")
        if invoice.customer_id != customer.id:
            raise ValidationFailure(
                f"Invoice {invoice.invoice_number} does not belong to {customer.customer_name}"
            )
        if foreign and invoice.invoice_currency != payment.payment_currency:
            raise ValidationFailure(
                f"The payment is in {payment.payment_currency} but invoice {invoice.invoice_number} "
                f"is in {invoice.invoice_currency}"
            )

    already_paid = [invoices[i].invoice_number for i in ids if invoices[i].status is InvoiceStatus.PAID]
    if already_paid and not body.confirm_repayment:
        raise ConflictError(
            f"Invoice {', '.join(already_paid)} is already paid. If this is a re-payment, send "
            "confirm_repayment=true and that amount becomes a credit note"
        )

    total = sum((money(line.amount) for line in body.lines), _ZERO)
    paid_own = money(received_own)
    if foreign and total != paid_own:
        raise ValidationFailure(
            f"The amounts must add up to the amount paid ({payment.payment_currency} {paid_own}); "
            f"they add up to {total}"
        )
    if total > paid_own:
        raise ValidationFailure(f"The amounts ({total}) are more than the payment ({paid_own})")
    remainder = paid_own - total  # INR only: stays unapplied

    open_targets: list[Target] = []
    open_amounts: list[Decimal] = []
    expected: dict[uuid.UUID, Decimal] = {}
    fx_rate: dict[uuid.UUID, Decimal | None] = {}
    credit_inr = _ZERO
    credit_invoice: uuid.UUID | None = None
    received_inr = remainder
    for line in body.lines:
        invoice = invoices[line.invoice_id]
        rate = (invoice.fx_rate_invoice or Decimal("1")) if foreign else Decimal("1")
        amount_inr = money(money(line.amount) * rate)
        received_inr += amount_inr
        if invoice.status is InvoiceStatus.PAID:  # a re-payment, confirmed here: it becomes a credit
            credit_inr += amount_inr
            credit_invoice = credit_invoice or invoice.id
            continue
        if invoice.po_hold:
            raise ConflictError(
                f"Invoice {invoice.invoice_number} is on purchase-order hold; release the hold first"
            )
        blocking = holds.holding_of((await holds.open_disputes_by_invoice(db, [invoice])).get(invoice.id))
        if blocking:
            raise ConflictError(
                f"Invoice {invoice.invoice_number} has a dispute that holds payments ({holds.names(blocking)}); "
                "classify or resolve it first, or leave the payment unapplied"
            )
        balance = money(invoice.collectible_balance)
        open_targets.append(Target(invoice.id, balance))
        open_amounts.append(amount_inr)
        expected[invoice.id] = balance
        fx_rate[invoice.id] = invoice.fx_rate_invoice if is_foreign(invoice.invoice_currency) else None

    plan = split_by_statement(
        open_targets, open_amounts, foreign=foreign, unplaced=money(remainder)
    )
    if credit_inr > 0:
        plan = replace(
            plan,
            credit=plan.credit + credit_inr,
            credit_invoice_id=credit_invoice,
            overall=Tolerance.T3 if plan.overall in (Tolerance.T1, Tolerance.T2) else plan.overall,
        )
    note_part = f" ({body.note})" if body.note else ""
    app = Application(
        plan=plan,
        layer=MatchedLayer.L4,
        score=None,
        reason=f"Matched by {user.username}{note_part}",
        received_inr=money(received_inr),
        expected_balance=expected,
        fx_rate=fx_rate,
    )

    if payment.customer_id != customer.id:
        payment.customer_id = customer.id
        await db.flush()
    try:
        applied = await apply(db, payment.id, app, user.id, expected_states=DECIDABLE)
    except Contention as exc:
        await db.rollback()
        raise ConflictError("An invoice changed while you were deciding; reload and try again") from exc
    except NotMatching as exc:
        await db.rollback()
        raise ConflictError("This payment was decided by someone else") from exc

    if body.save_alias:
        await _save_alias(db, user, customer, payment, body.save_alias, None)

    by_id = {i.id: i for i in applied.invoices}
    result = ReviewResult(
        payment_id=payment.id,
        state=applied.state.value,
        message=payment.match_reason or "",
        allocations=[
            AllocationResult(
                invoice_number=by_id[s.invoice_id].invoice_number,
                allocated=s.allocated,
                tolerance=s.tolerance.value,
                invoice_status=by_id[s.invoice_id].status.value,
            )
            for s in plan.slices
        ],
        credit_note_number=applied.note.note_number if applied.note else None,
        unapplied_amount=payment.unapplied_amount,
        credited_amount=payment.credited_amount,
    )
    await unit_of_work.commit(db)
    return result


async def _provisional_rate(
    db: AsyncSession, payment: Payment, customer: Customer
) -> tuple[Decimal, str]:
    """The rate that values a foreign payment while it is on no invoice yet (provisional: it is
    replaced by the chosen invoice's frozen rate when the payment is applied).

    The frozen rate of the customer's open invoices in that currency, when they all share one;
    otherwise the ECB rate for the payment date.
    """
    open_invoices = await invoices_repo.open_invoices_for_customer(db, customer.id)
    rates = {
        i.fx_rate_invoice
        for i in open_invoices
        if i.invoice_currency == payment.payment_currency and i.fx_rate_invoice is not None
    }
    if len(rates) == 1:
        rate = rates.pop()
        return rate, f"the frozen rate {rate} of {customer.customer_name}'s open {payment.payment_currency} invoices"
    on = payment.payment_date or datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()
    found = await rate_for_invoice(payment.payment_currency, on)
    return found.inr_per_unit, f"the ECB rate for {on}"


async def leave_unapplied(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: LeaveUnapplied
) -> ReviewResult:
    payment = await _lock(db, payment_id, (PaymentState.NEEDS_REVIEW,))
    customer = await _customer(db, payment, body.customer_id)
    received_own = _own_amount(payment)
    if received_own is None or received_own <= 0:
        raise ValidationFailure("This payment has no amount yet: enter it first (POST .../amount)")
    valued = ""
    if is_foreign(payment.payment_currency):
        rate, source = await _provisional_rate(db, payment, customer)
        received_inr = money(received_own * rate)
        valued = f" (valued provisionally at {source}; the chosen invoice's frozen rate replaces it)"
    else:
        received_inr = money(received_own)

    before = payment.state
    payment.customer_id = customer.id
    payment.amount_received_inr = received_inr
    payment.unapplied_amount = received_inr
    payment.held_amount = _ZERO
    payment.held_for_dispute_id = None
    payment.credited_amount = _ZERO
    payment.state = PaymentState.UNAPPLIED
    payment.review_reasons = None
    payment.match_reason = f"Left unapplied by {user.username}: {body.reason}{valued}"
    payment.updated_by = user.id
    _log(db, payment, before, user, payment.match_reason)
    await db.flush()
    result = ReviewResult(
        payment_id=payment.id,
        state=payment.state.value,
        message=payment.match_reason,
        unapplied_amount=received_inr,
    )
    await unit_of_work.commit(db)
    return result


async def mark_rejected(db: AsyncSession, payment: Payment, user: User, reason: str) -> None:
    """A payment under review ends Rejected. Flushes only: the caller holds the payment's lock and commits."""
    before = payment.state
    payment.state = PaymentState.REJECTED
    payment.unapplied_amount = _ZERO
    payment.held_amount = _ZERO
    payment.held_for_dispute_id = None
    payment.credited_amount = _ZERO
    payment.match_reason = f"Rejected by {user.username}: {reason}"
    payment.updated_by = user.id
    _log(db, payment, before, user, payment.match_reason)
    await db.flush()


async def reject(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: RejectPayment
) -> ReviewResult:
    payment = await _lock(db, payment_id, (PaymentState.NEEDS_REVIEW,))
    await mark_rejected(db, payment, user, body.reason)
    result = ReviewResult(payment_id=payment.id, state=payment.state.value, message=payment.match_reason)
    await unit_of_work.commit(db)
    return result


async def enter_amount(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: EnterAmount
) -> ReviewResult:
    payment = await _lock(db, payment_id, (PaymentState.NEEDS_REVIEW,))
    if payment.amount_received_inr is not None or payment.amount_received_foreign is not None:
        raise ConflictError("This payment already has an amount")
    currency = (payment.payment_currency or body.currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValidationFailure("The advice did not state a currency: give one (currency)")
    amount = money(body.amount)
    # the matcher applies what it is given, so a payment without a transaction reference gets one here or goes no further
    given = (body.reference or "").strip()
    key = reference_key(given or payment.reference_number)
    if key is None:
        raise ValidationFailure("The advice has no transaction reference: give the UTR / reference number (reference)")
    if given and key != reference_key(payment.reference_number) and payment.customer_id is not None:
        earlier = await payments_repo.find_duplicate(db, payment.customer_id, key)
        if earlier is not None and earlier.id != payment.id:
            raise ConflictError(f"Reference {given} was already received from this customer: reject this payment as a duplicate")

    before = payment.state
    payment.payment_currency = currency
    if given:
        payment.reference_number = given
    if is_foreign(currency):
        payment.amount_received_foreign = amount
    else:
        payment.amount_received_inr = amount
    payment.state = PaymentState.MATCHING
    payment.review_reasons = None
    payment.match_reason = f"Amount {currency} {amount} entered by {user.username} (not on the advice)" + (
        f", reference {given}" if given else ""
    )
    payment.updated_by = user.id
    _log(db, payment, before, user, payment.match_reason)
    await db.flush()
    await unit_of_work.commit(db)

    outcome = await run_matching(db, payment_id, user.id)  # same matcher as an upload; it commits
    payment = await payments_repo.get_payment(db, payment_id)
    await db.refresh(payment)
    return ReviewResult(
        payment_id=payment_id,
        state=outcome.state.value if outcome.state else payment.state.value,
        message=payment.match_reason or "",
        allocations=[
            AllocationResult(
                invoice_number=a["invoice_number"],
                allocated=Decimal(a["allocated"]),
                tolerance=a["tolerance"],
                invoice_status=a["invoice_status"],
            )
            for a in outcome.allocations
        ],
        credit_note_number=outcome.note_number,
        unapplied_amount=payment.unapplied_amount,
        credited_amount=payment.credited_amount,
        reasons=outcome.reasons if outcome.status in (Status.REVIEW, Status.PARKED) else [],
    )


async def save_alias(
    db: AsyncSession, user: User, payment_id: uuid.UUID, body: SaveAlias
) -> AliasResult:
    payment = await payments_repo.get_payment(db, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found")
    customer = await _customer(db, payment, body.customer_id)
    alias, created = await _save_alias(db, user, customer, payment, body.alias_type, body.alias_value)
    if not created:
        raise ConflictError(f"'{alias.alias_value}' is already an alias of {customer.customer_name}")
    result = AliasResult(
        alias_id=alias.id,
        customer_id=customer.id,
        alias_type=body.alias_type,
        alias_value=alias.alias_value,
    )
    await unit_of_work.commit(db)
    return result
