"""What our own records say about a mail, found without a model: is the sender a customer, does the mail quote one
of our invoices, does it name one of our customers. This is the "context" of the design rules: a mail from an
unknown sender with NO context is general mail; a bare attachment from a known customer is worth reading.
"""

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.mail import threads
from core.services.mail.parse import ParsedMail
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.invoices.numbers import normalize_invoice_no

# something that looks like an invoice number: a letter prefix, an optional separator, then digits ("INV-1176",
# "inv 1157", "DN2001"). A bare number never counts: the prefix is part of the number.
_CANDIDATE = re.compile(r"[A-Za-z]{1,8}[\s/_-]?\d{3,}(?:[-/]\d+)*")
# words a payment advice uses; an unknown sender whose mail or attachment names carry them is worth a look
_PAYMENT_WORDS = re.compile(
    r"(?<![a-z])(payment|paid|remit[a-z]*|neft|rtgs|imps|utr|upi|swift|mt103|cheque|advice|transfer(?:red)?)(?![a-z])",
    re.IGNORECASE,  # a letter either side would make it part of another word; "_" and digits are not letters
)
_MAX_CANDIDATES = 60
_MIN_NAME_LENGTH = 6  # a shorter normalized name would match inside unrelated words


@dataclass(frozen=True)
class MailContext:
    sender_known: bool
    invoice_numbers: tuple[str, ...]  # OUR invoice numbers the mail quotes, as stored
    customer_named: bool  # a customer's name or alias appears in the subject, text or an attachment name
    invoice_info: tuple[str, ...] = ()  # "INV-1031 (Paid)": the quoted invoices with their status in our records
    payment_wording: bool = False  # the subject, text or an attachment name talks like a payment advice
    thread: threads.ThreadLink | None = None  # the conversation of ours this mail continues

    @property
    def has_context(self) -> bool:
        return self.sender_known or bool(self.invoice_numbers) or self.customer_named or self.payment_wording or self.thread is not None


def candidate_numbers(*texts: str) -> list[str]:
    """Normalized invoice-number candidates, in order of appearance, without repeats."""
    seen: dict[str, None] = {}
    for text in texts:
        for match in _CANDIDATE.finditer(text or ""):
            seen.setdefault(normalize_invoice_no(match.group()), None)
            if len(seen) >= _MAX_CANDIDATES:
                return list(seen)
    return list(seen)


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


async def find_context(db: AsyncSession, mail: ParsedMail, gmail_thread_id: str | None = None) -> MailContext:
    file_names = " ".join(a.file_name for a in mail.attachments)
    sender_known = bool(mail.sender) and bool(await customers_repo.get_by_email_or_alias(db, mail.sender))
    numbers = candidate_numbers(mail.subject, mail.body[:6000], file_names)
    invoices = await invoices_repo.get_by_numbers(db, numbers)
    text = _squash(f"{mail.subject} {mail.body[:6000]} {file_names}")
    names = [c.customer_name for c in await customers_repo.list_active_customers(db)]
    names += [value for _, value in await customers_repo.list_name_aliases(db)]
    named = any(len(key) >= _MIN_NAME_LENGTH and key in text for key in map(_squash, names))
    unique = list({i.invoice_number: i for i in invoices}.values())
    return MailContext(
        sender_known,
        tuple(i.invoice_number for i in unique),
        named,
        tuple(f"{i.invoice_number} ({i.status.value})" for i in unique),
        bool(_PAYMENT_WORDS.search(f"{mail.subject} {mail.body[:2000]} {file_names}")),
        await threads.find_thread(db, mail, gmail_thread_id),
    )
