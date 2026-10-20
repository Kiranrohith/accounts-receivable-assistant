"""Invoice status from the money on it. Pure."""

from decimal import Decimal

from data.models.postgres.enums import InvoiceStatus


def invoice_status_after(amount_paid: Decimal, collectible_total: Decimal) -> InvoiceStatus:
    """Open with nothing paid, Paid once fully paid, otherwise PartlyPaid."""
    if amount_paid <= 0:
        return InvoiceStatus.OPEN
    if amount_paid >= collectible_total:
        return InvoiceStatus.PAID
    return InvoiceStatus.PARTLY_PAID
