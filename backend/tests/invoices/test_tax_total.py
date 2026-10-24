"""The invoice's total tax is the sum of its tax lines when the document printed no combined figure."""

from decimal import Decimal

from core.services.invoices.tax import with_tax_total
from schemas.invoices.extraction import ExtractedInvoice, TaxLine


def _invoice(tax_total, *lines: str) -> ExtractedInvoice:
    return ExtractedInvoice(
        tax_total=tax_total,
        tax_breakdown=[TaxLine(type=f"T{n}", rate=Decimal(9), amount=Decimal(amount)) for n, amount in enumerate(lines)],
    )


def test_two_taxes_without_a_printed_total_are_added_up():
    result = with_tax_total(_invoice(None, "900.00", "900.00"))
    assert result.tax_total == Decimal("1800.00")


def test_three_taxes_are_added_up():
    assert with_tax_total(_invoice(None, "100.10", "50.05", "20.00")).tax_total == Decimal("170.15")


def test_a_single_tax_line_is_its_own_total():
    assert with_tax_total(_invoice(None, "1800.00")).tax_total == Decimal("1800.00")


def test_a_printed_total_is_never_replaced():
    invoice = _invoice(Decimal("1800.00"), "900.00", "850.00")
    assert with_tax_total(invoice) is invoice


def test_a_printed_zero_total_is_kept():
    invoice = _invoice(Decimal(0), "900.00")
    assert with_tax_total(invoice).tax_total == Decimal(0)


def test_no_tax_lines_leaves_the_total_empty():
    invoice = _invoice(None)
    assert with_tax_total(invoice) is invoice
    assert invoice.tax_total is None
