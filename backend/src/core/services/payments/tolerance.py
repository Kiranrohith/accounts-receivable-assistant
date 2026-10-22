"""Tolerances (D7) and how a received amount is split over invoices. Pure arithmetic, no I/O.

All amounts here are INR (a foreign payment is valued at its invoices' frozen rate first), against
the invoices' collectible balance. Two kinds of payment, two rules:

  INR payment on an INR invoice
    T1  |received - balance| <= TOLERANCE_T1_INR (inclusive)   -> Paid (signed residual = the difference)
    T3  more than that over                                     -> Paid + the excess becomes a credit note
    T4  more than that short                                    -> Partly paid + flag
    There is NO percentage band for INR: INR 1.01 short is T4, INR 1.01 over is T3.

  foreign payment on an invoice in the same currency
    T1  exactly the balance (the conversion is by the frozen rate, so the invoiced amount is exact)
    T2  any other difference within FX_BAND_PCT of the balance, short OR over (inclusive)
                                                                -> Paid, the signed difference is the
                                                                   residual; no credit note, no flag
    T3  beyond the band, over                                   -> Paid + credit note for the excess
    T4  beyond the band, short                                  -> Partly paid + flag

A payment in a different currency from the invoice never reaches this module: it goes to review.
An allocation clears the invoice's balance in T1-T3 (allocated = balance; the difference is the
residual in T1/T2 and the excess in T3) and is exactly what was received in T4.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import uuid

from constants.constants import FX_BAND_PCT, TOLERANCE_T1_INR
from core.services.invoices.amounts import money
from data.models.postgres.enums import Tolerance

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Fit:
    tolerance: Tolerance
    diff: Decimal  # received - balance, signed

    @property
    def clears_balance(self) -> bool:
        return self.tolerance is not Tolerance.T4


def classify(received: Decimal, balance: Decimal, *, foreign: bool) -> Fit:
    """`foreign`: a payment in a foreign currency on an invoice in that same currency."""
    diff = money(received - balance)
    if foreign:
        if diff == 0:
            return Fit(Tolerance.T1, diff)
        if balance > 0 and abs(diff) / balance <= FX_BAND_PCT / Decimal(100):
            return Fit(Tolerance.T2, diff)
    elif abs(diff) <= TOLERANCE_T1_INR:
        return Fit(Tolerance.T1, diff)
    return Fit(Tolerance.T3 if diff > 0 else Tolerance.T4, diff)


@dataclass(frozen=True)
class Target:
    """An invoice that can take money, in the order money is applied (oldest due date first)."""

    invoice_id: uuid.UUID
    balance: Decimal  # collectible balance, INR


@dataclass(frozen=True)
class Slice:
    invoice_id: uuid.UUID
    allocated: Decimal
    tolerance: Tolerance
    residual: Decimal | None  # signed; only T1/T2 slices carry one


@dataclass(frozen=True)
class Plan:
    slices: list[Slice]
    overall: Tolerance  # the set's verdict: drives the flag and the score
    credit: Decimal = _ZERO  # received beyond the balances (T3): becomes a credit note
    unapplied: Decimal = _ZERO  # received but not placed on any invoice: stays unapplied
    credit_invoice_id: uuid.UUID | None = None  # the note's linked invoice (default: the last slice)
    flags: list[str] = field(default_factory=list)

    @property
    def allocated_total(self) -> Decimal:
        return sum((s.allocated for s in self.slices), _ZERO)


def _worst(slices: list[Slice], excess: Decimal) -> Tolerance:
    kinds = {s.tolerance for s in slices}
    if Tolerance.T4 in kinds:
        return Tolerance.T4
    if excess > 0 or Tolerance.T3 in kinds:
        return Tolerance.T3
    return Tolerance.T2 if Tolerance.T2 in kinds else Tolerance.T1


def split_by_statement(
    targets: list[Target],
    stated: list[Decimal],
    *,
    foreign: bool,
    unplaced: Decimal = _ZERO,
) -> Plan:
    """The advice names an amount per invoice: each invoice is judged on its own amount.

    `unplaced` is money the advice assigns to invoices we do not have: it stays unapplied.
    """
    slices: list[Slice] = []
    excess = _ZERO
    for target, amount in zip(targets, stated, strict=True):
        fit = classify(amount, target.balance, foreign=foreign)
        if fit.tolerance in (Tolerance.T1, Tolerance.T2):
            slices.append(Slice(target.invoice_id, target.balance, fit.tolerance, fit.diff))
        elif fit.tolerance is Tolerance.T3:
            slices.append(Slice(target.invoice_id, target.balance, fit.tolerance, None))
            excess += fit.diff
        else:
            slices.append(Slice(target.invoice_id, money(amount), Tolerance.T4, None))
    return Plan(slices, _worst(slices, excess + unplaced), credit=excess, unapplied=unplaced)


def split_over_set(
    targets: list[Target],
    received: Decimal,
    *,
    foreign: bool,
    excess_is_credit: bool = True,
) -> Plan:
    """One amount for the whole set: judged against the set's total balance."""
    total = sum((t.balance for t in targets), _ZERO)
    fit = classify(received, total, foreign=foreign)
    if fit.tolerance is Tolerance.T4:
        remaining = money(received)
        slices: list[Slice] = []
        for target in targets:  # oldest first, until the money runs out
            if remaining <= 0:
                break
            take = min(target.balance, remaining)
            slices.append(Slice(target.invoice_id, take, Tolerance.T4, None))
            remaining -= take
        return Plan(slices, Tolerance.T4)
    slices = [Slice(t.invoice_id, t.balance, fit.tolerance, None) for t in targets]
    if fit.tolerance in (Tolerance.T1, Tolerance.T2):
        last = slices[-1]  # the signed difference rides on the last invoice
        slices[-1] = Slice(last.invoice_id, last.allocated, last.tolerance, fit.diff)
        return Plan(slices, fit.tolerance)
    if excess_is_credit:
        return Plan(slices, Tolerance.T3, credit=fit.diff)
    return Plan(slices, Tolerance.T3, unapplied=fit.diff)
