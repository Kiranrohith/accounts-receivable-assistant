from datetime import date

from fastapi import APIRouter, Depends, Query

from api.rest.dependencies import require_permission
from core.services.fx.ecb import fetch_rate_to_inr
from data.models.postgres import User
from schemas.fx.rates import FxRateOut

router = APIRouter(prefix="/fx", tags=["exchange rates"])


@router.get(
    "/rate",
    response_model=FxRateOut,
    summary="INR per unit of a currency on a date (ECB)",
    description=(
        "Requires invoices.view. Fetches the ECB Data Portal reference rate for the date and "
        "converts it to INR per one unit of the currency (a cross rate through the euro). "
        "On weekends and TARGET holidays the latest earlier rate is used: `rate_date` says "
        "which day, `days_back` how far. INR returns 1. An unknown currency returns 422; "
        "if the ECB is unreachable the answer is 502 (retry later)."
    ),
    responses={
        422: {"description": "Not a currency the ECB publishes, or no rate in the window"},
        502: {"description": "The ECB service is unreachable or erroring"},
    },
)
async def rate(
    currency: str = Query(..., min_length=3, max_length=3, examples=["USD"]),
    on: date = Query(..., alias="date", description="The date to convert on (YYYY-MM-DD)"),
    _: User = Depends(require_permission("invoices.view")),
) -> FxRateOut:
    found = await fetch_rate_to_inr(currency, on)
    return FxRateOut(
        currency=found.currency,
        requested_date=found.requested_date,
        rate_date=found.rate_date,
        days_back=found.days_back,
        inr_per_unit=found.inr_per_unit,
        eur_inr=found.eur_inr,
        eur_foreign=found.eur_foreign,
        source=found.source,
    )
