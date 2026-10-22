"""The tolerance rules of 2026-10-21, pure. The numbers are illustrative: the answer key is the seed kit
(payments_expected.json), which is to run in CI once it is committed; do not treat these as the key."""

import uuid
from decimal import Decimal

import pytest

from core.services.payments.tolerance import Target, classify, split_over_set
from data.models.postgres.enums import Tolerance

D = Decimal


# ---- INR payment on an INR invoice: +/- 1.00 inclusive, no percentage band --------------------
@pytest.mark.parametrize(
    ("received", "expected"),
    [
        ("9999.00", Tolerance.T1),  # INR 1.00 short
        ("10000.75", Tolerance.T1),  # INR 0.75 over
        ("10001.00", Tolerance.T1),  # the bound is inclusive, over
        ("9999.01", Tolerance.T1),
        ("9998.50", Tolerance.T4),  # INR 1.50 short is NOT T1 ...
        ("9920.00", Tolerance.T4),  # 0.8% short is NOT T2 (an INR invoice never has a band)
        ("10001.01", Tolerance.T3),
        ("10150.00", Tolerance.T3),  # even 1.5% over is T3, never T2
    ],
)
def test_inr_has_t1_and_nothing_between(received, expected):
    assert classify(D(received), D("10000.00"), foreign=False).tolerance is expected


# ---- foreign payment in the invoice's own currency: exact is T1, +/- 1.5% inclusive is T2 -----
@pytest.mark.parametrize(
    ("received", "expected", "diff"),
    [
        ("83000.00", Tolerance.T1, "0.00"),  # exactly the invoiced amount
        ("82253.00", Tolerance.T2, "-747.00"),  # 0.9% short
        ("83996.00", Tolerance.T2, "996.00"),  # 1.2% over
        ("81755.00", Tolerance.T2, "-1245.00"),  # exactly 1.5% short
        ("84245.00", Tolerance.T2, "1245.00"),  # exactly 1.5% over
        ("81672.00", Tolerance.T4, "-1328.00"),  # 1.6% short
        ("84328.00", Tolerance.T3, "1328.00"),  # 1.6% over
        ("82999.50", Tolerance.T2, "-0.50"),  # a foreign difference of even INR 0.50 is not "exact"
    ],
)
def test_foreign_same_currency(received, expected, diff):
    fit = classify(D(received), D("83000.00"), foreign=True)
    assert fit.tolerance is expected and fit.diff == D(diff)


def test_t2_residual_is_signed_and_no_credit_note_is_born():
    short = split_over_set([Target(uuid.uuid4(), D("83000.00"))], D("82253.00"), foreign=True)
    over = split_over_set([Target(uuid.uuid4(), D("83000.00"))], D("83996.00"), foreign=True)
    for plan, residual in ((short, "-747.00"), (over, "996.00")):
        assert plan.overall is Tolerance.T2 and plan.credit == 0 and plan.unapplied == 0
        assert plan.slices[0].allocated == D("83000.00") and plan.slices[0].residual == D(residual)


def test_beyond_the_band_foreign_payments_are_t3_and_t4():
    over = split_over_set([Target(uuid.uuid4(), D("83000.00"))], D("84328.00"), foreign=True)
    short = split_over_set([Target(uuid.uuid4(), D("83000.00"))], D("81672.00"), foreign=True)
    assert over.overall is Tolerance.T3 and over.credit == D("1328.00")
    assert short.overall is Tolerance.T4 and short.slices[0].allocated == D("81672.00")
