"""The exchange rate an invoice is converted at, looked up ONLY when its currency is not INR.

An INR invoice never touches the rate service. A foreign invoice is converted at the ECB
rate for its invoice date (frozen on the invoice, so later rate moves never change it).
Raises (all carry a plain-English message):
- FxRateNotFound  (422): the ECB has no rate for the currency/date, or the newest one is
                         too old to use automatically
- FxServiceError  (502): the ECB is unreachable or erroring after the retries
"""

from datetime import date
from decimal import Decimal

from constants.constants import FX_MAX_STALE_DAYS
from core.services.fx.ecb import FxRate, FxRateNotFound, fetch_rate_to_inr

BASE_CURRENCY = "INR"


def is_foreign(currency: str | None) -> bool:
    code = (currency or "").strip().upper()
    return bool(code) and code != BASE_CURRENCY


def manual_rate(currency: str, invoice_date: date, inr_per_unit: Decimal) -> FxRate:
    """A rate a person typed in for a currency the ECB does not publish. It is frozen on the invoice exactly like an
    ECB one; the ECB legs do not apply, so they are left empty."""
    return FxRate(
        currency=currency.strip().upper(),
        requested_date=invoice_date,
        rate_date=invoice_date,
        inr_per_unit=inr_per_unit,
        eur_inr=Decimal(0),
        eur_foreign=None,
        source="entered by a person",
    )


async def rate_for_invoice(currency: str, invoice_date: date) -> FxRate:
    rate = await fetch_rate_to_inr(currency, invoice_date)
    if rate.days_back > FX_MAX_STALE_DAYS:
        raise FxRateNotFound(
            f"The newest ECB {rate.currency} rate is from {rate.rate_date}, "
            f"{rate.days_back} days before the invoice date {invoice_date}; "
            "too old to use automatically"
        )
    return rate
