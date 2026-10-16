"""Exchange rates from the ECB Data Portal API, expressed as INR per one unit of a currency.

The ECB publishes reference rates against the euro (1 EUR = x currency) for each TARGET
working day. INR per unit of a currency is therefore a cross rate through the euro:

    INR per 1 USD = (INR per EUR) / (USD per EUR)

For a date with no publication (weekend, TARGET holiday) the latest earlier rate is used, and
the rate's own date is returned beside the requested one so the caller can see (and store)
which day it really is. Both legs always come from the same publication day.

Failure handling follows the retry policy: timeouts, connection errors, 429 and 5xx are
retried (3s / 9s / 15s, jittered +/-30%) and then raised as FxServiceError (retryable);
other 4xx responses are not retried. The ECB answers 404 when it has no data for the
request, which here means an unknown or unsupported currency (or no rate in the window).
"""

import asyncio
import csv
import io
import logging
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import httpx

from constants.constants import (
    BACKOFF_JITTER,
    ECB_API_URL,
    ECB_TIMEOUT_SECONDS,
    FX_LOOKBACK_DAYS,
    OTHER_RETRY_DELAYS_SECONDS,
)
from core.exceptions.errors import ExternalServiceError, ValidationFailure

logger = logging.getLogger(__name__)

BASE = "INR"
RATE_PLACES = Decimal("0.00000001")  # invoices.fx_rate_invoice is NUMERIC(18, 8)


class FxRateNotFound(ValidationFailure):
    """The ECB has no rate for this currency (unknown code, or nothing published in the window)."""


class FxServiceError(ExternalServiceError):
    """The ECB could not be reached or answered with an error; retryable by the caller later."""

    retryable = True


@dataclass(frozen=True)
class FxRate:
    currency: str
    requested_date: date
    rate_date: date  # the ECB publication day the rate really comes from
    inr_per_unit: Decimal
    eur_inr: Decimal  # the two ECB legs the cross rate was built from
    eur_foreign: Decimal | None  # None for EUR itself and INR
    source: str = "ECB"

    @property
    def days_back(self) -> int:
        return (self.requested_date - self.rate_date).days


def _jittered(seconds: float) -> float:
    return seconds * (1 + random.uniform(-float(BACKOFF_JITTER), float(BACKOFF_JITTER)))


def _parse(csv_text: str) -> dict[str, dict[date, Decimal]]:
    """CSV (format=csvdata) -> {currency: {day: units of currency per EUR}}."""
    out: dict[str, dict[date, Decimal]] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        value = (row.get("OBS_VALUE") or "").strip()
        if not value:
            continue
        out.setdefault(row["CURRENCY"], {})[date.fromisoformat(row["TIME_PERIOD"])] = Decimal(value)
    return out


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> str | None:
    """GET with the retry policy. None means the ECB answered 404 (no data)."""
    delays = (0, *OTHER_RETRY_DELAYS_SECONDS)
    last: str = "unknown error"
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(_jittered(delay))
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:  # timeouts, connection resets, DNS
            last = f"{type(exc).__name__}: {exc}"
            logger.warning("ECB attempt %d failed: %s", attempt, last)
            continue
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            last = f"HTTP {response.status_code}"
            logger.warning("ECB attempt %d failed: %s", attempt, last)
            continue
        if response.status_code >= 400:  # not retryable: our request is wrong
            raise FxServiceError(f"The ECB rejected the rate request (HTTP {response.status_code})")
        return response.text
    raise FxServiceError(
        f"The ECB exchange-rate service is not available right now ({last}); try again later"
    )


async def fetch_rate_to_inr(
    currency: str, on_date: date, client: httpx.AsyncClient | None = None
) -> FxRate:
    """INR per one unit of `currency` for `on_date` (latest earlier ECB day if none that day)."""
    code = (currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise FxRateNotFound(f"'{currency}' is not a valid 3-letter currency code")
    if code == BASE:
        return FxRate(BASE, on_date, on_date, Decimal(1), Decimal(1), None, source="identity")

    series = f"D.{BASE}.EUR.SP00.A" if code == "EUR" else f"D.{code}+{BASE}.EUR.SP00.A"
    params = {
        "startPeriod": (on_date - timedelta(days=FX_LOOKBACK_DAYS)).isoformat(),
        "endPeriod": on_date.isoformat(),
        "format": "csvdata",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=ECB_TIMEOUT_SECONDS)
    try:
        body = await _get(client, f"{ECB_API_URL}/{series}", params)
    finally:
        if owns_client:
            await client.aclose()

    data = _parse(body) if body else {}
    inr = data.get(BASE, {})
    foreign = inr if code == "EUR" else data.get(code, {})
    days = sorted((d for d in inr if code == "EUR" or d in foreign), reverse=True)
    if not days:
        # 404, an empty answer, or only one leg: the ECB has nothing to build the rate from.
        raise FxRateNotFound(
            f"No ECB rate for {code} on or in the {FX_LOOKBACK_DAYS} days before {on_date}: the "
            f"ECB may not publish {code}, or has not published rates for that period yet"
        )

    day = days[0]
    if code == "EUR":
        rate, eur_foreign = inr[day], None
    else:
        eur_foreign = foreign[day]
        rate = inr[day] / eur_foreign
    return FxRate(
        currency=code,
        requested_date=on_date,
        rate_date=day,
        inr_per_unit=rate.quantize(RATE_PLACES, rounding=ROUND_HALF_UP),
        eur_inr=inr[day],
        eur_foreign=eur_foreign,
    )
