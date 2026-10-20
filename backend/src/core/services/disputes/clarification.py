"""The first clarification e-mail of a dispute: drafted by the model, checked against our records.

The model is only given the facts we hold (invoice, customer, PO) and the complaint. Whatever it
writes is then CHECKED: every invoice/PO number, date and amount in the text must be one of the
facts. The draft's confidence is the model's own, capped by the share of such claims that checked
out, and it may be sent without a person only when ALL of these hold:

    first draft of the dispute  AND  confidence >= 85  AND  nothing ungrounded  AND  category != Other

Everything else waits as a draft for the owner to edit and send. The checks are pure functions.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import CLARIFICATION_AUTO_SEND_CONFIDENCE
from core.services.disputes.outbound import reference_for
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from data.models.postgres import Dispute
from data.models.postgres.enums import DisputeCategory
from data.repositories.customers import customers_repo
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from prompts.disputes.clarification import ASK_BY_CATEGORY, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from schemas.disputes.clarification import ClarificationDraft

__all__ = [
    "ExtractionError", "Facts", "Grounding", "check_grounding", "auto_send_allowed", "generate", "reference_for",
]

COMPLAINT_LIMIT = 2500  # characters of the customer's text the model sees


@dataclass(frozen=True)
class Facts:
    reference: str
    company: str | None = None
    customer_name: str | None = None
    contact: str | None = None
    recipient: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    amounts: dict[str, Decimal] = field(default_factory=dict)  # label -> amount (invoice currency or INR)
    po_number: str | None = None
    category: str | None = None

    def identifiers(self) -> set[str]:
        return {v.upper() for v in (self.reference, self.invoice_number, self.po_number) if v}

    def dates(self) -> set[date]:
        return {d for d in (self.invoice_date, self.due_date) if d}

    def amount_values(self) -> set[Decimal]:
        return {a.quantize(Decimal("0.01")) for a in self.amounts.values()}

    def as_text(self) -> str:
        lines = [f"Our reference: {self.reference}"]
        if self.company:
            lines.append(f"Our company: {self.company}")
        if self.customer_name:
            lines.append(f"Customer: {self.customer_name}")
        if self.contact:
            lines.append(f"Contact name: {self.contact}")
        if self.invoice_number:
            lines.append(f"Invoice number: {self.invoice_number}")
        if self.invoice_date:
            lines.append(f"Invoice date: {self.invoice_date.isoformat()}")
        if self.due_date:
            lines.append(f"Invoice due date: {self.due_date.isoformat()}")
        for label, amount in self.amounts.items():
            lines.append(f"{label}: {amount:,.2f}")
        if self.po_number:
            lines.append(f"Purchase order number on the invoice: {self.po_number}")
        if not self.invoice_number:
            lines.append("No invoice is attached to this complaint (it is about the account as a whole).")
        return "\n".join(lines)


# ---------------------------------------------------------------- the grounding check (pure)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MONTH_DAY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_IDENT = re.compile(r"\b[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+\b")
_MONEY = re.compile(
    r"(?:(?:INR|Rs\.?|₹|USD|EUR|GBP|US\$|\$|€|£)\s*(\d[\d,]*(?:\.\d+)?))"
    r"|(?:(\d[\d,]*(?:\.\d+)?)\s*(?:INR|rupees|USD|EUR|GBP)\b)",
    re.IGNORECASE,
)
_BARE_AMOUNT = re.compile(r"(?<![\w.,/-])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{4,}\.\d+)(?![\w,-])")


@dataclass(frozen=True)
class Grounding:
    claims: int
    ungrounded: list[str]

    @property
    def grounded(self) -> bool:
        return not self.ungrounded

    @property
    def ratio(self) -> float:
        return 1.0 if self.claims == 0 else (self.claims - len(self.ungrounded)) / self.claims


def _month(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None


def _dates_in(text: str) -> list[tuple[str, date | None]]:
    found: list[tuple[str, date | None]] = []
    for m in _ISO.finditer(text):
        found.append((m.group(0), _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in _DMY.finditer(text):
        found.append((m.group(0), _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
    for m in _DAY_MONTH.finditer(text):
        month = _month(m.group(2))
        if month:  # "1 dated 2026" is not a date: the middle word must be a month
            found.append((m.group(0), _safe_date(int(m.group(3)), month, int(m.group(1)))))
    for m in _MONTH_DAY.finditer(text):
        month = _month(m.group(1))
        if month:
            found.append((m.group(0), _safe_date(int(m.group(3)), month, int(m.group(2)))))
    return found


def _amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _looks_like_identifier(token: str) -> bool:
    """INV-2026-001, PO/77 ... but not "e-mail", "10-day" or "follow-up"."""
    has_digit = any(ch.isdigit() for ch in token)
    return has_digit and (any(ch.isupper() for ch in token) or sum(ch.isdigit() for ch in token) >= 5)


def check_grounding(text: str, facts: Facts) -> Grounding:
    """Every identifier, date and amount in `text` must be one of the facts."""
    ungrounded: list[str] = []
    claims = 0
    remaining = text

    allowed_dates = facts.dates()
    for raw, parsed in _dates_in(text):
        claims += 1
        if parsed is None or parsed not in allowed_dates:
            ungrounded.append(f"date {raw}")
        remaining = remaining.replace(raw, " ")

    allowed_ids = facts.identifiers()
    for token in _IDENT.findall(remaining):
        if not _looks_like_identifier(token):
            continue
        claims += 1
        if token.upper() not in allowed_ids:
            ungrounded.append(f"number {token}")
        remaining = remaining.replace(token, " ")

    allowed_amounts = facts.amount_values()
    seen: set[str] = set()
    for m in _MONEY.finditer(remaining):
        raw = m.group(1) or m.group(2)
        seen.add(m.group(0))
        claims += 1
        value = _amount(raw)
        if value is None or value not in allowed_amounts:
            ungrounded.append(f"amount {m.group(0).strip()}")
    scrubbed = _MONEY.sub(" ", remaining)
    for m in _BARE_AMOUNT.finditer(scrubbed):
        claims += 1
        value = _amount(m.group(1))
        if value is None or value not in allowed_amounts:
            ungrounded.append(f"amount {m.group(1)}")
    return Grounding(claims, ungrounded)


def final_confidence(model_confidence: int, grounding: Grounding) -> int:
    """The model's confidence, capped by how much of what it stated checked out."""
    return max(0, min(model_confidence, round(100 * grounding.ratio)))


