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
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import (
    BACKOFF_JITTER,
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
    classify,
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
from data.repositories.invoices.numbers import normalize_invoice_no
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


class _Unapply(Exception):
    """Decided: the customer is known but the money fits no invoice. It is kept as unapplied cash."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Park(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _order(invoice: Invoice) -> tuple:
    return (invoice.due_date, invoice.created_at, invoice.invoice_number)


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


async def to_unapplied(db: AsyncSession, payment: Payment, reason: str) -> None:
    """The customer is known and the money pays nothing we can name: it waits as unapplied cash (a person
    can apply it later or convert it to a credit note). The whole INR amount is unapplied."""
    before = payment.state
    payment.state = PaymentState.UNAPPLIED
    payment.unapplied_amount = money(payment.amount_received_inr)
    payment.held_amount = _ZERO
    payment.held_for_dispute_id = None
    payment.credited_amount = _ZERO
    payment.review_reasons = None
    payment.match_reason = "Unapplied: " + reason
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=before.value,
        to_state=PaymentState.UNAPPLIED.value,
        triggered_by_type=TriggerType.SYSTEM,
        reason=payment.match_reason,
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


def _frozen_rate(currency: str, open_invoices: list[Invoice]) -> Decimal | None:
    """INR per unit of `currency`, when the customer's open invoices in it all froze the same rate."""
    if currency == "INR":
        return Decimal("1")
    rates = {i.fx_rate_invoice for i in open_invoices if i.invoice_currency == currency and i.fx_rate_invoice}
    return rates.pop() if len(rates) == 1 else None


def _currency_mismatch(
    payment: Payment, invoices: list[Invoice], open_invoices: list[Invoice], received_own: Decimal
) -> str:
    """Why a payment in one currency cannot be applied to invoices in another (C12), with the
    indicative conversion when the frozen rates on file allow one. Never auto-applied."""
    paid_in = payment.payment_currency or "INR"
    want = sorted({i.invoice_currency for i in invoices})
    head = (
        f"The payment is in {paid_in} but invoice {_life(sorted(invoices, key=_order))} "
        f"{'is' if len(invoices) == 1 else 'are'} in {', '.join(want)}: a person must value it"
    )
    from_rate = _frozen_rate(paid_in, open_invoices)
    notes = []
    for currency in want:
        to_rate = _frozen_rate(currency, open_invoices)
        if from_rate is None or to_rate is None:
            notes.append(f"no frozen {paid_in if from_rate is None else currency} rate is on file, so no conversion is shown")
            continue
        converted = money(received_own * from_rate / to_rate)
        notes.append(f"indicatively {paid_in} {money(received_own)} is about {currency} {converted} at the frozen rates on file")
    return head + "; " + "; ".join(notes) if notes else head


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
    payment_date = payment.payment_date  # None when the advice states none: then no date bonus is given

    # --- what the advice quotes (numbers are compared normalized: INV-1176 = inv 1176) ---
    rows: list[dict] = []
    seen_numbers: set[str] = set()
    for r in payment.extracted_invoice_numbers or []:
        number = (r.get("invoice_number") or "").strip()
        if number and normalize_invoice_no(number) not in seen_numbers:
            seen_numbers.add(normalize_invoice_no(number))
            rows.append(r)
    numbers = [r["invoice_number"].strip() for r in rows]
    stated = {normalize_invoice_no(r["invoice_number"]): _stated_amount(r) for r in rows}
    found = {
        normalize_invoice_no(i.invoice_number): i
        for i in await invoices_repo.get_by_numbers(db, numbers)
        if i.customer_id == customer_id
    }
    unknown = [n for n in numbers if normalize_invoice_no(n) not in found]
    quoted_open = sorted((i for i in found.values() if i.status is not InvoiceStatus.PAID), key=_order)
    quoted_paid = [i for i in found.values() if i.status is InvoiceStatus.PAID]
    open_invoices = await invoices_repo.open_invoices_for_customer(db, customer_id)

    stated_all = bool(numbers) and all(stated[normalize_invoice_no(n)] is not None for n in numbers)
    if quoted_paid and not (quoted_open and stated_all):
        # nothing else to apply, or no per-invoice amounts to split the payment by: a person decides
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
        mismatched = [i for i in quoted_open if i.invoice_currency != (payment.payment_currency or "INR")]
        if mismatched:
            raise _Review([_currency_mismatch(payment, mismatched, open_invoices, received_own)])
    else:
        pool = open_invoices
        if not pool:
            raise _Review(["The customer has no open invoice this payment can be applied to"])
        # only invoices in the payment's own currency can be matched; the rest need a person (C12)
        same = [i for i in pool if i.invoice_currency == (payment.payment_currency or "INR")]
        if not same:
            raise _Review([_currency_mismatch(payment, pool, open_invoices, received_own)])
        pool = same

    # --- the INR value of the payment for a set of invoices ---
    def received_for(members: tuple[Cand, ...]) -> Decimal | None:
        if not foreign:
            return money(received_own)
        rates = {m.rate for m in members}
        return money(received_own * rates.pop()) if len(rates) == 1 else None

    invoices_by_id = {i.id: i for i in pool}
    reasons_extra: list[str] = []
    review_notes: list[str] = []  # what a person must still decide on a payment that was applied in part

    if layer is MatchedLayer.L1 and quoted_open:
        members = tuple(_cand(i) for i in quoted_open)
        received_inr = received_for(members)
        if received_inr is None:
            raise _Review(
                [
                    "The invoices quoted were frozen at different exchange rates, so the payment "
                    "cannot be valued automatically"
                ]
            )
        rate = members[0].rate if foreign else Decimal("1")

        def stated_inr(numbers_: list[str]) -> Decimal:
            return money(sum((stated[normalize_invoice_no(n)] for n in numbers_), Decimal("0")) * rate)

        # quoted numbers whose money cannot be applied now: invoices we do not have, invoices already paid
        paid_numbers = [i.invoice_number for i in quoted_paid]
        carved = [*unknown, *paid_numbers]
        scored_received = received_inr
        if stated_all and carved:
            # the known part is judged on what the advice states for it, not on the whole payment
            scored_received = money(received_inr - stated_inr(carved))
        scored_sets = [
            (
                score_set(
                    layer, members, scored_received, foreign=foreign, payment_date=payment_date,
                    customer_has_one_open_invoice=len(open_invoices) == 1,
                    quoted_shape_known=shape_known,
                ),
                members,
            )
        ]
        choice = choose([s for s, _ in scored_sets])
        targets = [Target(m.id, m.balance) for m in members]

        # the advice's own split, when it gives an amount for every invoice it names
        if stated_all:
            total_stated = stated_inr(numbers)
            if abs(total_stated - received_inr) > Decimal(ROUNDING_TOLERANCE):
                raise _Review(
                    [
                        f"The advice's own split ({total_stated}) does not add up to the amount paid "
                        f"({received_inr}); please check it"
                    ]
                )
            amounts = [money(stated[normalize_invoice_no(i.invoice_number)] * rate) for i in quoted_open]
            plan = split_by_statement(targets, amounts, foreign=foreign, unplaced=stated_inr(carved) if carved else _ZERO)
        else:
            plan = split_over_set(targets, received_inr, foreign=foreign, excess_is_credit=not unknown)
        if unknown:
            reasons_extra.append(
                f"{', '.join(unknown)} is not in our invoices; any money beyond the invoices we have "
                "stays unapplied"
            )
        if paid_numbers:
            reasons_extra.append(
                f"Invoice {', '.join(paid_numbers)} is already paid (closed): the {stated_inr(paid_numbers)} the "
                "advice states for it stays unapplied until a person confirms it as a re-payment, which "
                "turns it into a credit note"
            )
            review_notes.append(
                f"The advice also pays {', '.join(paid_numbers)}, which is already paid: confirm it as a "
                "re-payment (it becomes a credit note) or reject it"
            )
    else:
        cands = [_cand(i) for i in pool]
        sets, too_many = candidate_sets(cands, received_for, foreign=foreign)
        if not sets:
            raise _Review(["No invoice combination could be valued for this payment"])
        if not foreign and not any(
            classify(received_for(m) or _ZERO, sum((c.balance for c in m), _ZERO), foreign=False).tolerance
            in (Tolerance.T1, Tolerance.T2)
            for m in sets
        ):
            # the customer is known (L2/L3) but no invoice, and no combination of them, takes this amount
            raise _Unapply(
                f"The amount {money(received_own)} fits no open invoice, nor any combination of this "
                "customer's open invoices"
                + (f"; {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not in our invoices" if unknown else "")
                + ". It is kept as unapplied cash: apply it to an invoice later or turn it into a credit note"
            )
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
    # decision 68: an over-payment bypasses the gate ONLY when the customer quoted our invoice number (L1; a sender or
    # customer conflict already went to review). Only a customer-named invoice justifies auto-creating a written
    # credit promise: without a quoted number (L2/L3) it is reviewed first and the note is minted on confirmation.
    over_bypass = plan.overall is Tolerance.T3 and layer is MatchedLayer.L1 and not choice.tied_with
    if not (choice.auto or short_bypass or over_bypass):
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
    if over_bypass and not choice.auto:
        reason += " (over-payment on a quoted invoice number: paid, the excess becomes a credit note)"
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
        review_notes=review_notes,
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
    except _Unapply as unapplied:
        await to_unapplied(db, payment, unapplied.reason)
        await unit_of_work.commit(db)
        return MatchOutcome(Status.APPLIED, PaymentState.UNAPPLIED, [unapplied.reason], payment.matched_layer)

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
