"""Is this mail the next message of a conversation we are already part of? (B7)

A customer answers OUR mail (a dispute clarification, a payment reminder), or goes on writing in the thread they
started. Three signals say which conversation, strongest first:
  1. the Message-ID of one of our own mails in its In-Reply-To / References headers,
  2. the Gmail thread it belongs to (the thread of a complaint we raised a dispute from; the thread of a reminder, once a
     first reply has taught us which it is),
  3. our reference that every mail we send carries in its subject: "DSP-1A2B3C4D" (dispute), "REM-1A2B3C4D" (reminder).

What the link is for:
  - an unknown sender replying in our thread is not a stranger (it counts as context for triage);
  - a dispute reply joins that dispute (E2) or, when it is resolved, opens a linked one (E3);
  - a reply the classifier finds to be neither a dispute nor a payment is never dropped: it becomes a note on the
    conversation (on the dispute, or on the invoice's history for a reminder) and the person who owns it is told;
  - a payment, purchase order or complaint arriving in a reminder's thread, or a payment or purchase order in a
    dispute's thread, leaves a note there, so the person knows what it was about.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.services import notifications
from core.services.disputes import assignment, outbound
from core.services.mail import sending
from core.services.mail.attachments import MailRef
from core.services.mail.parse import ParsedMail
from core.services.reminders import drafts as reminder_drafts
from core.services.reminders import lifecycle as reminder_lifecycle
from data.models.postgres import Dispute, IngestedEmail, Reminder
from data.models.postgres.enums import DisputeMessageKind, NotificationType
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from data.repositories.reminders import reminders_repo

EXCERPT = 1500  # characters of the customer's words kept on the conversation
_REFERENCE = re.compile(r"\bDSP-([0-9A-F]{8})\b", re.IGNORECASE)
_REMINDER_REFERENCE = re.compile(r"\bREM-([0-9A-F]{8})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ThreadLink:
    dispute_id: uuid.UUID | None
    reference: str  # "DSP-1A2B3C4D" or "REM-1A2B3C4D"
    status: str
    invoice_id: uuid.UUID | None
    how: str  # which signal found it
    kind: str = "dispute"  # "dispute" | "reminder"
    reminder_id: uuid.UUID | None = None

    @property
    def what(self) -> str:
        noun = "payment reminder" if self.kind == "reminder" else "dispute"
        return f"our {noun} {self.reference} (currently {self.status})"

    @property
    def describe(self) -> str:
        return f"{self.reference} ({self.status}), found by {self.how}"

    def as_json(self) -> dict:
        return {
            "kind": self.kind,
            "dispute_id": str(self.dispute_id) if self.dispute_id else None,
            "reminder_id": str(self.reminder_id) if self.reminder_id else None,
            "reference": self.reference,
            "status": self.status,
            "invoice_id": str(self.invoice_id) if self.invoice_id else None,
            "how": self.how,
        }


def _link(dispute: Dispute, how: str) -> ThreadLink:
    return ThreadLink(dispute.id, outbound.reference_for(dispute.id), dispute.status.value, dispute.invoice_id, how)


def _reminder_link(reminder: Reminder, how: str) -> ThreadLink:
    return ThreadLink(
        None, reminder_drafts.reference_for(reminder.id), reminder.state.value, reminder.invoice_id, how,
        kind="reminder", reminder_id=reminder.id,
    )


def message_ids_of(mail: ParsedMail) -> list[str]:
    """The Message-IDs this mail answers: In-Reply-To first, then the References chain, without repeats."""
    seen: dict[str, None] = {}
    for value in (mail.in_reply_to, *mail.references):
        for token in (value or "").split():
            seen.setdefault(token, None)
    return list(seen)


async def find_thread(db: AsyncSession, mail: ParsedMail, gmail_thread_id: str | None) -> ThreadLink | None:
    """The conversation of ours this mail continues, or None."""
    ids = message_ids_of(mail)
    dispute = await disputes_repo.by_sent_message_ids(db, ids)
    if dispute is not None:
        return _link(dispute, "a reply to our mail")
    reminder = await reminders_repo.by_message_ids(db, ids)
    if reminder is not None:
        return _reminder_link(reminder, "a reply to our mail")
    if gmail_thread_id:
        dispute = await disputes_repo.latest_in_thread(db, gmail_thread_id)
        if dispute is not None:
            return _link(dispute, "the same mail thread")
        reminder = await reminders_repo.by_thread(db, gmail_thread_id)
        if reminder is not None:
            return _reminder_link(reminder, "the same mail thread")
    found = _REFERENCE.search(mail.subject or "")
    if found:
        dispute = await disputes_repo.by_reference(db, found.group(1))
        if dispute is not None:
            return _link(dispute, "the dispute reference in the subject")
    found = _REMINDER_REFERENCE.search(mail.subject or "")
    if found:
        reminder = await reminders_repo.by_reference(db, found.group(1))
        if reminder is not None:
            return _reminder_link(reminder, "the reminder reference in the subject")
    return None


async def note_reply(
    db: AsyncSession, link: ThreadLink, email: IngestedEmail, text: str, *, kind: DisputeMessageKind, excerpt: str = ""
) -> None:
    """Put a reply on its conversation and tell the person who owns it. Flushes only (the caller commits)."""
    mail_ref = MailRef(email.gmail_message_id)
    body = f"{text}\nFrom: {email.sender or 'unknown'}\nSubject: {email.subject or '(none)'}\nMail: {mail_ref.link}"
    if excerpt.strip():
        body += "\n\n" + excerpt.strip()[:EXCERPT]
    if link.kind == "reminder":
        await _note_on_reminder(db, link, email, text, body)
        return
    dispute = await disputes_repo.get_dispute(db, link.dispute_id)
    if dispute is None:
        return
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


async def _note_on_reminder(db: AsyncSession, link: ThreadLink, email: IngestedEmail, text: str, body: str) -> None:
    """A reply to a reminder: a line on the invoice's history, the thread remembered, the approver told."""
    reminder = await reminders_repo.get_reminder(db, link.reminder_id)
    invoice = await invoices_repo.get_invoice(db, link.invoice_id) if link.invoice_id else None
    if reminder is None or invoice is None:
        return
    if reminder.gmail_thread_id is None and email.gmail_thread_id:
        reminder.gmail_thread_id = email.gmail_thread_id  # later mails in this Gmail thread are then found at once
    reminder_lifecycle.timeline(db, invoice, body)
    people = [reminder.handled_by] if reminder.handled_by else await sending.admin_ids(db)
    for person in dict.fromkeys(people):
        await notifications.create(
            db,
            person,
            NotificationType.REMINDER_REPLY,
            f"Invoice {invoice.invoice_number}: {text.rstrip('.')}",
            entity_type="reminder",
            entity_id=reminder.id,
        )


def reply_note(mail_kind: str, link: ThreadLink | None = None) -> str:
    if link is not None and link.kind == "reminder":
        where = "our payment reminder"
        return {
            "payment": f"The customer replied to {where} with a payment advice; it is being read as a payment.",
            "purchase_order": f"The customer replied to {where} with a purchase order; it is being read as one.",
            "dispute": f"The customer replied to {where} with a complaint; it is being read as one.",
            "both": f"The customer replied to {where} with a payment and a complaint; both are being read.",
            "general": f"The customer replied to {where}; nothing in it is a payment or a complaint.",
        }[mail_kind]
    return {
        "payment": "The customer replied in this thread with a payment advice; it is being read as a payment.",
        "purchase_order": "The customer replied in this thread with a purchase order; it is being read as one.",
        "general": "The customer replied in this thread; nothing in it is a payment or a new complaint.",
    }[mail_kind]
