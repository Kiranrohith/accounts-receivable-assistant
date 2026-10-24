"""The invoice's total tax, when the reader left it empty.

Most invoices print one combined "Total tax" line. An invoice that itemises two or more taxes (CGST + SGST, or a cess on top
of IGST) often prints only the separate lines, so the reader, told never to calculate a number, returns the lines in
tax_breakdown and nothing in tax_total. The total is then the sum of those lines: plain addition of printed amounts, done
here and not by the model. A tax_total the document did print is never replaced.
"""

from decimal import Decimal

from schemas.invoices.extraction import ExtractedInvoice


def with_tax_total(extracted: ExtractedInvoice) -> ExtractedInvoice:
    """The extraction with tax_total set to the sum of tax_breakdown when it was empty. Returns the same object when
    nothing changes (a printed total, or no tax lines)."""
    if extracted.tax_total is not None or not extracted.tax_breakdown:
        return extracted
    return extracted.model_copy(update={"tax_total": sum((t.amount for t in extracted.tax_breakdown), Decimal(0))})
