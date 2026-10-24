import pytest

from data.repositories.purchase_orders.numbers import normalize_po_no


@pytest.mark.parametrize("written", ["PO-123", "PO123", "po 123", "P.O./123", " po_123 ", "Po-123"])
def test_every_spelling_of_the_number_is_the_same(written):
    assert normalize_po_no(written) == "PO123"


def test_the_prefix_is_required():
    assert normalize_po_no("123") == "123"
    assert normalize_po_no("123") != normalize_po_no("PO-123")


def test_different_numbers_stay_different():
    assert normalize_po_no("PO-123") != normalize_po_no("PO-1234")
    assert normalize_po_no("4500012345") == "4500012345"
    assert normalize_po_no("PO/2026/001") == "PO2026001"


def test_accented_letters_are_removed_not_transliterated():
    # the generated column in the database is ASCII-only too; the two must agree
    assert normalize_po_no("PO-é1") == "PO1"


def test_nothing_but_punctuation_is_empty():
    assert normalize_po_no("---") == ""
    assert normalize_po_no("  ") == ""
