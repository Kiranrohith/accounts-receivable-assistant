"""Invoice-number normalization, pure. Illustrative cases; the answer key is the seed kit."""

import pytest

from data.repositories.invoices.numbers import normalize_invoice_no


@pytest.mark.parametrize("written", ["INV-1176", "INV1176", "inv 1176", "Inv/1176", " inv_1176 ", "I.N.V 1176"])
def test_every_spelling_of_the_number_is_the_same(written):
    assert normalize_invoice_no(written) == "INV1176"


def test_the_prefix_is_required():
    assert normalize_invoice_no("1104") == "1104"
    assert normalize_invoice_no("1104") != normalize_invoice_no("INV-1104")  # a bare number names nothing


def test_debit_notes_follow_the_same_rule():
    assert normalize_invoice_no("DN-2001") == "DN2001" == normalize_invoice_no("dn 2001")


def test_accented_letters_are_removed_not_transliterated():
    # the generated column in the database is ASCII-only too; the two must agree
    assert normalize_invoice_no("INV-é1") == "INV1"
    assert normalize_invoice_no("ß-1") == "1"


def test_different_numbers_stay_different():
    assert normalize_invoice_no("INV-1176") != normalize_invoice_no("INV-1177")
    assert normalize_invoice_no("INV-2026-001") == "INV2026001"
