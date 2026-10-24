import uuid
from decimal import Decimal

from core.services.purchase_orders.fulfillment import (
    BilledLine,
    OrderedLine,
    evaluate_lines,
)
from core.services.purchase_orders.normalize import normalize_description
from core.services.purchase_orders.validation import validate_purchase_order
from schemas.invoices.validation import Outcome, Severity
from schemas.purchase_orders.extraction import (
    ExtractedPurchaseOrder,
    POFieldConfidences,
    POLineItem,
)

D = Decimal


def po(**over) -> ExtractedPurchaseOrder:
    base = dict(
        po_number="PO-1",
        customer_name="Zenith",
        tax_id_type="GSTIN", tax_id_value="33AABCZ1234F1Z5",
        currency="INR",
        total_amount=D("1000"),
        line_items=[POLineItem(description="Laptop", quantity=D("10"))],
        confidence=POFieldConfidences(
            po_number=95, customer_name=95, total_amount=95, line_items=95
        ),
    )
    base.update(over)
    return ExtractedPurchaseOrder(**base)


def codes(result, severity=None):
    return {i.code for i in result.issues if severity is None or i.severity is severity}


# ---- normalisation ----
def test_normalize_ignores_case_punctuation_and_spacing():
    assert normalize_description("  Laptop (Dell-5440),  14\" ") == "laptop dell 5440 14"
    assert normalize_description("LAPTOP_dell 5440") == normalize_description("laptop Dell-5440")
    assert normalize_description(None) == ""


# ---- validation ----
def test_clean_po_passes():
    assert validate_purchase_order(po()).outcome is Outcome.PASS


def test_po_number_certainly_absent_is_rejected():
    r = validate_purchase_order(po(po_number=None, confidence=POFieldConfidences(po_number=95, customer_name=95, total_amount=95, line_items=95)))
    assert r.outcome is Outcome.FAIL and "missing_field" in codes(r, Severity.FAIL)


def test_po_number_possibly_missed_goes_to_review():
    low = POFieldConfidences(po_number=40, customer_name=95, total_amount=95, line_items=95)
    r = validate_purchase_order(po(po_number=None, confidence=low))
    assert r.outcome is Outcome.REVIEW and "po_number_unreadable" in codes(r)
    none = POFieldConfidences(customer_name=95, total_amount=95, line_items=95)
    assert validate_purchase_order(po(po_number=None, confidence=none)).outcome is Outcome.REVIEW


def test_no_customer_at_all_fails_but_a_tax_id_alone_is_enough():
    assert validate_purchase_order(po(customer_name=None, tax_id_value=None)).outcome is Outcome.FAIL
    r = validate_purchase_order(po(customer_name=None))
    assert "missing_field" not in codes(r)


def test_no_lines_and_bad_quantities():
    assert "no_line_items" in codes(validate_purchase_order(po(line_items=[])), Severity.FAIL)
    for qty in (D("0"), D("-3")):
        r = validate_purchase_order(po(line_items=[POLineItem(description="x", quantity=qty)]))
        assert "non_positive_quantity" in codes(r, Severity.FAIL)
    r = validate_purchase_order(po(line_items=[POLineItem(description="x", quantity=None)]))
    assert r.outcome is Outcome.REVIEW and "missing_quantity" in codes(r)


def test_amount_and_currency_rules():
    assert "non_positive_amount" in codes(validate_purchase_order(po(total_amount=D("0"))), Severity.FAIL)
    assert "invalid_currency" in codes(validate_purchase_order(po(currency="rupees")), Severity.FAIL)
    assert validate_purchase_order(po(currency=None)).outcome is Outcome.PASS


def test_low_or_missing_confidence_goes_to_review():
    r = validate_purchase_order(po(confidence=POFieldConfidences(po_number=50, customer_name=95, total_amount=95, line_items=95)))
    assert r.outcome is Outcome.REVIEW and "low_confidence" in codes(r)
    r = validate_purchase_order(po(confidence=POFieldConfidences()))
    assert r.outcome is Outcome.REVIEW and "no_confidence" in codes(r)


# ---- fulfilment check ----
def lines(*qty):
    return [OrderedLine(uuid.uuid4(), normalize_description(n), D(q)) for n, q in qty]


def billed(*items):
    return [BilledLine(uuid.uuid4(), n, None if q is None else D(q)) for n, q in items]


def test_within_order_passes_and_reports_remaining():
    po_lines = lines(("Laptop", "10"), ("Monitor", "5"))
    out = evaluate_lines(po_lines, {}, billed(("laptop", "4"), ("MONITOR!", "5")))
    assert all(c.ok for c in out)
    assert [c.remaining_after for c in out] == [D("6"), D("0")]


def test_cumulative_ten_plus_ten_plus_ten_against_ten_fails_on_second():
    po_lines = lines(("Laptop", "10"))
    key = po_lines[0].id
    first = evaluate_lines(po_lines, {}, billed(("Laptop", "10")))
    assert first[0].ok and first[0].remaining_after == D("0")
    second = evaluate_lines(po_lines, {key: D("10")}, billed(("Laptop", "10")))
    assert not second[0].ok and second[0].remaining_after == D("-10")
    assert "over the ordered quantity" in second[0].reason


def test_item_not_on_po_fails():
    out = evaluate_lines(lines(("Laptop", "10")), {}, billed(("Server", "1")))
    assert not out[0].ok and out[0].po_line_item_id is None and "not on the purchase order" in out[0].reason


def test_missing_or_zero_quantity_fails():
    out = evaluate_lines(lines(("Laptop", "10")), {}, billed(("Laptop", None), ("Laptop", "0")))
    assert [c.ok for c in out] == [False, False]


def test_two_lines_of_same_item_in_one_invoice_add_up():
    out = evaluate_lines(lines(("Laptop", "10")), {}, billed(("Laptop", "6"), ("laptop", "6")))
    assert [c.ok for c in out] == [True, False] and out[1].already_invoiced == D("6")


def test_po_without_ordered_quantity_fails():
    po_lines = [OrderedLine(uuid.uuid4(), "laptop", None)]
    assert not evaluate_lines(po_lines, {}, billed(("Laptop", "1")))[0].ok
