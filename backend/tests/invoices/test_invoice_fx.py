"""When the rate service is used for an invoice, and when a rate is too old to use."""

from datetime import date
from decimal import Decimal

import pytest

from core.services.fx.ecb import FxRate, FxRateNotFound
from core.services.invoices import fx as invoice_fx

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_only_foreign_currencies_need_a_rate():
    assert not invoice_fx.is_foreign("INR") and not invoice_fx.is_foreign(" inr ")
    assert not invoice_fx.is_foreign(None) and not invoice_fx.is_foreign("")
    assert invoice_fx.is_foreign("USD") and invoice_fx.is_foreign("eur")


def _rate(days_back: int) -> FxRate:
    asked = date(2026, 10, 12)
    return FxRate("USD", asked, date.fromordinal(asked.toordinal() - days_back), Decimal("90"), Decimal("100"), Decimal("1.1"))


async def test_rate_up_to_the_limit_is_used(monkeypatch):
    async def fake(currency, on_date):  # noqa: ANN001
        return _rate(5)

    monkeypatch.setattr(invoice_fx, "fetch_rate_to_inr", fake)
    assert (await invoice_fx.rate_for_invoice("USD", date(2026, 10, 12))).days_back == 5


async def test_older_rate_is_refused_with_a_clear_message(monkeypatch):
    async def fake(currency, on_date):  # noqa: ANN001
        return _rate(6)

    monkeypatch.setattr(invoice_fx, "fetch_rate_to_inr", fake)
    with pytest.raises(FxRateNotFound, match="too old"):
        await invoice_fx.rate_for_invoice("USD", date(2026, 10, 12))
