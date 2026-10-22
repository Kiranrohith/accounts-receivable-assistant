"""E9: a mail that went to the wrong pipeline is sent to the other one by a person, in one click, both ways.

  payment -> dispute   the mail was a complaint. Its payment jobs are withdrawn and a payment still under review is
                       rejected, with the reason. A payment that already holds money is NOT undone silently: the
                       person reverses it first (the money screens), then reroutes.
  dispute -> payment   the mail was a payment advice. Its disputes are withdrawn (closed with no e-mail to the customer,
                       their holds released, their owners told) and a follow-up it added to another dispute becomes an
                       internal note there. A complaint that already had a reply sent to the customer, was escalated or
                       was resolved is real work: it stays, and the person resolves it the usual way.

Everything is checked before anything changes, and the whole change commits together with the new hand-off, so a
refused reroute leaves the mail as it was. Only these two classes are swapped; a mail the classifier called `general`
is pulled out of the ignored list with the ordinary routing action.
"""

import hashlib
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError
from core.services.disputes import workflow
from core.services.jobs import queue
from core.services.mail import handoff
from core.services.mail.classification import Decision, Route
from core.services.mail.triage import apply_decision
from core.services.payments import review
from data.models.postgres import IngestedEmail, User
from data.models.postgres.enums import (
    DisputeMessageKind,
    DisputeStatus,
    DocKind,
    EmailClassification,
    EmailStatus,
    PaymentState,
)
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import documents_repo
from data.repositories.mail import emails_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo
from schemas.mail.triage import RerouteMail, RerouteResult

logger = logging.getLogger(__name__)

PAYMENT, DISPUTE = EmailClassification.PAYMENT, EmailClassification.DISPUTE
TARGETS = {PAYMENT: DISPUTE, DISPUTE: PAYMENT}
_MONEY = (PaymentState.APPLIED, PaymentState.PARTIALLY_APPLIED, PaymentState.UNAPPLIED, PaymentState.HELD)
_IN_FLIGHT = (PaymentState.RECEIVED, PaymentState.READING, PaymentState.MATCHING)
_WORKED = (DisputeStatus.RESOLVED, DisputeStatus.ESCALATED, DisputeStatus.CLARIFICATION_SENT)


def can_reroute_to(email: IngestedEmail) -> str | None:
    """The class this mail can be sent to, or None (only a mail handled as a payment or a dispute can be swapped)."""
    if email.status is EmailStatus.PROCESSED and email.classification in TARGETS:
        return TARGETS[email.classification].value
    return None


async def _withdraw_jobs(db: AsyncSession, entity_ids: list[uuid.UUID], reason: str, what: str) -> None:
    for job in await jobs_repo.open_jobs_for(db, entity_ids):
        if not await queue.cancel(db, job.id, reason):
            raise ConflictError(f"{what} is being read right now: try again in a moment")


async def _undo_payments(db: AsyncSession, user: User, email: IngestedEmail, why: str) -> list[str]:
    payments = await payments_repo.payments_of_mail(db, email.id)
    for payment in payments:
        short = str(payment.id)[:8]
        if payment.state in _MONEY:
            raise ConflictError(
                f"Payment {short} from this mail is {payment.state.value}: its money is already recorded. "
                "Reverse the payment first, then reroute the mail"
            )
        if payment.state in _IN_FLIGHT:
            raise ConflictError(f"Payment {short} from this mail is still being processed: try again in a moment")
    documents = await documents_repo.documents_of_mail(db, email.id, DocKind.PAYMENT)
    await _withdraw_jobs(db, [d.id for d in documents], why, "A payment of this mail")
    done = []
    for payment in payments:
        if payment.state is PaymentState.NEEDS_REVIEW:
            locked = await payments_repo.lock_payment(db, payment.id)
            if locked is not None and locked.state is PaymentState.NEEDS_REVIEW:
                await review.mark_rejected(db, locked, user, why)
                done.append(f"payment {str(locked.id)[:8]} rejected")
    return done


