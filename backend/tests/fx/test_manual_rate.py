from datetime import date
from decimal import Decimal

from core.services.invoices.fx import manual_rate


def test_a_typed_rate_is_frozen_like_an_ecb_one_and_says_who_gave_it():
    r = manual_rate(" qar ", date(2026, 10, 5), Decimal("22.75"))
    assert (r.currency, r.inr_per_unit, r.rate_date, r.days_back) == ("QAR", Decimal("22.75"), date(2026, 10, 5), 0)
    assert r.source == "entered by a person"
