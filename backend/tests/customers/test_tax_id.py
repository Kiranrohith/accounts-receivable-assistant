"""A customer's tax ID: the one normalization rule, the shape of each type, and the safety nets on a document (the reader's
value checked against its type, the one other id in the text, the matched customer's own). Everything is exact: nothing
is guessed, and two candidates mean none is chosen."""

import asyncio

import pytest

from core.services.customers import tax_id as t
from core.services.invoices import extraction as invoice_extraction
from core.services.purchase_orders import extraction as po_extraction
from data.models.postgres import enums as db_enums
from data.repositories.customers.tax_ids import normalize_tax_id
from schemas.customers.tax_id import TaxIdType, from_legacy
from schemas.invoices.extraction import ExtractedInvoice
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

OURS = "33AAHCG2210A1Z7"
THEIRS = "29ABCDE1234F1Z5"
THIRD = "07AAACU9999A1Z1"
VAT = "GB 284 7731 09"
TRN = "100123456700003"

INVOICE_TEXT = f"""
Genworx Technologies Pvt Ltd, Chennai
GSTIN: {OURS}
Bill To: Zenith Softlabs Pvt Ltd, Bengaluru
GST No. {THEIRS}
Total INR 1000
"""
UK_TEXT = f"""
Genworx Technologies Pvt Ltd, Chennai   GSTIN: {OURS}
Bill To: Brightwave Digital Ltd, London
VAT Reg No: {VAT}     Date: 05/10/2026
"""


# ------------------------------------------------------------------------------------------ normalization
@pytest.mark.parametrize("written", ["GB 284 7731 09", "gb2847731 09", "GB-284.7731/09", " GB284773109 ", "Gb 284\t7731  09"])
def test_every_spelling_of_a_tax_id_is_the_same(written):
    assert normalize_tax_id(written) == "GB284773109"


def test_what_is_not_a_separator_is_kept():
    assert normalize_tax_id("94-3456789") == "943456789"
    assert normalize_tax_id("29abcde1234f1z5") == THEIRS
    assert normalize_tax_id("NL857321947B01") == "NL857321947B01"
    assert normalize_tax_id("GB28477/3109") != normalize_tax_id("GB284773119")  # nothing fuzzy: a digit apart is another id


@pytest.mark.parametrize("empty", [None, "", "   ", "- . / -"])
def test_nothing_normalizes_to_empty(empty):
    assert normalize_tax_id(empty) == ""


# ------------------------------------------------------------------------------------------ shapes
@pytest.mark.parametrize(
    "kind, value",
    [
        ("GSTIN", THEIRS), ("GSTIN", OURS), ("GSTIN", "27AABCS2468H1Z9"),
        ("VAT", "GB284773109"), ("VAT", "DE298345761"), ("VAT", "NL857321947B01"), ("VAT", "FR12834567901"), ("VAT", "IE6388047V"),
        ("VAT", "ATU12345678"), ("VAT", "GB12"), ("VAT", "XX" + "A1" * 6 + "9"),
        ("EIN", "943456789"), ("EIN", "000000000"),
        ("TRN", TRN), ("TRN", "000000000000000"),
        ("OTHER", "anything at all, 12 ab"), ("OTHER", "UEN 201934512K"),
    ],
)
def test_a_value_with_the_shape_of_its_type_passes(kind, value):
    assert t.has_shape(kind, normalize_tax_id(value))


@pytest.mark.parametrize(
    "kind, value",
    [
        ("GSTIN", "29ABCDE1234F1Z"), ("GSTIN", "29ABCDE1234F1Z55"), ("GSTIN", "29ABCDE1234F0Z5"), ("GSTIN", "GB284773109"),
        ("GSTIN", "29ABCDE1234F1X5"),  # 14th character must be Z
        ("VAT", "G284773109"), ("VAT", "12284773109"), ("VAT", "GB2"), ("VAT", "GB" + "1" * 14), ("VAT", "G!284773109"),
        ("EIN", "94345678"), ("EIN", "9434567890"), ("EIN", "94-345678A"),
        ("TRN", "10012345670000"), ("TRN", "1001234567000033"), ("TRN", "10012345670000A"),
    ],
)
def test_a_value_without_the_shape_of_its_type_fails(kind, value):
    assert not t.has_shape(kind, normalize_tax_id(value))


