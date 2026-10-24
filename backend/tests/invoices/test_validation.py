from datetime import date
from decimal import Decimal

import pytest

from core.services.invoices.validation import validate_extraction
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences
from schemas.invoices.validation import Outcome, Severity

TODAY = date(2026, 10, 14)
ALL_95 = {f: 95 for f in FieldConfidences.model_fields}


def make(**overrides) -> ExtractedInvoice:
    base = dict(
        invoice_number="INV-1",
        customer_name="Acme Traders Pvt Ltd",
        tax_id_type="GSTIN", tax_id_value="29ABCDE1234F1Z5",
        invoice_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        currency="INR",
        total_amount=Decimal("50000"),
        confidence=FieldConfidences(**ALL_95),
    )
    base.update(overrides)
    return ExtractedInvoice(**base)


def codes(result, severity=None):
    return sorted(i.code for i in result.issues if severity is None or i.severity is severity)


def test_clean_invoice_passes():
    result = validate_extraction(make(), today=TODAY)
    assert result.outcome is Outcome.PASS and result.issues == []


@pytest.mark.parametrize("field", ["invoice_number", "invoice_date", "due_date", "total_amount"])
def test_each_must_have_field_missing_fails(field):
    result = validate_extraction(make(**{field: None}), today=TODAY)
    assert result.outcome is Outcome.FAIL
    assert ("missing_field", field) in [(i.code, i.field) for i in result.issues]


def test_missing_customer_name_is_not_a_rejection():
    # Identity is judged by customer matching (GSTIN may still identify them).
    assert validate_extraction(make(customer_name=None), today=TODAY).outcome is Outcome.PASS


def test_missing_currency_goes_to_review_not_rejection():
    result = validate_extraction(make(currency=None), today=TODAY)
    assert result.outcome is Outcome.REVIEW and codes(result) == ["missing_currency"]


def test_blank_string_counts_as_missing():
    result = validate_extraction(make(invoice_number="   "), today=TODAY)
    assert result.outcome is Outcome.FAIL


def test_a_tax_id_is_not_required():
    assert validate_extraction(make(tax_id_value=None, tax_id_type=None), today=TODAY).outcome is Outcome.PASS


def test_all_reasons_reported_not_just_first():
    result = validate_extraction(
        make(invoice_number=None, due_date=None, total_amount=Decimal("0")), today=TODAY
    )
    assert result.outcome is Outcome.FAIL
    assert codes(result, Severity.FAIL) == ["missing_field", "missing_field", "non_positive_amount"]


@pytest.mark.parametrize("amount", ["0", "-5", "-0.01"])
def test_amount_must_be_positive(amount):
    result = validate_extraction(make(total_amount=Decimal(amount)), today=TODAY)
    assert "non_positive_amount" in codes(result, Severity.FAIL)


@pytest.mark.parametrize("currency", ["RS", "INRR", "in r", "12$", "inr"])
def test_currency_must_be_three_capital_letters(currency):
    result = validate_extraction(make(currency=currency), today=TODAY)
    assert "invalid_currency" in codes(result, Severity.FAIL)


def test_due_before_invoice_date_fails():
    result = validate_extraction(make(due_date=date(2026, 9, 30)), today=TODAY)
    assert result.outcome is Outcome.FAIL and "due_before_invoice" in codes(result)


def test_due_equal_to_invoice_date_is_fine():
    result = validate_extraction(make(due_date=date(2026, 10, 1)), today=TODAY)
    assert result.outcome is Outcome.PASS


@pytest.mark.parametrize(
    "invoice_date, expected",
    [
        (date(2024, 10, 14), Outcome.PASS),  # exactly 2 years ago: allowed
        (date(2024, 10, 13), Outcome.REVIEW),  # one day older
        (date(2026, 11, 13), Outcome.PASS),  # exactly +30 days: allowed
        (date(2026, 11, 14), Outcome.REVIEW),  # +31 days
    ],
)
def test_invoice_date_bounds(invoice_date, expected):
    result = validate_extraction(
        make(invoice_date=invoice_date, due_date=invoice_date), today=TODAY
    )
    assert result.outcome is expected


