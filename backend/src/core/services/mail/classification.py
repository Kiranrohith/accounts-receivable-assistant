"""What kind of mail is this, and where does it go. Two steps, the second pure so it can be tested without a model:

1. `pre_decide`: rules that need no model. A mail from an UNKNOWN sender with NO context (no known invoice
   number, no customer named) is general mail, ignored without spending a model call. A bare attachment (no
   subject, no text) from a KNOWN customer is read as a payment candidate: the document classifier decides what
   the attachment really is.
2. `classify_mail` asks the model; `decide` turns its verdict into a route:
   - a PO and a payment advice in one mail, or confidence under TRIAGE_HUMAN_THRESHOLD   -> a person (human triage)
   - general                                                                            -> ignored (AR/Ignored)
   - a purchase order with no usable attachment (body-only POs are out of scope)        -> a person
   - payment / dispute / both / purchase order, confident                              -> automatic
"""

from dataclasses import dataclass
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from core.services.mail.attachments import Qualification
from core.services.mail.context import MailContext
from core.services.mail.parse import ParsedMail
from data.models.postgres.enums import EmailClassification
from prompts.mail.triage import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from schemas.mail.triage import MailClass, MailClassification

__all__ = ["Decision", "ExtractionError", "Route", "classify_mail", "decide", "pre_decide"]

BODY_LIMIT = 6000
BARE_TEXT_LENGTH = 3  # under this many characters of subject AND of text: nothing was written
BARE_CONFIDENCE = 80


class Route(str, Enum):
    AUTO = "auto"  # confident: goes to its pipeline
    HUMAN = "human"  # a person routes it (the human triage queue)
    IGNORE = "ignore"  # general mail: AR/Ignored


@dataclass(frozen=True)
class Decision:
    route: Route
    mail_class: EmailClassification | None  # what it was taken for (the suggestion when a person decides)
    confidence: int | None
    reason: str


def _as_class(value: MailClass) -> EmailClassification:
    return EmailClassification(value.value)


def is_bare(mail: ParsedMail) -> bool:
    return len(mail.subject.strip()) < BARE_TEXT_LENGTH and len(mail.body.strip()) < BARE_TEXT_LENGTH


def pre_decide(mail: ParsedMail, context: MailContext, qualification: Qualification) -> Decision | None:
    """A verdict that needs no model, or None to ask it."""
    if not context.sender_known and not context.has_context:
        return Decision(
            Route.IGNORE, EmailClassification.GENERAL, 90,
            "The sender is not one of our customers and nothing in the mail refers to our invoices or customers",
        )
    if is_bare(mail) and qualification.qualifying:
        if context.sender_known:
            return Decision(
                Route.AUTO, EmailClassification.PAYMENT, BARE_CONFIDENCE,
                "A bare attachment from a known customer: read by the document classifier",
            )
        return Decision(
            Route.IGNORE, EmailClassification.GENERAL, 90,
            "A bare attachment from an unknown sender with nothing that refers to us",
        )
    return None


def decide(verdict: MailClassification, qualification: Qualification) -> Decision:
    mail_class = _as_class(verdict.mail_class)
    confident = verdict.confidence >= TRIAGE_HUMAN_THRESHOLD
    why = verdict.reason
    if verdict.mixed_po_and_payment:
        return Decision(Route.HUMAN, mail_class, verdict.confidence, f"A purchase order and a payment advice in one mail: a person routes it. {why}")
    if not confident:
        return Decision(Route.HUMAN, mail_class, verdict.confidence, f"Not sure enough ({verdict.confidence} < {TRIAGE_HUMAN_THRESHOLD}): {why}")
    if mail_class is EmailClassification.GENERAL:
        return Decision(Route.IGNORE, mail_class, verdict.confidence, why)
    if mail_class is EmailClassification.PURCHASE_ORDER and not qualification.qualifying:
        return Decision(
            Route.HUMAN, mail_class, verdict.confidence,
            f"A purchase order mail with no usable attachment (a PO is a structured document: body-only POs are out of scope). {why}",
        )
    return Decision(Route.AUTO, mail_class, verdict.confidence, why)


def _size(n: int) -> str:
    return f"{n // 1024} KB" if n >= 1024 else f"{n} bytes"


def describe_attachments(mail: ParsedMail, qualification: Qualification) -> str:
    if not mail.attachments:
        return "none"
    verdicts = {id(d.attachment): d for d in (*qualification.qualifying, *qualification.skipped, *qualification.rejected)}
    parts = []
    for a in mail.attachments:
        d = verdicts.get(id(a))
        outcome = f"{d.verdict.value}: {d.reason}" if d else "not checked"
        parts.append(f"{a.file_name} ({a.content_type or 'unknown type'}, {_size(len(a.data))}, {outcome})")
    return "; ".join(parts)


async def classify_mail(
    mail: ParsedMail, context: MailContext, qualification: Qualification, *, is_reply: bool
) -> tuple[MailClassification, str, int]:
    """(verdict, model used, attempts). Raises ExtractionError when every model failed."""
    body = mail.body.strip()
    prompt = USER_PROMPT_TEMPLATE.format(
        sender_known="yes" if context.sender_known else "no",
        invoice_numbers=", ".join(context.invoice_info or context.invoice_numbers) or "none",
        is_reply=(
            f"yes, in the thread of our dispute {context.thread.reference} (currently {context.thread.status})"
            if context.thread
            else "yes" if is_reply else "no"
        ),
        attachments=describe_attachments(mail, qualification),
        sender=f"{mail.sender_name + ' ' if mail.sender_name else ''}<{mail.sender or 'unknown'}>",
        subject=mail.subject or "(none)",
        body=(body[:BODY_LIMIT] + ("\n[... cut ...]" if len(body) > BODY_LIMIT else "")) or "(empty)",
    )
    return await run_structured_extraction(MailClassification, [SystemMessage(SYSTEM_PROMPT), HumanMessage(prompt)])
