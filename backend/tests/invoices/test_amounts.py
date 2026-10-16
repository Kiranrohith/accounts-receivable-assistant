from decimal import Decimal

import pytest

from core.services.invoices.amounts import freeze_inr_amounts


def freeze(total, deducts_tds=True, debit_note=False):
    return freeze_inr_amounts(Decimal(total), deducts_tds=deducts_tds, is_debit_note=debit_note)


def test_tds_is_ten_percent_of_gross_and_comes_off_the_collectible():
    a = freeze("118000")
    assert (a.amount_inr, a.tds_amount, a.collectible_total) == (
        Decimal("118000.00"),
        Decimal("11800.00"),
        Decimal("106200.00"),
    )
    assert a.fx_rate == Decimal("1")


def test_debit_note_skips_tds():
    a = freeze("50000", debit_note=True)
    assert a.tds_amount == Decimal("0.00") and a.collectible_total == Decimal("50000.00")


def test_customer_that_does_not_deduct_tds_collects_everything():
    a = freeze("50000", deducts_tds=False)
    assert a.tds_amount == Decimal("0.00") and a.collectible_total == Decimal("50000.00")


@pytest.mark.parametrize(
    "total, tds, collectible",
    [
        ("100.05", "10.01", "90.04"),  # 10.005 rounds half up to 10.01
        ("100.04", "10.00", "90.04"),
        ("0.04", "0.00", "0.04"),
        ("99999999999999.99", "10000000000000.00", "89999999999999.99"),
    ],
)
def test_rounding_keeps_the_pieces_adding_up_exactly(total, tds, collectible):
    a = freeze(total)
    assert (a.tds_amount, a.collectible_total) == (Decimal(tds), Decimal(collectible))
    assert a.tds_amount + a.collectible_total == a.amount_inr


def test_gross_is_rounded_to_two_places_before_tds():
    assert freeze("100.005").amount_inr == Decimal("100.01")


# ---- foreign currency (rate = INR per unit, from the ECB) ----
from core.services.invoices.amounts import freeze_amounts  # noqa: E402


def test_foreign_invoice_converts_to_inr_and_takes_tds_on_the_inr_amount():
    a = freeze_amounts(Decimal("1000"), rate=Decimal("96.32855373"), foreign=True,
                       deducts_tds=True, is_debit_note=False)
    assert a.amount_foreign == Decimal("1000.00") and a.fx_rate == Decimal("96.32855373")
    assert a.amount_inr == Decimal("96328.55")  # 96328.55373 rounded half up to paise
    assert a.tds_amount == Decimal("9632.86")  # 10% of 96328.55 = 9632.855 -> half up
    assert a.collectible_total == Decimal("86695.69")


def test_foreign_debit_note_and_non_deducting_customer_skip_tds():
    for kwargs in ({"deducts_tds": True, "is_debit_note": True}, {"deducts_tds": False, "is_debit_note": False}):
        a = freeze_amounts(Decimal("10"), rate=Decimal("90"), foreign=True, **kwargs)
        assert a.tds_amount == Decimal("0.00") and a.collectible_total == Decimal("900.00")


def test_inr_invoice_has_no_foreign_amount_and_rate_one():
    a = freeze("100")
    assert a.amount_foreign is None and a.fx_rate == Decimal("1")