def test_invoice_date_old_and_future_use_distinct_codes():
    assert "invoice_date_too_old" in codes(
        validate_extraction(make(invoice_date=date(2020, 1, 1), due_date=date(2020, 2, 1)), today=TODAY)
    )
    assert "invoice_date_in_future" in codes(
        validate_extraction(make(invoice_date=date(2027, 3, 1), due_date=date(2027, 3, 31)), today=TODAY)
    )


def test_due_date_more_than_a_year_after_invoice_goes_to_review():
    ok = validate_extraction(make(due_date=date(2027, 10, 1)), today=TODAY)
    far = validate_extraction(make(due_date=date(2027, 10, 2)), today=TODAY)
    assert ok.outcome is Outcome.PASS
    assert far.outcome is Outcome.REVIEW and codes(far) == ["due_date_too_far"]


@pytest.mark.parametrize(
    "invoice_date, expected",
    [
        (date(2026, 2, 28), Outcome.PASS),  # 29 Feb 2028 minus 2 years falls back to 28 Feb 2026
        (date(2026, 2, 27), Outcome.REVIEW),
    ],
)
def test_two_years_back_from_leap_day(invoice_date, expected):
    result = validate_extraction(
        make(invoice_date=invoice_date, due_date=invoice_date), today=date(2028, 2, 29)
    )
    assert result.outcome is expected


@pytest.mark.parametrize(
    "subtotal, tax, total, expected",
    [
        ("100", "18", "118", Outcome.PASS),
        ("100", "18", "118.80", Outcome.PASS),  # off by 0.80, within the 1-unit tolerance
        ("100", "18", "119", Outcome.PASS),  # off by exactly 1: allowed
        ("100", "18", "119.01", Outcome.REVIEW),
        ("100", "18", "100", Outcome.REVIEW),  # tax ignored in the total
    ],
)
def test_subtotal_plus_tax_must_match_total(subtotal, tax, total, expected):
    result = validate_extraction(
        make(subtotal=Decimal(subtotal), tax_total=Decimal(tax), total_amount=Decimal(total)),
        today=TODAY,
    )
    assert result.outcome is expected


def test_arithmetic_skipped_when_subtotal_or_tax_absent():
    assert validate_extraction(make(subtotal=Decimal("10")), today=TODAY).outcome is Outcome.PASS
    assert validate_extraction(make(tax_total=Decimal("10")), today=TODAY).outcome is Outcome.PASS


def test_low_confidence_on_required_field_goes_to_review():
    conf = FieldConfidences(**{**ALL_95, "invoice_date": 60})
    result = validate_extraction(make(confidence=conf), today=TODAY)
    assert result.outcome is Outcome.REVIEW
    assert [(i.code, i.field) for i in result.issues] == [("low_confidence", "invoice_date")]


def test_confidence_exactly_at_threshold_is_accepted():
    conf = FieldConfidences(**{**ALL_95, "total_amount": 75})
    assert validate_extraction(make(confidence=conf), today=TODAY).outcome is Outcome.PASS


def test_value_without_any_confidence_goes_to_review():
    result = validate_extraction(make(confidence=FieldConfidences()), today=TODAY)
    assert result.outcome is Outcome.REVIEW
    assert set(codes(result)) == {"no_confidence"}


def test_low_confidence_on_optional_field_is_ignored():
    conf = FieldConfidences(**{**ALL_95, "po_number": 10, "tax_id": 10})
    assert validate_extraction(make(confidence=conf), today=TODAY).outcome is Outcome.PASS


def test_missing_field_does_not_also_complain_about_its_confidence():
    result = validate_extraction(make(invoice_date=None), today=TODAY)
    assert codes(result) == ["missing_field"]


def test_fail_outranks_review():
    result = validate_extraction(
        make(total_amount=Decimal("-1"), confidence=FieldConfidences(**{**ALL_95, "currency": 10})),
        today=TODAY,
    )
    assert result.outcome is Outcome.FAIL
    assert {i.severity for i in result.issues} == {Severity.FAIL, Severity.REVIEW}
