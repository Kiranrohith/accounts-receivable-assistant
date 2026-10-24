"""The customer's GSTIN when the reader missed it: taken from the document's own text (the one GSTIN that is not ours),
or filled in from the matched customer. Nothing is guessed: two candidates mean none is chosen."""

import asyncio

import pytest

from core.services.customers import gstin as g
from core.services.invoices import extraction as invoice_extraction
from core.services.purchase_orders import extraction as po_extraction
from schemas.invoices.extraction import ExtractedInvoice
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

OURS = "33AAHCG2210A1Z7"
THEIRS = "29ABCDE1234F1Z5"
THIRD = "07AAACU9999A1Z1"

INVOICE_TEXT = f"""
Genworx Technologies Pvt Ltd, Chennai
GSTIN: {OURS}
Bill To: Zenith Softlabs Pvt Ltd, Bengaluru
GST No. {THEIRS}
Total INR 1000
"""


def test_every_distinct_gstin_in_a_text_is_found_in_order():
    assert g.gstins_in_text(INVOICE_TEXT) == [OURS, THEIRS]
    assert g.gstins_in_text(f"{THEIRS} and again {THEIRS.lower()}") == [THEIRS]
    assert g.gstins_in_text("no tax id here, only 12345 and ABC") == []


def test_a_gstin_glued_to_other_letters_or_digits_is_not_one():
    assert g.gstins_in_text(f"X{THEIRS}") == []
    assert g.gstins_in_text(f"{THEIRS}9") == []
    assert g.gstins_in_text(f"({THEIRS}),") == [THEIRS]


@pytest.mark.parametrize("given", [None, "", "  ", OURS, OURS.lower(), "29ABCDE1234F1Z"])  # nothing, ours, or not a GSTIN
def test_when_the_readers_answer_is_unusable_the_one_other_gstin_is_taken(given):
    assert g.recover(given, INVOICE_TEXT, OURS) == THEIRS


def test_a_usable_answer_is_kept_even_if_the_text_shows_another():
    assert g.recover(THIRD, INVOICE_TEXT, OURS) == THIRD
    assert g.recover(f" {THEIRS.lower()} ", INVOICE_TEXT, OURS) == THEIRS


def test_two_other_gstins_are_ambiguous_so_none_is_chosen():
    assert g.recover(None, INVOICE_TEXT + f"\nShip To GSTIN {THIRD}", OURS) is None


def test_nothing_is_taken_when_the_document_shows_only_ours_or_nothing():
    assert g.recover(None, f"GSTIN {OURS}", OURS) is None
    assert g.recover(None, "no ids", OURS) is None


def test_without_our_gstin_configured_a_single_printed_gstin_is_used():
    assert g.recover(None, f"Bill to GSTIN {THEIRS}", None) == THEIRS


def test_the_extraction_gets_the_recovered_gstin_and_a_confidence_for_it():
    inv = ExtractedInvoice(customer_gstin=OURS, customer_name="Zenith")
    out = g.with_gstin_from_text(inv, INVOICE_TEXT, OURS)
    assert out.customer_gstin == THEIRS and out.confidence.customer_gstin == g.RECOVERED_CONFIDENCE
    assert out.customer_name == "Zenith" and inv.customer_gstin == OURS  # the original is untouched


def test_an_extraction_that_needs_no_help_is_returned_unchanged():
    inv = ExtractedInvoice(customer_gstin=THEIRS)
    assert g.with_gstin_from_text(inv, INVOICE_TEXT, OURS) is inv
    po = ExtractedPurchaseOrder(customer_gstin=None)
    assert g.with_gstin_from_text(po, "nothing printed", OURS) is po


def test_it_works_the_same_for_a_purchase_order():
    po = ExtractedPurchaseOrder(customer_gstin=None)
    out = g.with_gstin_from_text(po, INVOICE_TEXT, OURS)
    assert out.customer_gstin == THEIRS


def test_the_matched_customers_own_gstin_fills_an_empty_field_only():
    inv = ExtractedInvoice(customer_gstin=None)
    assert g.with_customer_gstin(inv, THEIRS).customer_gstin == THEIRS
    assert g.with_customer_gstin(ExtractedInvoice(customer_gstin="  "), THEIRS).customer_gstin == THEIRS
    printed = ExtractedInvoice(customer_gstin=THIRD)
    assert g.with_customer_gstin(printed, THEIRS) is printed  # a printed value is never replaced
    assert g.with_customer_gstin(inv, None) is inv


def _settings(monkeypatch, module):
    monkeypatch.setattr(module.get_settings(), "our_gstin", OURS)
    monkeypatch.setattr(module.get_settings(), "our_company_name", "Genworx Technologies Pvt Ltd")


def test_invoice_extraction_replaces_our_own_gstin_returned_by_the_reader(monkeypatch):
    _settings(monkeypatch, invoice_extraction)

    async def reader(schema, messages):
        return ExtractedInvoice(customer_name="Zenith", customer_gstin=OURS), "stub", 1

    monkeypatch.setattr(invoice_extraction, "run_structured_extraction", reader)
    out = asyncio.run(invoice_extraction.extract_invoice(INVOICE_TEXT))
    assert out.invoice.customer_gstin == THEIRS


def test_po_extraction_fills_a_gstin_the_reader_left_empty(monkeypatch):
    _settings(monkeypatch, po_extraction)

    async def reader(schema, messages):
        return ExtractedPurchaseOrder(customer_name="Zenith", customer_gstin=None), "stub", 1

    monkeypatch.setattr(po_extraction, "run_structured_extraction", reader)
    out = asyncio.run(po_extraction.extract_purchase_order(INVOICE_TEXT))
    assert out.purchase_order.customer_gstin == THEIRS


def test_the_prompts_tell_the_reader_to_return_the_other_gstin():
    from prompts.invoices.extraction import build_system_prompt as invoice_prompt
    from prompts.purchase_orders.extraction import build_system_prompt as po_prompt

    for prompt in (invoice_prompt("Genworx", OURS), po_prompt("Genworx", OURS)):
        assert "by elimination" in prompt and "OTHER" in prompt and OURS in prompt
        assert "only when the document shows no GSTIN other than" in prompt
