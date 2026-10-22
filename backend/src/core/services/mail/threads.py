"""Is this mail the next message of a conversation we are already part of? (B7)

A customer answers OUR mail (a clarification, later a reminder), or goes on writing in the thread they started.
Three signals say which conversation, strongest first:
  1. the Message-ID of one of our own mails in its In-Reply-To / References headers,
  2. the Gmail thread it belongs to (the thread of a complaint we raised a dispute from),
  3. our dispute reference ("DSP-1A2B3C4D") that every mail we send carries in its subject.

What the link is for:
  - an unknown sender replying in our thread is not a stranger (it counts as context for triage);
  - a dispute reply joins that dispute (E2) or, when it is resolved, opens a linked one (E3);
  - a reply the classifier finds to be neither a dispute nor a payment is never dropped: it becomes a note on the
    conversation and its owner is told;
  - a payment or purchase order arriving in a dispute's thread leaves a note there, so the owner knows.
Reminder threads join this the same way once reminders are sent (they are one more lookup in `find_thread`).
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.services import notifications
from core.services.disputes import assignment, outbound
from core.services.mail.attachments import MailRef
from core.services.mail.parse import ParsedMail
from data.models.postgres import Dispute, IngestedEmail
from data.models.postgres.enums import DisputeMessageKind, NotificationType
from data.repositories.disputes import disputes_repo

EXCERPT = 1500  # characters of the customer's words kept on the dispute
_REFERENCE = re.compile(r"\bDSP-([0-9A-F]{8})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ThreadLink:
    dispute_id: uuid.UUID
    reference: str  # "DSP-1A2B3C4D"
    status: str
    invoice_id: uuid.UUID | None
    how: str  # which signal found it

    @property
    def describe(self) -> str:
        return f"{self.reference} ({self.status}), found by {self.how}"

    def as_json(self) -> dict:
        return {
            "dispute_id": str(self.dispute_id),
            "reference": self.reference,
            "status": self.status,
            "invoice_id": str(self.invoice_id) if self.invoice_id else None,
            "how": self.how,
        }


def _link(dispute: Dispute, how: str) -> ThreadLink:
    return ThreadLink(dispute.id, outbound.reference_for(dispute.id), dispute.status.value, dispute.invoice_id, how)


def message_ids_of(mail: ParsedMail) -> list[str]:
    """The Message-IDs this mail answers: In-Reply-To first, then the References chain, without repeats."""
    seen: dict[str, None] = {}
    for value in (mail.in_reply_to, *mail.references):
        for token in (value or "").split():
            seen.setdefault(token, None)
    return list(seen)


async def find_thread(db: AsyncSession, mail: ParsedMail, gmail_thread_id: str | None) -> ThreadLink | None:
    """The conversation of ours this mail continues, or None."""
    dispute = await disputes_repo.by_sent_message_ids(db, message_ids_of(mail))
    if dispute is not None:
        return _link(dispute, "a reply to our mail")
    if gmail_thread_id:
        dispute = await disputes_repo.latest_in_thread(db, gmail_thread_id)
        if dispute is not None:
            return _link(dispute, "the same mail thread")
    found = _REFERENCE.search(mail.subject or "")
    if found:
        dispute = await disputes_repo.by_reference(db, found.group(1))
        if dispute is not None:
            return _link(dispute, "the dispute reference in the subject")
    return None


async def note_reply(
    db: AsyncSession, link: ThreadLink, email: IngestedEmail, text: str, *, kind: DisputeMessageKind, excerpt: str = ""
) -> None:
    """Put a reply on the dispute's conversation and tell its current owner. Flushes only (the caller commits)."""
    dispute = await disputes_repo.get_dispute(db, link.dispute_id)
    if dispute is None:
        return
    mail_ref = MailRef(email.gmail_message_id)
    body = f"{text}\nFrom: {email.sender or 'unknown'}\nSubject: {email.subject or '(none)'}\nMail: {mail_ref.link}"
    if excerpt.strip():
        body += "\n\n" + excerpt.strip()[:EXCERPT]
    inbound = kind is DisputeMessageKind.INBOUND_EMAIL  # the customer's own words keep the mail's id (thread evidence)
    if inbound and await disputes_repo.message_exists(db, dispute.id, email.gmail_message_id):
        return
    await disputes_repo.insert_message(
        db,
        dispute_id=dispute.id,
        kind=kind,
        body=body,
        gmail_message_id=email.gmail_message_id if inbound else None,
        ingested_email_id=email.id,
    )
    if dispute.assigned_to is not None:
        await notifications.create(
            db,
            dispute.assigned_to,
            NotificationType.DISPUTE_FOLLOW_UP,
            f"A mail arrived in the thread of the dispute {await assignment.label(db, dispute)}: {text.rstrip('.')}",
            entity_type="dispute",
            entity_id=dispute.id,
        )


def reply_note(mail_kind: str) -> str:
    return {
        "payment": "The customer replied in this thread with a payment advice; it is being read as a payment.",
        "purchase_order": "The customer replied in this thread with a purchase order; it is being read as one.",
        "general": "The customer replied in this thread; nothing in it is a payment or a new complaint.",
    }[mail_kind]

