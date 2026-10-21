"""Match an identified payment to the invoice(s) it pays and apply it (Flow 2, D3-D7).

`run_matching(db, payment_id)` takes a payment in state Matching (its customer is known) and ends
it as one of:
  APPLIED  allocations written, invoices moved, the payment Applied / PartiallyApplied
  REVIEW   NeedsReview with the reasons: low score, a tie, the amounts do not add up, an invoice
           already paid, two invoices each under a dispute that holds money, a currency we cannot
           value, or contention it could not win
  PARKED   still Matching: a target invoice is on PO hold; it is matched again when released

Which invoices: the ones the advice quotes (L1), else the customer's open invoices (L2/L3): every
single invoice plus every combination that fits the amount. A best score of 85 or more with no tie
applies by itself; a short payment from a certain identity (L1, or the customer's only open
invoice) applies as Partly paid with a flag whatever the score.

An invoice with a dispute that holds money (uncategorised, Other, or about the payment itself) has
its slice of the payment frozen (payments.held_amount, pinned to the oldest such dispute) while the
other slices apply; other open disputes only tell their owner that money arrived (see holds.py and
release.py).

Foreign payments are valued at the frozen rate of the invoice(s) they pay; a set that mixes
currencies or frozen rates cannot be valued and goes to review.

Credit notes are spent only on the advice's instruction (a quoted note number, or "use our
credit"), and only against what the payment leaves unpaid. A bad instruction (unknown, void or
another customer's note; no open note at all) goes to review. Without an instruction nothing is
spent; the open credit is only mentioned.
"""

import asyncio
import random
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import (
    BACKOFF_JITTER,
    BUSINESS_TIMEZONE,
    CONTENTION_BACKOFF_SECONDS,
    REMATCH_CAP,
    ROUNDING_TOLERANCE,
)
from core.services.invoices.amounts import money
from core.services.invoices.fx import is_foreign
from core.services.payments import holds
from core.services.payments.apply import Application, Contention, NotMatching, apply
from core.services.payments.credit import Credit, Spend, plan_spends
from core.services.payments.matching import (
    Cand,
    ScoredSet,
    candidate_sets,
    choose,
    number_shape,
    score_set,
)
from core.services.payments.tolerance import (
    Plan,
    Target,
    split_by_statement,
    split_over_set,
)
from data.models.postgres import Invoice, Payment
from data.models.postgres.enums import (
    InvoiceStatus,
    MatchedLayer,
    PaymentState,
    Tolerance,
    TriggerType,
)
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo

_ZERO = Decimal("0.00")
PARKED_PREFIX = "Parked:"


class Status(str, Enum):
    APPLIED = "applied"
    REVIEW = "review"
    PARKED = "parked"
    NOT_WAITING = "not_waiting"  # the payment was not in Matching: nothing to do


@dataclass
class MatchOutcome:
    status: Status
    state: PaymentState | None = None
    reasons: list[str] = field(default_factory=list)
    layer: MatchedLayer | None = None
    score: int | None = None
    allocations: list[dict] = field(default_factory=list)
    note_number: str | None = None
    credit_spent: list[dict] = field(default_factory=list)  # credit notes spent on the advice's instruction
    held: dict | None = None  # the slice frozen for a dispute: {invoice_number, amount}


class _Review(Exception):
    """Decided: this payment needs a person. Carries the reasons."""

    def __init__(self, reasons: list[str], score: int | None = None) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.score = score


class _Park(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _order(invoice: Invoice) -> tuple:
    return (invoice.due_date, invoice.created_at, invoice.invoice_number)


def _today() -> date:
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()


def _cand(invoice: Invoice) -> Cand:
    return Cand(
        invoice.id,
        invoice.invoice_number,
        invoice.due_date,
        money(invoice.collectible_balance),
        invoice.fx_rate_invoice if invoice.fx_rate_invoice is not None else Decimal("1"),
    )


def _stated_amount(row: dict) -> Decimal | None:
    value = row.get("amount")
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


async def to_review(
    db: AsyncSession, payment: Payment, reasons: list[str], score: int | None = None
) -> None:
    """Park the payment with a person. Every money bucket is zero while it waits."""
    before = payment.state
    payment.state = PaymentState.NEEDS_REVIEW
    payment.review_reasons = reasons
    payment.match_reason = "Waiting for a person: " + reasons[0]
    payment.confidence_score = score
    payment.unapplied_amount = _ZERO
    payment.held_amount = _ZERO
    payment.held_for_dispute_id = None
    payment.credited_amount = _ZERO
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before.value,
        to_state=PaymentState.NEEDS_REVIEW.value,
        triggered_by_type=TriggerType.SYSTEM,
        reason="; ".join(reasons),
        occurred_at=datetime.now(UTC),
    )
    await db.flush()