def test_the_shapes_never_overlap_so_a_missing_type_can_be_inferred():
    assert t.infer_type(THEIRS) == "GSTIN"
    assert t.infer_type("GB 284 7731 09") == "VAT"
    assert t.infer_type("94-3456789") == "EIN"
    assert t.infer_type(TRN) == "TRN"
    assert t.infer_type("12345") is None and t.infer_type(None) is None and t.infer_type("--") is None


def test_only_a_type_with_a_shape_may_find_a_customer():
    assert t.usable_for_matching("GSTIN", THEIRS) and t.usable_for_matching(TaxIdType.VAT, VAT)
    assert not t.usable_for_matching("OTHER", "UEN 201934512K")  # stored, never used to find a customer
    assert not t.usable_for_matching(None, THEIRS)
    assert not t.usable_for_matching("GSTIN", "29ABCDE1234F1Z") and not t.usable_for_matching("GSTIN", "  ")


def test_the_schema_and_database_enums_have_the_same_members():
    assert {m.name: m.value for m in TaxIdType} == {m.name: m.value for m in db_enums.TaxIdType}


# ------------------------------------------------------------------------------------------ the shape check
def test_a_value_that_fails_its_shape_is_left_out_with_a_low_confidence_and_a_reason():
    inv = ExtractedInvoice(tax_id_type="GSTIN", tax_id_value="29ABCDE1234F1Z", customer_name="Zenith")
    out, note = t.check_shape(inv)
    assert out.tax_id_value is None and out.confidence.tax_id == t.REJECTED_CONFIDENCE
    assert "29ABCDE1234F1Z" in note and "GSTIN" in note and "15 characters" in note
    assert out.customer_name == "Zenith" and inv.tax_id_value == "29ABCDE1234F1Z"  # the original is untouched


def test_a_value_with_its_shape_or_no_shape_is_left_alone():
    for kind, value in (("GSTIN", THEIRS), ("VAT", VAT), ("OTHER", "UEN 201934512K"), (None, "whatever"), ("TRN", TRN)):
        inv = ExtractedInvoice(tax_id_type=kind, tax_id_value=value)
        assert t.check_shape(inv) == (inv, None)
    empty = ExtractedInvoice()
    assert t.check_shape(empty) == (empty, None)


# ------------------------------------------------------------------------------------------ recovery from the text
@pytest.mark.parametrize("given", [None, "", "  ", OURS, OURS.lower()])  # nothing, or ours
def test_when_the_readers_answer_is_empty_or_ours_the_one_other_gstin_is_taken(given):
    inv = ExtractedInvoice(tax_id_type="GSTIN" if given else None, tax_id_value=given)
    assert t.recover(inv, INVOICE_TEXT, OURS) == ("GSTIN", THEIRS, "GSTIN")


def test_a_usable_answer_is_kept_even_if_the_text_shows_another():
    assert t.recover(ExtractedInvoice(tax_id_type="GSTIN", tax_id_value=THIRD), INVOICE_TEXT, OURS) is None
    assert t.recover(ExtractedInvoice(tax_id_type="GSTIN", tax_id_value=f" {THEIRS.lower()} "), INVOICE_TEXT, OURS) is None


def test_two_other_ids_are_ambiguous_so_none_is_chosen():
    inv = ExtractedInvoice()
    assert t.recover(inv, INVOICE_TEXT + f"\nShip To GSTIN {THIRD}", OURS) is None
    assert t.recover(inv, UK_TEXT + f"\nTRN: {TRN}", OURS) is None


def test_nothing_is_taken_when_the_document_shows_only_ours_or_nothing():
    assert t.recover(ExtractedInvoice(), f"GSTIN {OURS}", OURS) is None
    assert t.recover(ExtractedInvoice(), "no ids", OURS) is None


def test_a_gstin_glued_to_other_letters_or_digits_is_not_one():
    assert t.recover(ExtractedInvoice(), f"X{THEIRS}", OURS) is None
    assert t.recover(ExtractedInvoice(), f"{THEIRS}9", OURS) is None
    assert t.recover(ExtractedInvoice(), f"({THEIRS}),", OURS) == ("GSTIN", THEIRS, "GSTIN")


