from decimal import Decimal

import pytest

from core.services.payments.status import invoice_status_after
from data.models.postgres.enums import InvoiceStatus

D = Decimal


@pytest.mark.parametrize(
    ("paid", "total", "expected"),
    [
        ("0", "900", InvoiceStatus.OPEN),
        ("0.00", "900", InvoiceStatus.OPEN),
        ("-1", "900", InvoiceStatus.OPEN),  # never below open
        ("0.01", "900", InvoiceStatus.PARTLY_PAID),
        ("899.99", "900", InvoiceStatus.PARTLY_PAID),
        ("900", "900", InvoiceStatus.PAID),  # exactly paid
        ("950", "900", InvoiceStatus.PAID),
    ],
)
def test_invoice_status_follows_the_money(paid, total, expected):
    assert invoice_status_after(D(paid), D(total)) is expected
