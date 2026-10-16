from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class FxRateOut(BaseModel):
    currency: str
    requested_date: date
    rate_date: date  # the ECB publication day the rate comes from (earlier on weekends/holidays)
    days_back: int  # requested_date - rate_date
    inr_per_unit: Decimal  # INR for one unit of the currency, 8 decimal places
    eur_inr: Decimal  # ECB leg: INR per 1 EUR on rate_date
    eur_foreign: Decimal | None  # ECB leg: units of the currency per 1 EUR (None for EUR/INR)
    source: str
