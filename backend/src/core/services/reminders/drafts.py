"""The text of a reminder.

A draft is stored as "Subject: ..." on its first line, a blank line, then the text. The two figures that change
with time are written as markers, [[balance]] and [[days_past_due]]: they are filled in with the true values when the
draft is shown and again at the moment it is sent, so a person can edit the wording freely and still never send a
stale figure (D4). Everything else (the customer's name, the invoice number and dates) is plain text.
"""

import re
import uuid
from datetime import date
from decimal import Decimal

from constants.constants import REMINDER_TEMPLATES
from core.services.invoices.amounts import format_money, money
from core.services.mail import templates
from data.models.postgres import Customer, Invoice

SUBJECT = "Subject: "
BALANCE, DAYS = "[[balance]]", "[[days_past_due]]"
_MARKER = re.compile(r"\[\[(balance|days_past_due)\]\]")


def reference_for(reminder_id: uuid.UUID) -> str:
    return "REM-" + str(reminder_id)[:8].upper()


def balance_text(invoice: Invoice) -> str:
    """What is still to be paid, in the currency the customer was invoiced in."""
    balance = money(invoice.collectible_balance)
    if invoice.invoice_currency != "INR" and invoice.fx_rate_invoice:
        return format_money(balance / invoice.fx_rate_invoice, invoice.invoice_currency)
    return format_money(balance, "INR")


def figures(invoice: Invoice, today: date) -> dict[str, str]:
    return {"balance": balance_text(invoice), "days_past_due": str((today - invoice.due_date).days)}


def fill(text: str, values: dict[str, str]) -> str:
    return _MARKER.sub(lambda m: values[m.group(1)], text)


def compose(invoice: Invoice, customer: Customer, tier: int) -> str:
    """The first draft of a reminder at this tier: the tier's template with the live figures left as markers."""
    rendered = templates.render_email(
        REMINDER_TEMPLATES[tier],
        contact_name=customer.contact,
        customer_name=customer.customer_name,
        invoice_number=invoice.invoice_number,
        invoice_date=f"{invoice.invoice_date:%d %b %Y}",
        due_date=f"{invoice.due_date:%d %b %Y}",
        balance=BALANCE,
        days_past_due=DAYS,
    )
    return f"{SUBJECT}{rendered.subject}\n\n{rendered.body}"


def split(draft: str) -> tuple[str, str]:
    """(subject, body) of a stored draft."""
    first, _, rest = draft.partition("\n")
    if first.startswith(SUBJECT):
        return first[len(SUBJECT):].strip(), rest.lstrip("\n")
    return "", draft


def join(subject: str, body: str) -> str:
    return f"{SUBJECT}{' '.join(subject.split())}\n\n{body.strip()}\n"


def with_reference(subject: str, reminder_id: uuid.UUID) -> str:
    ref = reference_for(reminder_id)
    return subject if ref in subject else f"{subject} [{ref}]"


def price(value: Decimal) -> str:  # for the history shown to people
    return format_money(value, "INR")
