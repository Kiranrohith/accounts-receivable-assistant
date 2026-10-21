"""Sending a drafted message to the customer, and what that does to the dispute.

A draft's subject lives on the first line of its body ("Subject: ..."), so the table needs no extra
column. Sending turns that same row into a `sent` message (one row per outgoing mail), records the
mail's own Message-ID, stamps the first response, and moves Assigned / Categorised on to
ClarificationSent (an Escalated dispute stays Escalated). When the mail cannot be sent the draft
stays a draft, the owner gets a SendFailure notice, and the response clock simply keeps running.

The caller holds the dispute's row lock. Both outcomes are committed here.
"""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import ExternalServiceError, ValidationFailure
from core.services import notifications
from core.services.disputes import assignment
from core.services.mail import smtp
from data.models.postgres import Dispute, DisputeMessage
from data.models.postgres.enums import DisputeMessageKind, DisputeStatus, NotificationType
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo

_SUBJECT = "Subject: "
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def reference_for(dispute_id: uuid.UUID) -> str:
    return "DSP-" + str(dispute_id)[:8].upper()


def compose(subject: str, text: str) -> str:
    return f"{_SUBJECT}{' '.join(subject.split())}\n\n{text.strip()}"


def split(body: str) -> tuple[str | None, str]:
    """(subject, text) of a stored message body; subject is None when it has none."""
    first, _, rest = body.partition("\n")
    if first.startswith(_SUBJECT):
        return first[len(_SUBJECT):].strip(), rest.lstrip("\n")
    return None, body


def subject_with_reference(subject: str, reference: str) -> str:
    subject = subject.strip() or "Your query"
    return subject if reference in subject else f"{subject} [{reference}]"


async def recipient_for(db: AsyncSession, dispute: Dispute) -> str | None:
    if dispute.from_email and _EMAIL.match(dispute.from_email.strip()):
        return dispute.from_email.strip()
    customer = await customers_repo.get_by_id(db, dispute.customer_id) if dispute.customer_id else None
    if customer and customer.email and _EMAIL.match(customer.email.strip()):
        return customer.email.strip()
    return None


async def send_draft(
    db: AsyncSession, dispute: Dispute, message: DisputeMessage, *, user_id: uuid.UUID | None
) -> str:
    """Send `message` (a draft) and record it. Returns the address it went to.
    Raises ValidationFailure when there is nobody to send to (nothing changes) and
    ExternalServiceError when the mail server would not take it (the owner is told)."""
    to = await recipient_for(db, dispute)
    if to is None:
        raise ValidationFailure(
            "There is no valid e-mail address to send this to (no sender address, and the customer has none)"
        )
    subject, text = split(message.body)
    final_subject = subject_with_reference(subject or "Your query", reference_for(dispute.id))
    mail = smtp.build_message(to=to, subject=final_subject, body=text, sender=get_settings().smtp_from)
    try:
        sent = await smtp.send(mail)
    except smtp.SmtpError as exc:
        if dispute.assigned_to is not None:
            await notifications.create(
                db,
                dispute.assigned_to,
                NotificationType.SEND_FAILURE,
                f"Could not send the e-mail for dispute {await assignment.label(db, dispute)}: "
                f"{exc.message}. It stays a draft"
                + ("." if dispute.status is DisputeStatus.RESOLVED else "; the response deadline keeps running."),
                entity_type="dispute",
                entity_id=dispute.id,
                actor=user_id,
            )
        await unit_of_work.commit(db)
        raise ExternalServiceError(f"The e-mail could not be sent: {exc.message}") from exc

    now = datetime.now(UTC)
    message.kind = DisputeMessageKind.SENT
    message.sent_at = now
    message.gmail_message_id = sent.message_id
    message.body = compose(final_subject, text)
    if dispute.status is DisputeStatus.RESOLVED:  # the resolution e-mail: not a first response
        assignment.log_state(db, dispute, dispute.status, f"Resolution e-mail sent to {to}", user_id=user_id)
    elif dispute.first_response_at is None:
        dispute.first_response_at = now
    if dispute.status in (DisputeStatus.ASSIGNED, DisputeStatus.CATEGORISED):
        before = dispute.status
        dispute.status = DisputeStatus.CLARIFICATION_SENT
        assignment.log_state(db, dispute, before, f"Clarification sent to {to}", user_id=user_id)
    await unit_of_work.commit(db)
    return to