async def _credit_plan(
    db: AsyncSession, payment: Payment, leftovers: list[tuple[uuid.UUID, Decimal]]
) -> tuple[list[Spend], dict[uuid.UUID, Decimal], str | None]:
    """The credit-note spends the advice asks for, or a hint when it did not ask.

    Returns (spends, each spent note's remaining when planned, a hint to add to the explanation).
    Raises _Review when the instruction cannot be followed.
    """
    if not leftovers:
        return [], {}, None
    eligible = await payments_repo.eligible_notes_for_customer(db, payment.customer_id)
    credits = [Credit(n.id, n.note_number, money(n.remaining_amount), n.created_at) for n in eligible]
    quoted = (payment.extracted_credit_note_ref or "").strip()
    specific: Credit | None = None
    if quoted:
        note = await payments_repo.get_note_by_number(db, quoted)
        if note is None or note.customer_id != payment.customer_id:
            raise _Review([f"The advice quotes credit note {quoted}, which is not one of this customer's notes"])
        specific = next((c for c in credits if c.note_id == note.id), None)
        if specific is None:
            raise _Review(
                [f"Credit note {note.note_number} cannot be used ({note.status.value}, nothing left to spend)"]
            )
    elif payment.credit_note_requested:
        if not credits:
            raise _Review(["The advice asks us to use the customer's credit, but they have no open credit note"])
    else:
        if credits:
            total = sum((c.remaining for c in credits), Decimal("0"))
            return [], {}, (
                f"the customer has open credit notes worth {money(total)} "
                f"({', '.join(c.number for c in credits)}); none was spent because the advice did not say to use them"
            )
        return [], {}, None
    spends = plan_spends(leftovers, credits, specific=specific)
    remaining = {c.note_id: c.remaining for c in credits}
    if specific is not None:
        remaining.setdefault(specific.note_id, specific.remaining)
    return spends, {sp.note_id: remaining[sp.note_id] for sp in spends}, None


def _life(invoices: list[Invoice]) -> str:
    return ", ".join(i.invoice_number for i in invoices)


async def _carve_dispute_hold(
    db: AsyncSession, plan: Plan, used: list[Invoice]
) -> tuple[Plan, Decimal, Invoice | None, str]:
    """Take out of the plan the slice of an invoice that has a dispute holding money.

    A dispute that holds (uncategorised, Other, or about the payment itself) freezes the WHOLE slice
    the remittance gives that invoice: there is no disputed-amount to freeze less, and applying part
    of it would presume the dispute's outcome. The other slices apply as planned. Returns the plan
    without that slice, the amount held (as received), the invoice and a line for the explanation.
    Raises _Review when the payment cannot be split that cleanly.
    """
    disputes = await holds.open_disputes_by_invoice(db, used)
    holding_invoices = [i for i in used if holds.holding_of(disputes.get(i.id))]
    if not holding_invoices:
        return plan, _ZERO, None, ""
    if len(holding_invoices) > 1:
        which = "; ".join(
            f"{i.invoice_number} (dispute {holds.names(holds.holding_of(disputes[i.id]))})"
            for i in holding_invoices
        )
        raise _Review(
            [
                f"More than one invoice in this payment has a dispute that holds money: {which}. "
                "The first would be held and the second cannot be, so a person must decide where the money goes"
            ]
        )
    invoice = holding_invoices[0]
    if plan.credit > 0 or plan.unapplied > 0:
        raise _Review(
            [
                f"Invoice {invoice.invoice_number} has a dispute that holds money, and this payment also has "
                "money beyond its invoices (an overpayment or an invoice we do not have): "
                "a person must decide where the money goes"
            ]
        )
    sliced = next(s for s in plan.slices if s.invoice_id == invoice.id)
    amount = money(sliced.allocated + (sliced.residual or _ZERO))
    pinned = holds.holding_of(disputes[invoice.id])[0]
    note = (
        f"{amount} is held for dispute {holds.names([pinned])} on invoice {invoice.invoice_number} "
        "until the dispute is classified or resolved"
    )
    return replace(plan, slices=[s for s in plan.slices if s.invoice_id != invoice.id]), amount, invoice, note


