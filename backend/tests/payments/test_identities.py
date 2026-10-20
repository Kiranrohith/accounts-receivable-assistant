import uuid
from decimal import Decimal

import pytest

from core.services.payments.identities import (
    invoice_violations,
    note_violations,
    payment_violations,
)
from data.models.postgres.enums import NoteStatus, PaymentState
from data.repositories.payments.payments_repo import InvoiceMoney, NoteMoney, PaymentMoney

D = Decimal
S = PaymentState


def payment(state, received="1000", allocated="1000", unapplied="0", held="0", credited="0", count=1):
    return PaymentMoney(
        uuid.uuid4(), state, None if received is None else D(received), D(unapplied), D(held),
        D(credited), D(allocated), count,
    )


def rules(violations) -> list[str]:
    return [v.rule for v in violations]


# ---- payments ----------------------------------------------------------------------------


def test_applied_payment_that_adds_up_is_sound():
    assert payment_violations(payment(S.APPLIED)) == []


def test_tolerance_residual_counts_toward_the_identity():
    # received 1000 = allocated 990 + residual 10 (the caller sums them): still sound
    assert payment_violations(payment(S.APPLIED, received="1000", allocated="1000")) == []


def test_credited_amount_is_part_of_the_identity():
    # 1200 received: 1000 applied, 200 turned into a credit note; Applied means finished
    assert payment_violations(payment(S.APPLIED, received="1200", allocated="1000", credited="200")) == []


def test_money_that_does_not_add_up_is_reported():
    found = payment_violations(payment(S.APPLIED, received="1000", allocated="900"))
    assert any("received =" in r for r in rules(found))


def test_applied_with_unapplied_money_is_not_finished():
    found = payment_violations(payment(S.APPLIED, received="1000", allocated="900", unapplied="100"))
    assert rules(found) == ["Applied is finished"]


def test_partially_applied_must_still_have_something_open():
    found = payment_violations(payment(S.PARTIALLY_APPLIED))
    assert rules(found) == ["PartiallyApplied still has something open"]
    assert payment_violations(payment(S.PARTIALLY_APPLIED, received="1000", allocated="600", unapplied="400")) == []


def test_unapplied_payment_has_no_allocations():
    ok = payment(S.UNAPPLIED, received="500", allocated="0", unapplied="500", count=0)
    assert payment_violations(ok) == []
    bad = payment(S.UNAPPLIED, received="500", allocated="200", unapplied="300", count=1)
    assert rules(payment_violations(bad)) == ["Unapplied has no allocations"]


def test_enforced_state_needs_an_inr_amount():
    assert payment_violations(payment(S.APPLIED, received=None))[0].rule.startswith("an enforced state")


@pytest.mark.parametrize("state", [S.RECEIVED, S.READING, S.MATCHING])
def test_transit_states_are_exempt(state):
    assert payment_violations(payment(state, received=None, allocated="0", count=0)) == []
    assert payment_violations(payment(state, received="999", allocated="1", count=0)) == []


@pytest.mark.parametrize("state", [S.NEEDS_REVIEW, S.REJECTED, S.FAILED])
def test_awaiting_a_human_is_not_unapplied_cash(state):
    clean = payment(state, received="1000", allocated="0", count=0)
    assert payment_violations(clean) == []
    dirty = payment(state, received="1000", allocated="0", unapplied="1000", count=0)
    assert "every bucket at zero" in rules(payment_violations(dirty))[0]
    with_allocation = payment(state, received="1000", allocated="1000", count=1)
    assert "no Active allocations" in rules(payment_violations(with_allocation))[0]


def test_held_is_narrow():
    assert payment_violations(payment(S.HELD, received="500", allocated="0", held="500", count=0)) == []
    partial = payment(S.HELD, received="500", allocated="200", held="300", count=1)
    assert rules(payment_violations(partial)) == ["Held is held in its entirety"]


# ---- invoices ----------------------------------------------------------------------------


def invoice(status, total="1000", paid="0", allocated=None):
    return InvoiceMoney(uuid.uuid4(), "INV-1", status, D(total), D(paid), D(paid if allocated is None else allocated))


def test_invoice_status_follows_the_money():
    assert invoice_violations(invoice("Open")) == []
    assert invoice_violations(invoice("PartlyPaid", paid="400")) == []
    assert invoice_violations(invoice("Paid", paid="1000")) == []


def test_invoice_amount_paid_must_equal_active_allocations():
    found = invoice_violations(invoice("PartlyPaid", paid="400", allocated="300"))
    assert rules(found) == ["amount_paid = sum of Active allocations"]


@pytest.mark.parametrize(
    ("status", "paid"),
    [("Open", "10"), ("PartlyPaid", "0"), ("PartlyPaid", "1000"), ("Paid", "999")],
)
def test_invoice_status_that_contradicts_the_money(status, paid):
    assert invoice_violations(invoice(status, paid=paid))


# ---- notes -------------------------------------------------------------------------------


def note(status, amount="500", remaining="500", spent="0"):
    return NoteMoney(uuid.uuid4(), "CN-2026-000001", status, D(amount), D(remaining), D(spent))


def test_note_statuses_follow_the_remaining_amount():
    assert note_violations(note(NoteStatus.OPEN)) == []
    assert note_violations(note(NoteStatus.PARTIALLY_USED, remaining="200", spent="300")) == []
    assert note_violations(note(NoteStatus.CLOSED, remaining="0", spent="500")) == []


def test_note_remaining_must_match_what_was_spent():
    found = note_violations(note(NoteStatus.PARTIALLY_USED, remaining="200", spent="100"))
    assert rules(found)[0].startswith("remaining =")


def test_voided_note_skips_the_status_check():
    assert note_violations(note(NoteStatus.VOIDED)) == []
    assert note_violations(note(NoteStatus.OPEN, remaining="500", spent="0")) == []
    assert rules(note_violations(note(NoteStatus.CLOSED))) == ["status follows the remaining amount"]
