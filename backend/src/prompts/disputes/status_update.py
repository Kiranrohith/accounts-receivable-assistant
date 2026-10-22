"""The status update e-mailed to a customer when their dispute is escalated. Plain text, no model:
it states only our reference and the invoice number, never a date, an amount or a promise."""

SUBJECT = "An update on your query"

BODY_WITH_INVOICE = """\
Hello,

We are writing about your query on invoice {invoice_number}. We have not forgotten it: it has \
now been passed to a senior member of our accounts team, who will look at it with priority and \
get in touch with you. You can reply to this e-mail if you have anything to add.

Thank you for your patience.

Accounts Receivable{company}"""

BODY_GENERAL = """\
Hello,

We are writing about your recent query. We have not forgotten it: it has now been passed to a \
senior member of our accounts team, who will look at it with priority and get in touch with \
you. You can reply to this e-mail if you have anything to add.

Thank you for your patience.

Accounts Receivable{company}"""


def render(invoice_number: str | None, company: str | None) -> tuple[str, str]:
    tail = f"\n{company}" if company else ""
    body = (BODY_WITH_INVOICE if invoice_number else BODY_GENERAL).format(
        invoice_number=invoice_number or "", company=tail
    )
    return SUBJECT, body
