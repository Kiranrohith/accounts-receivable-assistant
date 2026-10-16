"""Offline tests of the ECB client: a fake transport answers with ECB-shaped CSV."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from core.services.fx import ecb
from core.services.fx.ecb import FxRateNotFound, FxServiceError, fetch_rate_to_inr

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


HEADER = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"


def csv_rows(rows: list[tuple[str, str, str]]) -> str:
    return HEADER + "".join(
        f"EXR.D.{cur}.EUR.SP00.A,D,{cur},EUR,SP00,A,{day},{value},A\n" for cur, day, value in rows
    )


def client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):  # retries must not really wait in tests
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(ecb.asyncio, "sleep", instant)


async def test_inr_needs_no_call():
    def boom(_):  # noqa: ANN001
        raise AssertionError("no request expected for INR")

    r = await fetch_rate_to_inr("inr", date(2026, 10, 1), client(boom))
    assert r.inr_per_unit == 1 and r.days_back == 0 and r.source == "identity"


async def test_usd_is_a_cross_rate_through_the_euro():
    body = csv_rows([("INR", "2026-10-01", "100.0"), ("USD", "2026-10-01", "1.25")])
    r = await fetch_rate_to_inr("usd", date(2026, 10, 1), client(lambda _: httpx.Response(200, text=body)))
    assert r.inr_per_unit == Decimal("80.00000000")  # 100 / 1.25
    assert (r.eur_inr, r.eur_foreign, r.rate_date, r.days_back) == (Decimal("100.0"), Decimal("1.25"), date(2026, 10, 1), 0)


async def test_request_asks_for_both_legs_and_a_lookback_window():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["q"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, text=csv_rows([("INR", "2026-10-01", "100"), ("USD", "2026-10-01", "2")]))

    await fetch_rate_to_inr("USD", date(2026, 10, 1), client(handler))
    assert seen["path"].endswith("/D.USD+INR.EUR.SP00.A")
    assert seen["q"] == {"startPeriod": "2026-09-21", "endPeriod": "2026-10-01", "format": "csvdata"}


async def test_weekend_uses_latest_earlier_day_and_says_so():
    body = csv_rows([
        ("INR", "2026-10-08", "99"), ("USD", "2026-10-08", "1.1"),
        ("INR", "2026-10-09", "100"), ("USD", "2026-10-09", "1.25"),
    ])
    r = await fetch_rate_to_inr("USD", date(2026, 10, 11), client(lambda _: httpx.Response(200, text=body)))
    assert r.rate_date == date(2026, 10, 9) and r.days_back == 2 and r.inr_per_unit == Decimal("80.00000000")


async def test_both_legs_must_come_from_the_same_day():
    body = csv_rows([("INR", "2026-10-09", "100"), ("USD", "2026-10-08", "1.25")])
    with pytest.raises(FxRateNotFound):
        await fetch_rate_to_inr("USD", date(2026, 10, 9), client(lambda _: httpx.Response(200, text=body)))


async def test_eur_uses_the_inr_leg_directly():
    body = csv_rows([("INR", "2026-10-01", "108.5")])
    r = await fetch_rate_to_inr("EUR", date(2026, 10, 1), client(lambda _: httpx.Response(200, text=body)))
    assert r.inr_per_unit == Decimal("108.50000000") and r.eur_foreign is None


async def test_rate_is_rounded_to_8_places_half_up():
    body = csv_rows([("INR", "2026-10-01", "100"), ("USD", "2026-10-01", "3")])
    r = await fetch_rate_to_inr("USD", date(2026, 10, 1), client(lambda _: httpx.Response(200, text=body)))
    assert r.inr_per_unit == Decimal("33.33333333")


async def test_unknown_currency_404_is_not_retried():
    calls = []

    def handler(_):  # noqa: ANN001
        calls.append(1)
        return httpx.Response(404, text="No results found")

    with pytest.raises(FxRateNotFound, match="may not publish XYZ"):
        await fetch_rate_to_inr("XYZ", date(2026, 10, 1), client(handler))
    assert len(calls) == 1


async def test_bad_code_rejected_without_a_call():
    for bad in ("US", "USDD", "U1D", ""):
        with pytest.raises(FxRateNotFound):
            await fetch_rate_to_inr(bad, date(2026, 10, 1), client(lambda _: httpx.Response(500)))


async def test_5xx_is_retried_then_succeeds():
    body = csv_rows([("INR", "2026-10-01", "100"), ("USD", "2026-10-01", "2")])
    answers = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, text=body)])
    r = await fetch_rate_to_inr("USD", date(2026, 10, 1), client(lambda _: next(answers)))
    assert r.inr_per_unit == Decimal("50.00000000")


async def test_persistent_failure_raises_retryable_error_after_four_tries():
    calls = []

    def handler(_):  # noqa: ANN001
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(FxServiceError) as err:
        await fetch_rate_to_inr("USD", date(2026, 10, 1), client(handler))
    assert len(calls) == 4 and err.value.retryable and err.value.status_code == 502


async def test_timeouts_are_retried():
    def handler(_):  # noqa: ANN001
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(FxServiceError, match="not available"):
        await fetch_rate_to_inr("USD", date(2026, 10, 1), client(handler))


async def test_other_4xx_is_not_retried():
    calls = []

    def handler(_):  # noqa: ANN001
        calls.append(1)
        return httpx.Response(400, text="bad")

    with pytest.raises(FxServiceError, match="rejected"):
        await fetch_rate_to_inr("USD", date(2026, 10, 1), client(handler))
    assert len(calls) == 1


async def test_currency_the_ecb_does_not_publish_returns_only_the_inr_leg():
    body = csv_rows([("INR", "2026-10-01", "100")])  # what the ECB sends for AED: INR only
    with pytest.raises(FxRateNotFound, match="No ECB rate for AED"):
        await fetch_rate_to_inr("AED", date(2026, 10, 1), client(lambda _: httpx.Response(200, text=body)))


async def test_no_observations_in_window_is_not_found():
    with pytest.raises(FxRateNotFound, match="No ECB rate for USD"):
        await fetch_rate_to_inr("USD", date(2026, 10, 1), client(lambda _: httpx.Response(200, text=HEADER)))
