import uuid
from decimal import Decimal

import pytest

from core.services.purchase_orders.listing import fulfillment_of
from data.models.postgres import PurchaseOrderLineItem

D = Decimal


def line(quantity) -> PurchaseOrderLineItem:
    return PurchaseOrderLineItem(
        id=uuid.uuid4(), po_id=uuid.uuid4(), description="Laptop", quantity=None if quantity is None else D(quantity)
    )


@pytest.mark.parametrize(
    ("ordered", "invoiced", "expected"),
    [
        (["10"], ["0"], "Not invoiced"),
        (["10"], ["4"], "Partly invoiced"),
        (["10"], ["10"], "Fully invoiced"),
        (["10"], ["12"], "Over-invoiced"),  # only possible through an approved override
        (["10", "5"], ["10", "2"], "Partly invoiced"),  # one line done, another not
        (["10", "5"], ["10", "5"], "Fully invoiced"),
        (["10", "5"], ["12", "1"], "Over-invoiced"),  # over on any line wins
        (["10", "5"], ["0", "0"], "Not invoiced"),
        ([None], ["0"], "Not invoiced"),  # a line with no ordered quantity cannot be judged
    ],
)
def test_fulfillment_label(ordered, invoiced, expected):
    lines = [line(q) for q in ordered]
    ledger = {ln.id: D(done) for ln, done in zip(lines, invoiced, strict=True)}
    assert fulfillment_of(lines, ledger) == expected


def test_a_po_with_no_lines_is_not_invoiced():
    assert fulfillment_of([], {}) == "Not invoiced"
