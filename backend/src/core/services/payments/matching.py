"""Scoring and choosing the invoice set a payment pays (D6). Pure logic, no I/O.

score = layer base (L1 60 / L2 45 / L3 35) + amount fit (T1 35 / T2 30 / over-short 15)
        + modifiers: earliest due date within 60 days of the payment (+5), the customer's single
        open invoice (+5, never for a set), a quoted number that has the shape of a known invoice
        number (+3).
Stored and shown clamped at 100; the tie rule compares the RAW scores, so clamping can neither
create nor hide a tie. A best score of CONFIDENCE_AUTO_APPLY or more applies by itself; a tie
within TIE_MARGIN (inclusive) of the runner-up, or a lower score, goes to a person.
"""

import itertools
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from constants.constants import (
    AMOUNT_FIT_SCORE,
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_MAX,
    DUE_DATE_MODIFIER_WINDOW_DAYS,
    LAYER_BASE_SCORE,
    MAX_SUBSET_INVOICES,
    MODIFIER_DUE_WITHIN_60D,
    MODIFIER_KNOWN_REF_PATTERN,
    MODIFIER_SINGLE_OPEN_INVOICE,
    TIE_MARGIN,
)
from core.services.payments.tolerance import Fit, classify
from data.models.postgres.enums import MatchedLayer, Tolerance

_DIGITS = re.compile(r"\d+")
_FIT_KEY: Final = {Tolerance.T1: "T1", Tolerance.T2: "T2"}


@dataclass(frozen=True)
class Cand:
    """An invoice that could be paid (INR collectible balance)."""

    id: object
    number: str
    due_date: date
    balance: Decimal
    rate: Decimal  # frozen rate to INR (1 for an INR invoice)


@dataclass(frozen=True)
class ScoredSet:
    members: tuple[Cand, ...]
    received: Decimal  # INR value of the payment for this set
    fit: Fit
    raw: int

    @property
    def score(self) -> int:
        return min(self.raw, CONFIDENCE_MAX)

    @property
    def label(self) -> str:
        return " + ".join(m.number for m in self.members)


@dataclass(frozen=True)
class Choice:
    best: ScoredSet | None
    tied_with: list[ScoredSet]  # sets within TIE_MARGIN of the best (not including it)
    auto: bool  # best exists, scores >= CONFIDENCE_AUTO_APPLY and nothing ties with it


def number_shape(number: str) -> str:
    """INV-2026-0042 -> INV-9-9: what an invoice number looks like, ignoring its digits."""
    return _DIGITS.sub("9", number.strip().upper())


def score_set(
    layer: MatchedLayer,
    members: tuple[Cand, ...],
    received: Decimal,
    *,
    foreign: bool,
    payment_date: date,
    customer_has_one_open_invoice: bool,
    quoted_shape_known: bool,
) -> ScoredSet:
    fit = classify(received, sum((m.balance for m in members), Decimal("0")), foreign=foreign)
    raw = LAYER_BASE_SCORE[layer.value] + AMOUNT_FIT_SCORE[_FIT_KEY.get(fit.tolerance, "OVER_SHORT")]
    earliest = min(m.due_date for m in members)
    if abs((earliest - payment_date).days) <= DUE_DATE_MODIFIER_WINDOW_DAYS:
        raw += MODIFIER_DUE_WITHIN_60D
    if len(members) == 1 and customer_has_one_open_invoice:
        raw += MODIFIER_SINGLE_OPEN_INVOICE
    if quoted_shape_known:
        raw += MODIFIER_KNOWN_REF_PATTERN
    return ScoredSet(members, received, fit, raw)


def candidate_sets(cands: list[Cand], received_for: callable, *, foreign: bool) -> tuple[list[tuple[Cand, ...]], bool]:
    """Every invoice alone, plus every combination whose total fits the amount (T1/T2).

    `received_for(members)` is the payment's INR value for that set, or None when the set cannot be
    judged (a foreign payment over invoices with different frozen rates). Returns (sets, too_many):
    with more open invoices than MAX_SUBSET_INVOICES only single invoices are tried.
    """
    sets: list[tuple[Cand, ...]] = [(c,) for c in cands]
    too_many = len(cands) > MAX_SUBSET_INVOICES
    if not too_many:
        for size in range(2, len(cands) + 1):
            for combo in itertools.combinations(cands, size):
                received = received_for(combo)
                if received is None:
                    continue
                fit = classify(received, sum((m.balance for m in combo), Decimal("0")), foreign=foreign)
                if fit.tolerance in (Tolerance.T1, Tolerance.T2):
                    sets.append(combo)
    return [s for s in sets if received_for(s) is not None], too_many


def choose(scored: list[ScoredSet]) -> Choice:
    if not scored:
        return Choice(None, [], False)
    ranked = sorted(scored, key=lambda s: (-s.raw, len(s.members), s.label))
    best, rest = ranked[0], ranked[1:]
    tied = [s for s in rest if best.raw - s.raw <= TIE_MARGIN]
    return Choice(best, tied, auto=not tied and best.raw >= CONFIDENCE_AUTO_APPLY)