def test_without_our_gstin_configured_a_single_printed_gstin_is_used():
    assert t.recover(ExtractedInvoice(), f"Bill to GSTIN {THEIRS}", None) == ("GSTIN", THEIRS, "GSTIN")


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"VAT Reg No: {VAT}   Date: 05/10/2026", ("VAT", VAT)),
        ("VAT: GB284773109", ("VAT", "GB284773109")),
        ("USt-IdNr DE 298 345 761", ("VAT", "DE 298 345 761")),
        ("BTW NL857321947B01", ("VAT", "NL857321947B01")),
        ("TVA FR 12 834567901", ("VAT", "FR 12 834567901")),
        (f"TRN: {TRN}", ("TRN", TRN)),
        ("EIN 94-3456789", ("EIN", "94-3456789")),
        ("EIN: 943456789.", ("EIN", "943456789")),
    ],
)
def test_the_other_kinds_are_taken_only_from_beside_their_label(text, expected):
    found = t.recover(ExtractedInvoice(), "Bill To: Someone Ltd\n" + text.upper() if False else "Bill To: Someone Ltd\n" + text, OURS)
    assert found is not None and (found[0], found[1]) == expected


@pytest.mark.parametrize(
    "text",
    ["Invoice INVOICE2026 total 943456789", "ref 100123456700003", "Order GB284773109 shipped", "VAT 20% on 1000", "EIN: 12345", "TRN: 1001234567000"],
)
def test_a_bare_look_alike_number_is_not_an_id(text):
    assert t.recover(ExtractedInvoice(), text, OURS) is None


def test_the_extraction_gets_the_recovered_id_the_type_the_label_and_a_confidence():
    inv = ExtractedInvoice(tax_id_type="GSTIN", tax_id_value=OURS, customer_name="Zenith")
    out = t.with_tax_id_from_text(inv, INVOICE_TEXT, OURS)
    assert (out.tax_id_type, out.tax_id_value) == (TaxIdType.GSTIN, THEIRS) and out.confidence.tax_id == t.RECOVERED_CONFIDENCE
    uk = t.with_tax_id_from_text(ExtractedInvoice(), UK_TEXT, OURS)
    assert (uk.tax_id_type, uk.tax_id_value, uk.tax_id_raw_label) == (TaxIdType.VAT, VAT, "VAT")
    assert out.customer_name == "Zenith" and inv.tax_id_value == OURS  # the original is untouched


def test_an_extraction_that_needs_no_help_is_returned_unchanged():
    inv = ExtractedInvoice(tax_id_type="GSTIN", tax_id_value=THEIRS)
    assert t.with_tax_id_from_text(inv, INVOICE_TEXT, OURS) is inv
    po = ExtractedPurchaseOrder()
    assert t.with_tax_id_from_text(po, "nothing printed", OURS) is po


def test_it_works_the_same_for_a_purchase_order():
    out = t.with_tax_id_from_text(ExtractedPurchaseOrder(), INVOICE_TEXT, OURS)
    assert (out.tax_id_type, out.tax_id_value) == (TaxIdType.GSTIN, THEIRS)


def test_the_check_then_the_recovery_run_in_that_order():
    bad = ExtractedInvoice(tax_id_type="GSTIN", tax_id_value="29ABCDE1234F1Z")
    out, note = t.read_tax_id(bad, INVOICE_TEXT, OURS)
    assert out.tax_id_value == THEIRS and "15 characters" in note  # the bad value was dropped, the one printed id taken
    out, note = t.read_tax_id(bad, "no ids printed", OURS)
    assert out.tax_id_value is None and note


# ------------------------------------------------------------------------------------------ from the matched customer
def test_the_matched_customers_own_id_and_type_fill_an_empty_field_only():
    inv = ExtractedInvoice()
    out = t.with_customer_tax_id(inv, "GB284773109", db_enums.TaxIdType.VAT)
    assert (out.tax_id_value, out.tax_id_type) == ("GB284773109", TaxIdType.VAT)
    assert t.with_customer_tax_id(ExtractedInvoice(tax_id_value="  "), THEIRS, "GSTIN").tax_id_value == THEIRS
    printed = ExtractedInvoice(tax_id_type="GSTIN", tax_id_value=THIRD)
    assert t.with_customer_tax_id(printed, THEIRS, "GSTIN") is printed  # a printed value is never replaced
    assert t.with_customer_tax_id(inv, None, "GSTIN") is inv


# ------------------------------------------------------------------------------------------ the extraction functions
def _settings(monkeypatch, module):
    monkeypatch.setattr(module.get_settings(), "our_gstin", OURS)
    monkeypatch.setattr(module.get_settings(), "our_company_name", "Genworx Technologies Pvt Ltd")


