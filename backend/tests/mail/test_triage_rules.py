"""The triage rules that need no model: what counts as context, the no-model verdicts, and how a model verdict becomes
a route. The model itself is measured separately against the seed kit (tests/kit/triage_probe.py)."""

import pytest

from core.services.mail.attachments import Attachment, MailRef, qualify
from core.services.mail.classification import Route, decide, describe_attachments, is_bare, pre_decide
from core.services.mail.context import MailContext, candidate_numbers
from core.services.mail.parse import ParsedMail
from data.models.postgres.enums import EmailClassification as C
from schemas.mail.triage import MailClass, MailClassification

PDF = Attachment("advice.pdf", b"%PDF-1.4\n" + b"x" * 100, "application/pdf")
MAIL = MailRef("m1")


def mail(subject="Payment INV-1001", body="Paid today.", attachments=()) -> ParsedMail:
    return ParsedMail(None, "ap@client.in", None, (), subject, None, None, (), body, list(attachments))


def verdict(cls: MailClass, confidence=95, mixed=False) -> MailClassification:
    return MailClassification(mail_class=cls, confidence=confidence, mixed_po_and_payment=mixed, reason="because")


KNOWN = MailContext(sender_known=True, invoice_numbers=(), customer_named=False)
QUOTES = MailContext(sender_known=False, invoice_numbers=("INV-1001",), customer_named=False)
NOTHING = MailContext(sender_known=False, invoice_numbers=(), customer_named=False)


# ---- context ------------------------------------------------------------------------------------------
def test_invoice_number_candidates_are_normalized_and_need_a_prefix():
    found = candidate_numbers("Payment for INV-1176 and inv 1157, DN2001 / INV/2026/0042", "ref 998877 only")
    assert found[:3] == ["INV1176", "INV1157", "DN2001"] and "INV20260042" in found
    assert "998877" not in found  # a bare number is not an invoice number


def test_candidates_repeat_once_and_are_capped():
    assert candidate_numbers("INV-1 INV-1001 inv 1001", "INV1001") == ["INV1001"]
    assert len(candidate_numbers(" ".join(f"AB{n}00" for n in range(500)))) == 60


def test_context_is_any_of_known_sender_quoted_invoice_named_customer_or_payment_wording():
    assert KNOWN.has_context and QUOTES.has_context and MailContext(False, (), True).has_context
    assert MailContext(False, (), False, (), True).has_context
    assert not NOTHING.has_context


def test_payment_wording_is_recognised_in_text_and_attachment_names():
    from core.services.mail.context import _PAYMENT_WORDS as words

    for text in ("payment done", "pls check, PAID today", "NEFT_Advice_123.pdf", "remittance details", "UTR 998877", "MT103 copy", "funds transferred"):
        assert words.search(text), text
    for text in ("Weekly newsletter", "Meeting on Friday at 4", "Neutral party notes", "Supervisor", "Quarterly newsletter and meeting notes"):
        assert not words.search(text), text


# ---- rules with no model ------------------------------------------------------------------------------
def test_an_unknown_sender_with_no_context_is_general_without_asking_the_model():
    d = pre_decide(mail("Weekly newsletter", "Buy now"), NOTHING, qualify([], MAIL))
    assert d.route is Route.IGNORE and d.mail_class is C.GENERAL


def test_an_unknown_sender_who_quotes_our_invoice_goes_on_to_the_model():
    assert pre_decide(mail(), QUOTES, qualify([], MAIL)) is None


def test_a_known_sender_goes_on_to_the_model():
    assert pre_decide(mail(), KNOWN, qualify([], MAIL)) is None


def test_a_bare_attachment_from_a_known_customer_is_read_as_a_payment_candidate():
    q = qualify([PDF], MAIL)
    d = pre_decide(mail("", "", [PDF]), KNOWN, q)
    assert d.route is Route.AUTO and d.mail_class is C.PAYMENT and d.confidence == 80


def test_a_bare_attachment_from_an_unknown_sender_is_ignored():
    d = pre_decide(mail("", "", [PDF]), NOTHING, qualify([PDF], MAIL))
    assert d.route is Route.IGNORE


def test_bare_means_neither_subject_nor_text():
    assert is_bare(mail("", " ")) and not is_bare(mail("", "Please see attached")) and not is_bare(mail("Payment", ""))


# ---- verdict -> route ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("cls", "expected_class"),
    [(MailClass.PAYMENT, C.PAYMENT), (MailClass.DISPUTE, C.DISPUTE), (MailClass.BOTH, C.BOTH)],
)
def test_a_confident_payment_dispute_or_both_is_automatic(cls, expected_class):
    d = decide(verdict(cls), qualify([], MAIL))
    assert d.route is Route.AUTO and d.mail_class is expected_class


def test_general_is_ignored():
    assert decide(verdict(MailClass.GENERAL), qualify([], MAIL)).route is Route.IGNORE


@pytest.mark.parametrize(("confidence", "route"), [(75, Route.AUTO), (74, Route.HUMAN), (0, Route.HUMAN)])
def test_the_human_threshold_is_75_inclusive(confidence, route):
    assert decide(verdict(MailClass.PAYMENT, confidence), qualify([], MAIL)).route is route


def test_a_low_confidence_general_goes_to_a_person_not_the_ignored_list():
    assert decide(verdict(MailClass.GENERAL, 60), qualify([], MAIL)).route is Route.HUMAN


def test_a_po_and_a_payment_advice_in_one_mail_goes_to_a_person_even_when_sure():
    d = decide(verdict(MailClass.PURCHASE_ORDER, 99, mixed=True), qualify([PDF], MAIL))
    assert d.route is Route.HUMAN and "one mail" in d.reason and d.mail_class is C.PURCHASE_ORDER


def test_a_po_mail_with_a_usable_attachment_is_automatic_and_without_one_goes_to_a_person():
    assert decide(verdict(MailClass.PURCHASE_ORDER), qualify([PDF], MAIL)).route is Route.AUTO
    d = decide(verdict(MailClass.PURCHASE_ORDER), qualify([], MAIL))
    assert d.route is Route.HUMAN and "body-only" in d.reason


def test_a_po_mail_whose_only_attachment_is_a_logo_still_counts_as_having_none():
    logo = Attachment("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 30, "image/png", inline=True)
    assert decide(verdict(MailClass.PURCHASE_ORDER), qualify([logo], MAIL)).route is Route.HUMAN


def test_a_payment_mail_without_any_attachment_is_still_automatic_the_body_is_the_advice():
    assert decide(verdict(MailClass.PAYMENT), qualify([], MAIL)).route is Route.AUTO


def test_the_attachment_description_tells_the_model_what_was_done_with_each_file():
    logo = Attachment("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 30, "image/png", inline=True)
    xls = Attachment("a.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")
    text = describe_attachments(mail(attachments=[PDF, logo, xls]), qualify([PDF, logo, xls], MAIL))
    assert "advice.pdf" in text and "qualify" in text and "skip: inline image" in text and "reject: file type" in text
    assert describe_attachments(mail(), qualify([], MAIL)) == "none"
