"""What every outgoing mail shares: the address check, attachments on a message, and the Delivery verdicts."""

from decimal import Decimal

import pytest

from core.services.invoices.amounts import format_money
from core.services.mail import sending, smtp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ap@zenith.test", "ap@zenith.test"),
        ("  ap@zenith.test  ", "ap@zenith.test"),
        ("", None),
        (None, None),
        ("not-an-address", None),
        ("a@b", None),
        ("a@b.com, c@d.com", None),
        ("Anita <a@b.com>", None),
    ],
)
def test_an_address_is_one_plain_address_or_nothing(value, expected):
    assert sending.clean_address(value) == expected


def test_a_message_can_carry_attachments():
    mail = smtp.build_message(
        to="a@b.com", subject="s", body="hello", sender="ar@x.test",
        attachments=[("CN-1.pdf", b"%PDF-1.4 data", "application/pdf")],
    )
    parts = [p for p in mail.iter_attachments()]
    assert len(parts) == 1 and parts[0].get_filename() == "CN-1.pdf" and parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4 data"


def test_a_message_without_attachments_is_plain_text():
    mail = smtp.build_message(to="a@b.com", subject="s", body="hello", sender="ar@x.test")
    assert not list(mail.iter_attachments()) and mail.get_content_type() == "text/plain"


def test_a_delivery_says_whether_a_retry_could_help():
    ok = sending.Delivery("a@b.com", sent=smtp.Sent("<id@x>", 1))
    refused = sending.Delivery("a@b.com", error=smtp.SmtpError("not on the allowlist", permanent=True))
    down = sending.Delivery("a@b.com", error=smtp.SmtpError("could not be reached", permanent=False))
    assert ok.ok and not ok.permanent and ok.reason == ""
    assert not refused.ok and refused.permanent and refused.reason == "not on the allowlist"
    assert not down.ok and not down.permanent


@pytest.mark.parametrize(
    ("amount", "currency", "text"),
    [
        (Decimal("115758"), "INR", "INR 1,15,758.00"),
        (Decimal("999"), "INR", "INR 999.00"),
        (Decimal("1234.5"), "INR", "INR 1,234.50"),
        (Decimal("12345678.9"), "INR", "INR 1,23,45,678.90"),
        (Decimal("100000"), "INR", "INR 1,00,000.00"),
        (Decimal("1234567.891"), "USD", "USD 1,234,567.89"),
        (Decimal("-5000"), "INR", "INR -5,000.00"),
    ],
)
def test_money_reads_with_indian_grouping_for_rupees(amount, currency, text):
    assert format_money(amount, currency) == text