def test_invoice_extraction_replaces_our_own_gstin_returned_by_the_reader(monkeypatch):
    _settings(monkeypatch, invoice_extraction)

    async def reader(schema, messages):
        return ExtractedInvoice(customer_name="Zenith", tax_id_type="GSTIN", tax_id_value=OURS), "stub", 1

    monkeypatch.setattr(invoice_extraction, "run_structured_extraction", reader)
    out = asyncio.run(invoice_extraction.extract_invoice(INVOICE_TEXT))
    assert out.invoice.tax_id_value == THEIRS and out.tax_id_note is None


def test_invoice_extraction_drops_a_corrupted_id_and_says_why(monkeypatch):
    _settings(monkeypatch, invoice_extraction)

    async def reader(schema, messages):
        return ExtractedInvoice(customer_name="Brightwave", tax_id_type="VAT", tax_id_value="G284773109"), "stub", 1

    monkeypatch.setattr(invoice_extraction, "run_structured_extraction", reader)
    out = asyncio.run(invoice_extraction.extract_invoice("Bill To: Brightwave Digital Ltd, London. no id printed"))
    assert out.invoice.tax_id_value is None and "G284773109" in out.tax_id_note and "VAT" in out.tax_id_note


def test_po_extraction_fills_an_id_the_reader_left_empty(monkeypatch):
    _settings(monkeypatch, po_extraction)

    async def reader(schema, messages):
        return ExtractedPurchaseOrder(customer_name="Zenith"), "stub", 1

    monkeypatch.setattr(po_extraction, "run_structured_extraction", reader)
    out = asyncio.run(po_extraction.extract_purchase_order(INVOICE_TEXT))
    assert out.purchase_order.tax_id_value == THEIRS


# ------------------------------------------------------------------------------------------ old extractions, prompts
def test_an_extraction_saved_with_the_old_gstin_field_is_read_as_a_gstin():
    inv = ExtractedInvoice.model_validate({"customer_gstin": THEIRS, "customer_name": "Zenith", "confidence": {"customer_gstin": 88, "tax": 70}})
    assert (inv.tax_id_type, inv.tax_id_value, inv.confidence.tax_id, inv.confidence.tax) == (TaxIdType.GSTIN, THEIRS, 88, 70)
    po = ExtractedPurchaseOrder.model_validate({"customer_gstin": THEIRS, "po_number": "P1", "confidence": {"customer_gstin": 60}})
    assert (po.tax_id_type, po.tax_id_value, po.confidence.tax_id) == (TaxIdType.GSTIN, THEIRS, 60)
    assert from_legacy({"tax_id_value": VAT, "tax_id_type": "VAT", "customer_gstin": THEIRS})["tax_id_value"] == VAT  # new wins


def test_the_prompts_ask_for_three_things_with_examples_and_keep_telling_ours_from_theirs():
    from prompts.invoices.extraction import build_system_prompt as invoice_prompt
    from prompts.purchase_orders.extraction import build_system_prompt as po_prompt

    for prompt in (invoice_prompt("Genworx", OURS), po_prompt("Genworx", OURS)):
        assert "tax_id_type" in prompt and "tax_id_value" in prompt and "tax_id_raw_label" in prompt
        assert "GSTIN, VAT, TRN or EIN" in prompt and "OTHER" in prompt
        assert 'VAT Reg No: GB 284 7731 09' in prompt and 'TRN: 100123456700003' in prompt and "29ABCDE1234F1Z5" in prompt
        assert "by elimination" in prompt and OURS in prompt and "NEVER the customer's" in prompt
        assert "customer_gstin" not in prompt


@pytest.mark.parametrize("label", ["PAN", "Pan No", "CIN", "Phone", "TEL", "IFSC", "A/C", "Udyam Reg"])
def test_a_number_labelled_as_something_else_is_not_a_tax_id(label):
    inv = ExtractedInvoice(tax_id_type="OTHER", tax_id_value="AAACP1234C", tax_id_raw_label=label)
    out, note = t.check_shape(inv)
    assert out.tax_id_value is None and out.tax_id_type is None and out.tax_id_raw_label is None and label.upper() in note.upper()
    # and so the customer's own id can still be filled in, and a single other id in the text can still be taken
    assert t.with_customer_tax_id(out, THEIRS, "GSTIN").tax_id_value == THEIRS


def test_a_real_label_that_merely_contains_those_letters_is_kept():
    for label in ("GSTIN", "VAT Reg No", "TRN", "EIN", "UEN", "Tax ID", "ABN", "BTW-nummer"):
        inv = ExtractedInvoice(tax_id_type="OTHER", tax_id_value="201934512K", tax_id_raw_label=label)
        assert t.check_shape(inv) == (inv, None), label
