"""Amounts frozen on an invoice at creation. Pure arithmetic."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from constants.constants import TDS_RATE

_CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FrozenAmounts:
    amount_inr: Decimal
    amount_foreign: Decimal | None  # the invoice total in its own currency; None for INR
    fx_rate: Decimal
    tds_amount: Decimal
    collectible_total: Decimal


def freeze_amounts(
    total: Decimal, *, rate: Decimal, foreign: bool, deducts_tds: bool, is_debit_note: bool
) -> FrozenAmounts:
    """`total` is in the invoice's own currency; `rate` is INR per one unit of it (1 for INR).

    TDS is TDS_RATE of the gross INR amount, taken off what we expect to collect. Debit
    notes and customers that do not deduct TDS collect the full amount.
    """
    gross_own = money(total)
    gross = money(gross_own * rate)
    tds = money(gross * TDS_RATE) if deducts_tds and not is_debit_note else Decimal("0.00")
    return FrozenAmounts(
        amount_inr=gross,
        amount_foreign=gross_own if foreign else None,
        fx_rate=rate,
        tds_amount=tds,
        collectible_total=gross - tds,
    )


def freeze_inr_amounts(total: Decimal, *, deducts_tds: bool, is_debit_note: bool) -> FrozenAmounts:
    return freeze_amounts(
        total, rate=Decimal("1"), foreign=False, deducts_tds=deducts_tds, is_debit_note=is_debit_note
    )


def format_money(value: Decimal, currency: str = "INR") -> str:
    """"INR 1,15,758.00": two decimals, Indian grouping (last three digits, then pairs) for INR, thousands otherwise."""
    value = money(value)
    sign = "-" if value < 0 else ""
    whole, _, cents = f"{abs(value):.2f}".partition(".")
    if currency == "INR" and len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head, *pairs, tail] if head else [*pairs, tail])
    elif len(whole) > 3:
        whole = f"{int(whole):,}"
    return f"{currency} {sign}{whole}.{cents}"
