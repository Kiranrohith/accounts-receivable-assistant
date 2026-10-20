"""Which credit notes pay an invoice's leftover balance (C7, C18). Pure logic, no I/O.

A spend happens only on an INSTRUCTION in the remittance:
  SPECIFIC  the advice quotes a note number: that note is used (as much as it has)
  GENERIC   the advice says "use / adjust our credit" with no number: best fit over the customer's
            eligible notes (Open / PartiallyUsed, something left):
              1. the smallest remaining that COVERS the balance, else
              2. the largest remaining first, CHAINING notes (one spend each) until the balance is
                 filled or the notes run out;
              ties by created_at, then note number: deterministic.
Without an instruction nothing is spent (the caller only mentions the credit as a hint).
A partial use leaves the rest on the same note.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Credit:
    note_id: uuid.UUID
    number: str
    remaining: Decimal
    created_at: datetime


@dataclass(frozen=True)
class Spend:
    note_id: uuid.UUID
    note_number: str
    invoice_id: uuid.UUID
    amount: Decimal


def best_fit(balance: Decimal, remaining: dict[uuid.UUID, Decimal], notes: list[Credit]) -> list[tuple[Credit, Decimal]]:
    """The notes (and how much of each) that pay `balance`, per the generic rule."""
    live = [n for n in notes if remaining[n.note_id] > 0]
    covering = [n for n in live if remaining[n.note_id] >= balance]
    if covering:
        note = min(covering, key=lambda n: (remaining[n.note_id], n.created_at, n.number))
        return [(note, balance)]
    chosen: list[tuple[Credit, Decimal]] = []
    left = balance
    for note in sorted(live, key=lambda n: (-remaining[n.note_id], n.created_at, n.number)):
        if left <= 0:
            break
        take = min(remaining[note.note_id], left)
        chosen.append((note, take))
        left -= take
    return chosen


def plan_spends(
    leftovers: list[tuple[uuid.UUID, Decimal]],
    notes: list[Credit],
    *,
    specific: Credit | None = None,
) -> list[Spend]:
    """Spend notes against each invoice's leftover balance, oldest invoice first.

    `notes` are the customer's eligible notes; `specific` is the one note the advice quoted.
    """
    remaining = {n.note_id: n.remaining for n in notes}
    if specific is not None:
        remaining.setdefault(specific.note_id, specific.remaining)
    spends: list[Spend] = []
    for invoice_id, balance in leftovers:
        if balance <= 0:
            continue
        if specific is not None:
            picks = [(specific, min(remaining[specific.note_id], balance))]
        else:
            picks = best_fit(balance, remaining, notes)
        for note, amount in picks:
            if amount <= 0:
                continue
            remaining[note.note_id] -= amount
            spends.append(Spend(note.note_id, note.number, invoice_id, amount))
    return spends


def note_status_after(remaining: Decimal, amount: Decimal) -> str:
    """Open -> PartiallyUsed -> Closed (at exactly zero)."""
    if remaining <= _ZERO:
        return "Closed"
    return "Open" if remaining == amount else "PartiallyUsed"
