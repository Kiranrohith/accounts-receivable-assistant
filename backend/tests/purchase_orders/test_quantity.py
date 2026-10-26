"""A quantity read off a document may carry its unit ("3 months"): the number is kept, so one such line no longer fails the
whole document (every attempt on every model used to be rejected with decimal_parsing)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.invoices.extraction import LineItem
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POLineItem


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("3 months", "3"), ("2 sprints", "2"), ("8 days", "8"), ("7 sessions", "7"), ("53 screens", "53"),
        ("4 m", "4"), ("8.5 days", "8.5"), ("1,200 nos", "1200"), (" 28 person-days ", "28"), ("3months", "3"),
        ("28", "28"), ("390.0", "390.0"), (28, "28"), (Decimal("2.5"), "2.5"),
    ],
)
def test_the_number_is_kept_when_a_unit_is_written_with_it(printed, expected):
    assert POLineItem(description="x", quantity=printed).quantity == Decimal(expected)
    assert LineItem(description="x", quantity=printed).quantity == Decimal(expected)


@pytest.mark.parametrize("printed", ["monthly", "per month", "", "-", "n/a", None])
def test_text_with_no_number_is_no_quantity_not_a_failed_document(printed):
    assert POLineItem(description="x", quantity=printed).quantity is None
    assert LineItem(description="x", quantity=printed).quantity is None


@pytest.mark.parametrize("printed", ["3 x 4", "3 m2", "1/2", "about 3"])
def test_anything_that_is_not_number_then_unit_still_fails_loudly(printed):
    with pytest.raises(ValidationError):
        POLineItem(description="x", quantity=printed)


def test_the_po_that_failed_every_model_now_reads():
    po = ExtractedPurchaseOrder(
        po_number="PO-BLU-0950", currency="INR", total_amount="495600.0",
        line_items=[
            {"description": "Cloud infrastructure management monthly", "quantity": "4 months", "unit_price": "45000.0", "line_total": "180000.0"},
            {"description": "Application support retainer monthly", "quantity": "2 months", "unit_price": "60000.0", "line_total": "120000.0"},
        ],
    )
    assert [line.quantity for line in po.line_items] == [Decimal("4"), Decimal("2")]