async def _undo_disputes(db: AsyncSession, user: User, email: IngestedEmail, why: str) -> list[str]:
    own = await disputes_repo.disputes_of_mail(db, email.id)
    for dispute in own:
        short = f"DSP-{str(dispute.id)[:8].upper()}"
        if dispute.status in _WORKED:
            raise ConflictError(
                f"Dispute {short} from this mail is {dispute.status.value}: it has been worked on, so it stays. "
                "Resolve it the usual way"
            )
        messages = [m for m, _ in await disputes_repo.messages_for(db, dispute.id)]
        if any(m.kind is DisputeMessageKind.SENT for m in messages):
            raise ConflictError(f"An e-mail for dispute {short} has already gone to the customer: resolve it the usual way")
    documents = await documents_repo.documents_of_mail(db, email.id, DocKind.DISPUTE)
    await _withdraw_jobs(db, [d.id for d in documents] + [d.id for d in own], why, "A dispute of this mail")
    done = []
    for dispute in own:
        await workflow.withdraw(db, user, dispute.id, why)
        done.append(f"dispute DSP-{str(dispute.id)[:8].upper()} withdrawn")
    own_ids = {d.id for d in own}
    for message in await disputes_repo.messages_of_mail(db, email.id):  # a follow-up this mail added to another dispute
        if message.dispute_id not in own_ids and message.kind is DisputeMessageKind.INBOUND_EMAIL:
            message.kind = DisputeMessageKind.INTERNAL_NOTE
            message.body = f"Not a customer message about this dispute ({why}):\n{message.body}"
            done.append(f"its follow-up on dispute DSP-{str(message.dispute_id)[:8].upper()} became an internal note")
    return done


async def _check_attachments_unread(db: AsyncSession, email: IngestedEmail) -> None:
    """Going back to `payment` after a payment of this very file was read and rejected cannot read it a second time."""
    _, qualification = handoff.load_mail(email)
    for decision in qualification.qualifying:
        same = await documents_repo.document_id_by_hash(db, hashlib.sha256(decision.attachment.data).hexdigest())
        original = await documents_repo.get_document(db, same) if same else None
        if original is not None and original.ingested_email_id == email.id:
            raise ConflictError(
                f"{decision.attachment.file_name} was already read from this mail once. Review that payment, "
                "or upload the file by hand"
            )


async def reroute(db: AsyncSession, user: User, email_id: uuid.UUID, body: RerouteMail) -> RerouteResult:
    email = await emails_repo.lock_email(db, email_id)
    if email is None:
        raise NotFoundError("Mail not found")
    current = email.classification
    if can_reroute_to(email) is None:
        raise ConflictError(
            "Only a mail already handled as a payment or as a dispute can be swapped between them "
            "(a mail waiting in the queue, or in the ignored list, is routed with the ordinary routing action)"
        )
    target = EmailClassification(body.mail_class)
    if target is current:
        raise ConflictError(f"This mail is already handled as a {current.value}")
    note = f" ({body.note})" if body.note else ""
    why = f"the mail was rerouted from {current.value} to {target.value} by {user.username}{note}"
    if target is PAYMENT:
        await _check_attachments_unread(db, email)
    undone = await (_undo_payments if current is PAYMENT else _undo_disputes)(db, user, email, why)

    old_labels = handoff.LABELS[current]
    apply_decision(db, email, Decision(Route.AUTO, target, 100, f"rerouted from {current.value} by {user.username}{note}"), by=f"Person ({user.username})")
    email.updated_by = user.id
    handed = await handoff.hand_off(db, email, target, created_by=user.id)
    await unit_of_work.commit(db)
    await handoff.unlabel_all(email, old_labels)
    await handoff.label_all(email, handed.labels)
    return RerouteResult(
        email_id=email.id,
        mail_class=target.value,
        undone=undone,
        message=f"Rerouted from {current.value} to {target.value}: {len(handed.jobs)} job(s) queued"
        + (f"; skipped: {'; '.join(handed.skipped)}" if handed.skipped else "."),
    )
