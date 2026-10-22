import asyncio
from datetime import date
from decimal import Decimal

import pytest

from core.services.disputes import clarification as c
from core.services.disputes import outbound
from core.services.mail import smtp
from data.models.postgres.enums import DisputeCategory

FACTS = c.Facts(
    reference="DSP-1A2B3C4D",
    company="Genworx Technologies Pvt Ltd",
    customer_name="Zenith Softlabs Pvt Ltd",
    invoice_number="INV-2026-001",
    invoice_date=date(2026, 10, 1),
    due_date=date(2026, 10, 31),
    amounts={"Invoice total (INR)": Decimal("50000.00"), "Received so far (INR)": Decimal("20000.00")},
    po_number="PO/77",
)


def test_text_made_only_of_our_facts_is_fully_grounded():
    text = (
        "Hello, about invoice INV-2026-001 dated 2026-10-01 for INR 50,000.00 against PO/77, due 31 October 2026. "
        "We have received Rs. 20,000.00 so far. We will reply within 2 working days. Ref DSP-1A2B3C4D."
    )
    g = c.check_grounding(text, FACTS)
    assert g.grounded and g.claims >= 6 and g.ratio == 1.0


def test_an_invented_invoice_number_amount_or_date_is_caught():
    g = c.check_grounding("Invoice INV-2026-999 for INR 55,000.00, due 5 November 2026.", FACTS)
    assert not g.grounded
    kinds = " | ".join(g.ungrounded)
    assert "INV-2026-999" in kinds and "55,000.00" in kinds and "5 November 2026" in kinds
    assert g.ratio == 0.0


def test_one_bad_claim_among_good_ones_lowers_the_ratio():
    g = c.check_grounding("Invoice INV-2026-001 for INR 50,000.00 and another for INR 1,234.00.", FACTS)
    assert g.ungrounded == ["amount INR 1,234.00"] and g.claims == 3 and round(g.ratio, 2) == 0.67


@pytest.mark.parametrize(
    "text",
    [
        "Please reply within 2 working days.",
        "We will follow-up by e-mail and send a 10-day update.",
        "Hello, thank you for writing.",
    ],
)
def test_ordinary_words_and_small_numbers_are_not_claims(text):
    g = c.check_grounding(text, FACTS)
    assert g.claims == 0 and g.grounded


def test_a_number_next_to_a_word_that_is_not_a_month_is_not_a_date():
    f = c.Facts(reference="DSP-1", invoice_number="D4-1", invoice_date=date(2026, 10, 1), amounts={"x": Decimal("1000")})
    g = c.check_grounding("About invoice D4-1 dated 2026-10-01 for INR 1,000.00, due in 1 week of 2026.", f)
    assert g.grounded and g.claims == 3


def test_a_bare_large_amount_is_a_claim_too():
    assert c.check_grounding("The balance is 30,000.00 now.", FACTS).ungrounded == ["amount 30,000.00"]
    assert c.check_grounding("The invoice total is 50,000.00 only.", FACTS).grounded


def test_dates_in_other_formats_are_read():
    assert c.check_grounding("Issued on 01/10/2026.", FACTS).grounded
    assert c.check_grounding("Issued on October 1, 2026.", FACTS).grounded
    assert c.check_grounding("Issued on 02/10/2026.", FACTS).ungrounded == ["date 02/10/2026"]


def test_confidence_is_the_models_capped_by_the_share_that_checked_out():
    half = c.Grounding(claims=4, ungrounded=["a", "b"])
    assert c.final_confidence(95, half) == 50
    assert c.final_confidence(40, half) == 40
    assert c.final_confidence(95, c.Grounding(0, [])) == 95


@pytest.mark.parametrize(
    ("category", "confidence", "ungrounded", "first", "expected"),
    [
        (DisputeCategory.QUALITY, 85, [], True, True),
        (DisputeCategory.QUALITY, 84, [], True, False),  # one under the bar
        (DisputeCategory.QUALITY, 95, ["number X"], True, False),  # anything ungrounded blocks it
        (DisputeCategory.OTHER, 99, [], True, False),  # Other is always a human's call
        (None, 99, [], True, False),
        (DisputeCategory.QUALITY, 99, [], False, False),  # only the FIRST draft is ever automatic
    ],
)
def test_the_auto_send_gate(category, confidence, ungrounded, first, expected):
    grounding = c.Grounding(3, ungrounded)
    assert c.auto_send_allowed(category=category, confidence=confidence, grounding=grounding, first_draft=first) is expected


def test_subject_travels_on_the_first_line_of_the_body():
    body = outbound.compose("  Your   complaint ", "Hello,\n\nThanks.\n")
    assert body == "Subject: Your complaint\n\nHello,\n\nThanks."
    assert outbound.split(body) == ("Your complaint", "Hello,\n\nThanks.")
    assert outbound.split("no subject here") == (None, "no subject here")


def test_the_reference_is_added_once():
    assert outbound.subject_with_reference("Re: invoice", "DSP-1") == "Re: invoice [DSP-1]"
    assert outbound.subject_with_reference("Re: invoice [DSP-1]", "DSP-1") == "Re: invoice [DSP-1]"
    assert outbound.subject_with_reference("  ", "DSP-1") == "Your query [DSP-1]"


# ---- the mail sender: only the transport is retried -------------------------------------------------
def message():
    return smtp.build_message(to="a@b.com", subject="Hi", body="Body", sender="ar@genworx.local")


def test_the_message_carries_its_own_id_and_reply_headers():
    m = smtp.build_message(to="a@b.com", subject="Hi", body="x", sender="ar@genworx.local", in_reply_to="<1@x>")
    assert m["Message-ID"].endswith("@genworx.local>") and m["In-Reply-To"] == "<1@x>" and m["To"] == "a@b.com"


def test_a_transport_failure_is_retried_then_succeeds(monkeypatch):
    calls = []

    def flaky(host, port, msg, *credentials):
        calls.append(1)
        if len(calls) < 3:
            raise smtp.SmtpError("down", permanent=False)

    monkeypatch.setattr(smtp, "_send_once", flaky)
    monkeypatch.setattr(smtp, "retry_delays_seconds", (0, 0, 0))
    sent = asyncio.run(smtp.send(message()))
    assert len(calls) == 3 and sent.attempts == 3


def test_it_gives_up_after_the_retries_are_used(monkeypatch):
    calls = []
    monkeypatch.setattr(smtp, "_send_once", lambda *a: calls.append(1) or (_ for _ in ()).throw(smtp.SmtpError("down", permanent=False)))
    monkeypatch.setattr(smtp, "retry_delays_seconds", (0, 0, 0))
    with pytest.raises(smtp.SmtpError) as err:
        asyncio.run(smtp.send(message()))
    assert len(calls) == 4 and "4 attempt(s)" in err.value.message  # the first try plus three retries


def test_a_refusal_that_will_not_change_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(smtp, "_send_once", lambda *a: calls.append(1) or (_ for _ in ()).throw(smtp.SmtpError("bad rcpt", permanent=True)))
    monkeypatch.setattr(smtp, "retry_delays_seconds", (0, 0, 0))
    with pytest.raises(smtp.SmtpError) as err:
        asyncio.run(smtp.send(message()))
    assert len(calls) == 1 and err.value.permanent


def test_the_default_retry_delays_are_3_9_and_15_seconds():
    from constants.constants import SMTP_RETRY_DELAYS_SECONDS

    assert SMTP_RETRY_DELAYS_SECONDS == (3, 9, 15)