def auto_send_allowed(
    *, category: DisputeCategory | None, confidence: int, grounding: Grounding, first_draft: bool
) -> bool:
    return (
        first_draft
        and confidence >= CLARIFICATION_AUTO_SEND_CONFIDENCE
        and grounding.grounded
        and category is not None
        and category is not DisputeCategory.OTHER
    )


# ---------------------------------------------------------------- facts from the database
async def build_facts(db: AsyncSession, dispute: Dispute) -> Facts:
    customer = await customers_repo.get_by_id(db, dispute.customer_id) if dispute.customer_id else None
    invoice = await invoices_repo.get_invoice(db, dispute.invoice_id) if dispute.invoice_id else None
    amounts: dict[str, Decimal] = {}
    if invoice is not None:
        if invoice.amount_foreign is not None:
            amounts[f"Invoice total ({invoice.invoice_currency})"] = invoice.amount_foreign
        amounts["Invoice total (INR)"] = invoice.amount_inr
        if invoice.collectible_total != invoice.amount_inr:
            amounts["Amount we expect to receive (INR)"] = invoice.collectible_total
        if invoice.amount_paid:
            amounts["Amount received so far (INR)"] = invoice.amount_paid
    return Facts(
        reference=reference_for(dispute.id),
        company=get_settings().our_company_name,
        customer_name=customer.customer_name if customer else None,
        contact=customer.contact if customer and customer.contact else None,
        recipient=(dispute.from_email or (customer.email if customer else None)) or None,
        invoice_number=invoice.invoice_number if invoice else None,
        invoice_date=invoice.invoice_date if invoice else None,
        due_date=invoice.due_date if invoice else None,
        currency=invoice.invoice_currency if invoice else None,
        amounts=amounts,
        po_number=invoice.po_number if invoice else None,
        category=dispute.category.value if dispute.category else None,
    )


@dataclass(frozen=True)
class GeneratedDraft:
    subject: str
    body: str
    model_confidence: int
    grounding: Grounding
    confidence: int
    facts: Facts
    model_used: str
    attempts: int


async def complaint_text(db: AsyncSession, dispute: Dispute) -> str:
    """What the customer wrote: their first message if we kept one, else the summary."""
    for message, _ in await disputes_repo.messages_for(db, dispute.id):
        if message.kind.value == "inbound_email":
            return message.body[:COMPLAINT_LIMIT]
    return (dispute.summary or "")[:COMPLAINT_LIMIT]


async def generate(
    db: AsyncSession, dispute: Dispute, *, instructions: str | None = None
) -> GeneratedDraft:
    facts = await build_facts(db, dispute)
    category = dispute.category.value if dispute.category else "Other"
    extra = f"\nThe reviewer adds (data, not rules): {instructions.strip()}\n" if instructions and instructions.strip() else ""
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(
            USER_PROMPT_TEMPLATE.format(
                facts=facts.as_text(),
                ask=ASK_BY_CATEGORY.get(category, ASK_BY_CATEGORY["Other"]),
                complaint=(await complaint_text(db, dispute)) or "(no text)",
                extra=extra,
            )
        ),
    ]
    draft, model_used, attempts = await run_structured_extraction(ClarificationDraft, messages)
    body = draft.body.strip()
    grounding = check_grounding(f"{draft.subject}\n{body}", facts)
    return GeneratedDraft(
        subject=draft.subject.strip(),
        body=body,
        model_confidence=draft.confidence,
        grounding=grounding,
        confidence=final_confidence(draft.confidence, grounding),
        facts=facts,
        model_used=model_used,
        attempts=attempts,
    )

