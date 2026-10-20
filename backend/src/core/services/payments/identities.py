"""verify_money_identities(): the money rules as code, run in tests and by scripts/verify_money.py.

  1. invoices.amount_paid = sum of Active allocations (payment- and note-funded) per invoice
  2. payments.amount_received_inr = sum of Active (allocated_amount + tolerance_residual)
     + unapplied_amount + held_amount + credited_amount, by payment state:
       enforced     Applied, PartiallyApplied, Unapplied, Held
       transit      Received, Reading, Matching (not checked: still being worked out)
       zero buckets NeedsReview, Rejected, Failed (awaiting a human is not unapplied cash)
  3. notes.remaining_amount = notes.amount - sum of Active note-funded allocations

The rules are pure functions over rows the repository reads, so they can be unit-tested.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres.enums import NoteStatus, PaymentState
from data.repositories.payments import payments_repo
from data.repositories.payments.payments_repo import InvoiceMoney, NoteMoney, PaymentMoney

_ZERO = Decimal("0")
_ENFORCED = frozenset(
    {PaymentState.APPLIED, PaymentState.PARTIALLY_APPLIED, PaymentState.UNAPPLIED, PaymentState.HELD}
)
_ZERO_BUCKETS = frozenset({PaymentState.NEEDS_REVIEW, PaymentState.REJECTED, PaymentState.FAILED})


@dataclass(frozen=True)
class Violation:
    entity: str  # invoice / payment / note
    entity_id: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.entity} {self.entity_id}: {self.rule} ({self.detail})"


def invoice_violations(row: InvoiceMoney) -> list[Violation]:
    found: list[Violation] = []

    def bad(rule: str, detail: str) -> None:
        found.append(Violation("invoice", row.invoice_number, rule, detail))

    if row.amount_paid != row.allocated:
        bad("amount_paid = sum of Active allocations", f"{row.amount_paid} != {row.allocated}")
    if row.status == "Open" and row.amount_paid != _ZERO:
        bad("Open invoice has nothing paid", f"amount_paid {row.amount_paid}")
    if row.status == "PartlyPaid" and not (_ZERO < row.amount_paid < row.collectible_total):
        bad("PartlyPaid is between nothing and everything", f"{row.amount_paid} of {row.collectible_total}")
    if row.status == "Paid" and row.amount_paid != row.collectible_total:
        bad("Paid invoice is fully paid", f"{row.amount_paid} of {row.collectible_total}")
    return found


def payment_violations(row: PaymentMoney) -> list[Violation]:
    found: list[Violation] = []

    def bad(rule: str, detail: str) -> None:
        found.append(Violation("payment", str(row.payment_id), rule, detail))

    state = row.state
    if state in _ENFORCED:
        if row.received_inr is None:
            bad("an enforced state has an INR amount", "amount_received_inr is NULL")
        else:
            total = row.allocated_plus_residual + row.unapplied + row.held + row.credited
            if row.received_inr != total:
                bad(
                    "received = allocations + residual + unapplied + held + credited",
                    f"{row.received_inr} != {total}",
                )
        if state is PaymentState.APPLIED and (row.unapplied != _ZERO or row.held != _ZERO):
            bad("Applied is finished", f"unapplied {row.unapplied}, held {row.held}")
        if state is PaymentState.PARTIALLY_APPLIED and row.unapplied == _ZERO and row.held == _ZERO:
            bad("PartiallyApplied still has something open", "unapplied and held are both 0")
        if state is PaymentState.UNAPPLIED and row.active_allocations:
            bad("Unapplied has no allocations", f"{row.active_allocations} active")
        if state is PaymentState.HELD and (row.active_allocations or row.unapplied != _ZERO):
            bad("Held is held in its entirety", "has allocations or unapplied money")
    elif state in _ZERO_BUCKETS:
        if row.unapplied != _ZERO or row.held != _ZERO or row.credited != _ZERO:
            bad(
                f"{state.value} has every bucket at zero",
                f"unapplied {row.unapplied}, held {row.held}, credited {row.credited}",
            )
        if row.active_allocations:
            bad(f"{state.value} has no Active allocations", f"{row.active_allocations} active")
    return found


def note_violations(row: NoteMoney) -> list[Violation]:
    found: list[Violation] = []

    def bad(rule: str, detail: str) -> None:
        found.append(Violation("note", row.note_number, rule, detail))

    if row.remaining != row.amount - row.spent:
        bad("remaining = amount - note-funded allocations", f"{row.remaining} != {row.amount} - {row.spent}")
    if row.status is not NoteStatus.VOIDED:
        expected = (
            NoteStatus.CLOSED if row.remaining == _ZERO
            else NoteStatus.OPEN if row.remaining == row.amount
            else NoteStatus.PARTIALLY_USED
        )
        if row.status is not expected:
            bad("status follows the remaining amount", f"{row.status.value}, expected {expected.value}")
    return found


async def verify_money_identities(db: AsyncSession) -> list[Violation]:
    """Walk every invoice, payment and note; return every broken rule (empty = all sound)."""
    found: list[Violation] = []
    for invoice in await payments_repo.invoice_money(db):
        found += invoice_violations(invoice)
    for payment in await payments_repo.payment_money(db):
        found += payment_violations(payment)
    for note in await payments_repo.note_money(db):
        found += note_violations(note)
    return found