async def _plan(db: AsyncSession, payment: Payment) -> tuple[Application, list[Invoice]]:
    """Decide what to apply, or raise _Review / _Park. Reads only."""
    customer_id = payment.customer_id
    if customer_id is None:
        raise _Review(["The payment has no customer yet, so it cannot be matched"])
    foreign = is_foreign(payment.payment_currency)
    received_own = payment.amount_received_foreign if foreign else payment.amount_received_inr
    if received_own is None or received_own <= 0:
        raise _Review(["The payment has no usable amount, so it cannot be matched"])
    layer = payment.matched_layer if payment.matched_layer in (
        MatchedLayer.L1, MatchedLayer.L2, MatchedLayer.L3
    ) else MatchedLayer.L3
    payment_date = payment.payment_date or _today()

    # --- what the advice quotes ---
    rows = [r for r in (payment.extracted_invoice_numbers or []) if (r.get("invoice_number") or "").strip()]
    numbers = [r["invoice_number"].strip() for r in rows]
    stated = {r["invoice_number"].strip(): _stated_amount(r) for r in rows}
    found = {
        i.invoice_number: i
        for i in await invoices_repo.get_by_numbers(db, numbers)
        if i.customer_id == customer_id
    }
    unknown = [n for n in numbers if n not in found]
    quoted_open = sorted((i for i in found.values() if i.status is not InvoiceStatus.PAID), key=_order)
    quoted_paid = [i for i in found.values() if i.status is InvoiceStatus.PAID]
    open_invoices = await invoices_repo.open_invoices_for_customer(db, customer_id)

    if quoted_paid:
        raise _Review(
            [
                f"Invoice {_life(sorted(quoted_paid, key=_order))} is already paid (closed). If this is a "
                "repeat or an extra payment, a person must confirm it; nothing was applied"
            ]
        )

    shapes = {number_shape(i.invoice_number) for i in [*open_invoices, *found.values()]}
    shape_known = any(number_shape(n) in shapes for n in numbers)

    if quoted_open:
        layer = MatchedLayer.L1
        pool = quoted_open
    else:
        pool = open_invoices
        if not pool:
            raise _Review(["The customer has no open invoice this payment can be applied to"])
        if foreign:
            pool = [i for i in pool if i.invoice_currency == payment.payment_currency]
            if not pool:
                raise _Review(
                    [
                        f"The payment is in {payment.payment_currency} but the customer has no open "
                        f"{payment.payment_currency} invoice; a third-currency payment needs a person"
                    ]
                )

    # --- the INR value of the payment for a set of invoices ---
    def received_for(members: tuple[Cand, ...]) -> Decimal | None:
        if not foreign:
            return money(received_own)
        rates = {m.rate for m in members}
        return money(received_own * rates.pop()) if len(rates) == 1 else None

    invoices_by_id = {i.id: i for i in pool}
    reasons_extra: list[str] = []

    if layer is MatchedLayer.L1 and quoted_open:
        members = tuple(_cand(i) for i in quoted_open)
        if foreign:
            wrong = [i for i in quoted_open if i.invoice_currency != payment.payment_currency]
            if wrong:
                raise _Review(
                    [
                        f"The payment is in {payment.payment_currency} but invoice "
                        f"{_life(wrong)} is in {wrong[0].invoice_currency}; a person must value it"
                    ]
                )
        received_inr = received_for(members)
        if received_inr is None:
            raise _Review(
                [
                    "The invoices quoted were frozen at different exchange rates, so the payment "
                    "cannot be valued automatically"
                ]
            )
        scored_sets = [
            (
                score_set(
                    layer, members, received_inr, foreign=foreign, payment_date=payment_date,
                    customer_has_one_open_invoice=len(open_invoices) == 1,
                    quoted_shape_known=shape_known,
                ),
                members,
            )
        ]
        choice = choose([s for s, _ in scored_sets])
        targets = [Target(m.id, m.balance) for m in members]

        # the advice's own split, when it gives an amount for every invoice it names
        all_stated = len(rows) == len(numbers) and numbers and all(stated[n] is not None for n in numbers)
        if all_stated:
            rate = members[0].rate if foreign else Decimal("1")
            total_stated = money(sum((stated[n] for n in numbers), Decimal("0")) * rate)
            if abs(total_stated - received_inr) > Decimal(ROUNDING_TOLERANCE):
                raise _Review(
                    [
                        f"The advice's own split ({total_stated}) does not add up to the amount paid "
                        f"({received_inr}); please check it"
                    ]
                )
            amounts = [money(stated[i.invoice_number] * rate) for i in quoted_open]
            unplaced = money(sum((stated[n] for n in unknown), Decimal("0")) * rate)
            plan = split_by_statement(targets, amounts, foreign=foreign, unplaced=unplaced)
        else:
            plan = split_over_set(targets, received_inr, foreign=foreign, excess_is_credit=not unknown)
        if unknown:
            reasons_extra.append(
                f"{', '.join(unknown)} is not in our invoices; any money beyond the invoices we have "
                "stays unapplied"
            )
    else:
        cands = [_cand(i) for i in pool]
        sets, too_many = candidate_sets(cands, received_for, foreign=foreign)
        if not sets:
            raise _Review(["No invoice combination could be valued for this payment"])
        scored_sets = [
            (
                score_set(
                    layer, members, received_for(members), foreign=foreign, payment_date=payment_date,
                    customer_has_one_open_invoice=len(open_invoices) == 1,
                    quoted_shape_known=shape_known,
                ),
                members,
            )
            for members in sets
        ]
        choice = choose([s for s, _ in scored_sets])
        if too_many:
            reasons_extra.append(
                "The customer has many open invoices, so only single invoices were tried"
            )
        best_members = choice.best.members
        received_inr = choice.best.received
        plan = split_over_set(
            [Target(m.id, m.balance) for m in best_members], received_inr, foreign=foreign
        )
        invoices_by_id = {i.id: i for i in pool}

    best: ScoredSet = choice.best
    # --- gate: score, tie, or the short-payment bypass ---
    certain = layer is MatchedLayer.L1 or (layer is MatchedLayer.L2 and len(open_invoices) == 1)
    short_bypass = plan.overall is Tolerance.T4 and certain and not choice.tied_with
    if not (choice.auto or short_bypass):
        if choice.tied_with:
            options = [best, *choice.tied_with]
            why = "Several invoice combinations fit about equally well (" + "; ".join(
                f"{s.label}: score {s.score}" for s in options[:4]
            ) + "); please choose"
        else:
            why = f"The match scored {best.score} ({best.label}), below the automatic threshold; please check it"
        raise _Review([why, *reasons_extra], score=best.score)

    members = best.members
    expected_balance = {m.id: m.balance for m in members}
    used = [invoices_by_id[m.id] for m in members]
    on_hold = [i for i in used if i.po_hold]
    if on_hold:
        raise _Park(f"invoice {_life(on_hold)} is on purchase-order hold")
    try:
        plan, held_amount, held_invoice, held_note = await _carve_dispute_hold(db, plan, used)
    except _Review as review:
        review.score = best.score
        raise

    leftovers = [
        (s.invoice_id, money(expected_balance[s.invoice_id] - s.allocated))
        for s in plan.slices
        if expected_balance[s.invoice_id] - s.allocated > 0
    ]
    try:
        spends, note_remaining, credit_hint = await _credit_plan(db, payment, leftovers)
    except _Review as review:
        review.score = best.score
        raise

    how = {
        MatchedLayer.L1: f"the invoice number quoted ({', '.join(m.number for m in members)})",
        MatchedLayer.L2: f"the sender's e-mail and the amount ({best.label})",
        MatchedLayer.L3: f"the payer's name and the amount ({best.label})",
    }[layer]
    reason = f"Matched by {layer.value}: {how}; score {best.score}"
    if short_bypass and not choice.auto:
        reason += " (short payment from a certain identity: applied as partly paid)"
    if reasons_extra:
        reason += "; " + "; ".join(reasons_extra)
    if held_note:
        reason += f"; {held_note}"
    if spends:
        reason += "; credit note(s) spent as the advice instructed: " + ", ".join(
            f"{sp.note_number} {sp.amount}" for sp in spends
        )
    if credit_hint:
        reason += f"; {credit_hint}"

    app = Application(
        plan=plan,
        layer=layer,
        score=best.score,
        reason=reason,
        received_inr=received_inr,
        expected_balance=expected_balance,
        spends=spends,
        expected_note_remaining=note_remaining,
        fx_rate={
            m.id: (invoices_by_id[m.id].fx_rate_invoice if is_foreign(invoices_by_id[m.id].invoice_currency) else None)
            for m in members
        },
        held=held_amount,
        held_invoice_id=held_invoice.id if held_invoice is not None else None,
    )
    return app, used


