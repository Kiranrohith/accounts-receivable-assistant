"""The text of a reminder: the three tiers render, the two changing figures stay markers until the moment they are shown or
sent, an edit keeps them, and every number reads in the currency the customer was invoiced in."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.services.reminders import drafts

INVOICE = SimpleNamespace(
    invoice_number="INV-1040", invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
    invoice_currency="INR", fx_rate_invoice=None, collectible_balance=Decimal("115758.00"),
)
CUSTOMER = SimpleNamespace(contact="Anita", customer_name="Zenith Softlabs Pvt Ltd")


@pytest.mark.parametrize(("tier", "subject"), [(1, "Payment reminder"), (15, "Second reminder"), (30, "Final reminder")])
def test_each_tier_renders_with_its_own_tone(tier, subject):
    text = drafts.compose(INVOICE, CUSTOMER, tier)
    head, body = drafts.split(text)
    assert head.startswith(subject) and "INV-1040" in head
    assert "Dear Anita" in body and "INV-1040" in body and "01 Oct 2026" in body


def test_the_figures_that_change_are_markers_until_shown():
    text = drafts.compose(INVOICE, CUSTOMER, 15)
    assert drafts.BALANCE in text and drafts.DAYS in text
    shown = drafts.fill(text, drafts.figures(INVOICE, date(2026, 10, 17)))
    assert "[[" not in shown and "INR 1,15,758.00" in shown and "16 days" in shown


def test_the_same_draft_shows_a_new_count_and_balance_later():
    text = drafts.compose(INVOICE, CUSTOMER, 15)
    later = SimpleNamespace(**{**INVOICE.__dict__, "collectible_balance": Decimal("15758.00")})
    shown = drafts.fill(text, drafts.figures(later, date(2026, 10, 20)))
    assert "INR 15,758.00" in shown and "19 days" in shown


def test_a_foreign_invoice_quotes_the_balance_in_its_own_currency():
    usd = SimpleNamespace(**{**INVOICE.__dict__, "invoice_currency": "USD", "fx_rate_invoice": Decimal("83.0"), "collectible_balance": Decimal("8300.00")})
    assert drafts.balance_text(usd) == "USD 100.00"


def test_a_stored_draft_splits_into_subject_and_body_and_joins_back():
    stored = drafts.join("  Payment   reminder ", "Hello\n\nbody  ")
    assert stored == "Subject: Payment reminder\n\nHello\n\nbody\n"
    assert drafts.split(stored) == ("Payment reminder", "Hello\n\nbody\n")


def test_a_draft_without_a_subject_line_is_all_body():
    assert drafts.split("just text") == ("", "just text")


def test_an_edit_that_keeps_the_markers_still_gets_live_figures():
    edited = drafts.join("Quick reminder", "Hi, [[balance]] is open, [[days_past_due]] days late.")
    subject, body = drafts.split(edited)
    assert drafts.fill(body, {"balance": "INR 5.00", "days_past_due": "4"}).strip() == "Hi, INR 5.00 is open, 4 days late."


def test_the_reference_is_in_the_subject_once():
    rid = uuid.UUID("1a2b3c4d-0000-0000-0000-000000000000")
    assert drafts.reference_for(rid) == "REM-1A2B3C4D"
    once = drafts.with_reference("Payment reminder", rid)
    assert once == "Payment reminder [REM-1A2B3C4D]" and drafts.with_reference(once, rid) == once