async def _attempt(db: AsyncSession, payment_id: uuid.UUID, actor: uuid.UUID | None) -> MatchOutcome:
    payment = await payments_repo.get_payment(db, payment_id)
    if payment is None:
        return MatchOutcome(Status.NOT_WAITING)
    await db.refresh(payment)
    if payment.state is not PaymentState.MATCHING:
        return MatchOutcome(Status.NOT_WAITING, payment.state)
    try:
        app, _ = await _plan(db, payment)
    except _Review as review:
        await to_review(db, payment, review.reasons, review.score)
        await unit_of_work.commit(db)
        return MatchOutcome(Status.REVIEW, PaymentState.NEEDS_REVIEW, review.reasons, score=review.score)
    except _Park as park:
        payment.match_reason = f"{PARKED_PREFIX} {park.reason}; it is matched again when the hold is released"
        await unit_of_work.commit(db)
        return MatchOutcome(Status.PARKED, PaymentState.MATCHING, [park.reason])

    try:
        applied = await apply(db, payment_id, app, actor)
    except NotMatching:
        await db.rollback()
        return MatchOutcome(Status.NOT_WAITING)
    # read what the outcome needs before commit expires the objects
    by_id = {i.id: i for i in applied.invoices}
    allocations = [
        {
            "invoice_number": by_id[s.invoice_id].invoice_number,
            "allocated": str(s.allocated),
            "tolerance": s.tolerance.value,
            "invoice_status": by_id[s.invoice_id].status.value,
        }
        for s in app.plan.slices
    ]
    outcome = MatchOutcome(
        Status.APPLIED, applied.state, [applied.payment.match_reason], app.layer, app.score, allocations,
        applied.note.note_number if applied.note else None,
        credit_spent=[
            {"note_number": number, "amount": str(amount), "invoice_number": invoice}
            for number, amount, invoice in applied.spent
        ],
        held=(
            {"invoice_number": by_id[app.held_invoice_id].invoice_number, "amount": str(app.held)}
            if app.held_invoice_id is not None
            else None
        ),
    )
    await unit_of_work.commit(db)
    return outcome


async def run_matching(
    db: AsyncSession, payment_id: uuid.UUID, actor: uuid.UUID | None = None
) -> MatchOutcome:
    """Match and apply one payment, re-planning when another payment moved its invoices."""
    for attempt in range(REMATCH_CAP + 1):
        try:
            return await _attempt(db, payment_id, actor)
        except Contention:
            await db.rollback()
            payment = await payments_repo.get_payment(db, payment_id)
            await db.refresh(payment)
            payment.rematch_count += 1
            count = payment.rematch_count
            await unit_of_work.commit(db)
            if count > REMATCH_CAP:
                break
            delay = CONTENTION_BACKOFF_SECONDS[min(attempt, len(CONTENTION_BACKOFF_SECONDS) - 1)]
            await asyncio.sleep(delay * (1 + random.uniform(-float(BACKOFF_JITTER), float(BACKOFF_JITTER))))
    payment = await payments_repo.get_payment(db, payment_id)
    await db.refresh(payment)
    reasons = [
        f"Other payments kept changing the same invoices ({REMATCH_CAP} attempts); please check this "
        "payment's match"
    ]
    await to_review(db, payment, reasons)
    await unit_of_work.commit(db)
    return MatchOutcome(Status.REVIEW, PaymentState.NEEDS_REVIEW, reasons)
