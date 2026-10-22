"""Recording a complaint. One entry for every source (an uploaded complaint, a person typing it in).

  E1  a complaint about the account as a whole has no invoice (customer-level)
  E2  a reply in the mail thread of an OPEN dispute is added to it as a message, and the dispute's
      current owner gets a DisputeFollowUp notice
  E3  a reply in the thread of a Resolved dispute opens a NEW dispute linked to it
  E10 anything else is its own dispute, even when the invoice already has others: only the thread
      joins disputes. Nothing is merged, linked or shared between them (their deadlines are
      independent); the other disputes on the invoice are SHOWN as "possibly related" when read
      (queries.related_disputes) and counted in the assignment notice.

A category with enough confidence (>= DISPUTE_CLASSIFY_THRESHOLD) is accepted: the dispute is
Categorised and handed to the least-loaded person (Assigned). Anything less waits in the classify
queue (Raised), keeping the suggested category for the person to confirm or change. The caller can
also insist on the queue (`hold_in_queue`): a complaint whose customer is not identified needs a
person whatever the category.
It flushes only; the caller commits, so a notice never outlives a rolled-back dispute.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import DISPUTE_CLASSIFY_THRESHOLD
from core.exceptions.errors import NotFoundError, ValidationFailure
from core.services import notifications
from core.services.disputes import assignment, calendar
from core.services.jobs import queue
from data.models.postgres import Dispute
from data.models.postgres.enums import DisputeCategory, DisputeMessageKind, DisputeStatus, NotificationType
from data.repositories.customers import customers_repo
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo


@dataclass
class Created:
    dispute: Dispute
    appended: bool  # True: an open dispute existed and the complaint became a message on it


def is_confident(category: DisputeCategory | None, confidence: int | None) -> bool:
    return category is not None and confidence is not None and confidence >= DISPUTE_CLASSIFY_THRESHOLD


async def categorise_and_assign(
    db: AsyncSession, dispute: Dispute, *, actor: uuid.UUID | None, reason: str
) -> None:
    """Raised -> Categorised -> Assigned (the owner is told). Without anyone to own it, stays Categorised."""
    before = dispute.status
    dispute.status = DisputeStatus.CATEGORISED
    assignment.log_state(db, dispute, before, reason, user_id=actor)
    owner = await assignment.pick_owner(db, escalated=False)
    if owner is None:
        return
    dispute.assigned_to = owner.id
    before = dispute.status
    dispute.status = DisputeStatus.ASSIGNED
    assignment.log_state(db, dispute, before, f"Assigned to {owner.username}", user_id=actor)
    await assignment.tell_owner(db, dispute, owner, await assignment.label(db, dispute), actor=actor)
    # the first clarification is written (and maybe sent) by a worker, never inside this request
    await queue.enqueue(
        db,
        job_type=queue.JOB_DISPUTE_CLARIFY,
        entity_type=queue.ENTITY_DISPUTE,
        entity_id=dispute.id,
        created_by=actor,
    )


async def create_dispute(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    invoice_id: uuid.UUID | None,
    summary: str | None,
    from_email: str | None = None,
    received_at: datetime | None = None,
    category: DisputeCategory | None = None,
    payment_related: bool = False,
    confidence: int | None = None,
    actor: uuid.UUID | None = None,
    classified_by_person: bool = False,
    source_document_id: uuid.UUID | None = None,
    ingested_email_id: uuid.UUID | None = None,
    gmail_thread_id: str | None = None,
    gmail_message_id: str | None = None,
    inbound_body: str | None = None,
    created_by: uuid.UUID | None = None,
    intake_notes: list[str] | None = None,
    hold_in_queue: bool = False,
    thread_dispute: Dispute | None = None,
) -> Created:
    received_at = received_at or datetime.now(UTC)
    invoice = None
    if invoice_id is not None:
        invoice = await invoices_repo.get_invoice_for_update(db, invoice_id)  # one creator per invoice at a time
        if invoice is None:
            raise NotFoundError("Invoice not found")
        if customer_id is not None and customer_id != invoice.customer_id:
            raise ValidationFailure("That invoice belongs to a different customer")
        customer_id = invoice.customer_id
    elif customer_id is not None and await customers_repo.get_by_id(db, customer_id) is None:
        raise NotFoundError("Customer not found")

    # the conversation this complaint continues: found by the caller (a reply to our mail, a reference in the
    # subject), or by the mail thread it sits in
    in_thread = thread_dispute or (await disputes_repo.latest_in_thread(db, gmail_thread_id) if gmail_thread_id else None)
    if in_thread is not None and in_thread.status is not DisputeStatus.RESOLVED:  # E2
        existing = in_thread
        if gmail_message_id and await disputes_repo.message_exists(db, existing.id, gmail_message_id):
            return Created(existing, appended=True)  # a complaint naming several invoices adds its mail once
        await disputes_repo.insert_message(
            db,
            dispute_id=existing.id,
            kind=DisputeMessageKind.INBOUND_EMAIL,
            body=(inbound_body or summary or "(no text)").strip(),
            gmail_message_id=gmail_message_id,
            ingested_email_id=ingested_email_id,
            created_by=actor,
        )
        # the CURRENT owner hears of it (after an escalation that is the inheriting FTM, not the
        # first FA); a dispute still in the classify queue has none, and its assignment will tell them
        if existing.assigned_to is not None and existing.assigned_to != actor:
            await notifications.create(
                db,
                existing.assigned_to,
                NotificationType.DISPUTE_FOLLOW_UP,
                f"The customer wrote again on the dispute {await assignment.label(db, existing)}",
                entity_type="dispute",
                entity_id=existing.id,
                actor=actor,
            )
        return Created(existing, appended=True)

    earlier = in_thread  # E3: the thread's dispute is Resolved (or there is no thread: None)
    confident = is_confident(category, confidence) and not hold_in_queue
    dispute = await disputes_repo.insert_dispute(
        db,
        customer_id=customer_id,
        invoice_id=invoice_id,
        linked_dispute_id=earlier.id if earlier else None,
        ingested_email_id=ingested_email_id,
        source_document_id=source_document_id,
        category=category,
        status=DisputeStatus.RAISED,
        payment_related=payment_related,
        classification_confidence=confidence,
        summary=summary,
        from_email=from_email,
        classified_by=actor if classified_by_person else None,
        classified_at=datetime.now(UTC) if classified_by_person else None,
        received_at=received_at,
        first_response_due=calendar.first_response_due(received_at),
        resolution_due=calendar.resolution_due(received_at),
        gmail_thread_id=gmail_thread_id,
        created_by=created_by or actor,
        updated_by=created_by or actor,
    )
    if invoice is not None:
        invoice.is_disputed = True
    if inbound_body:
        await disputes_repo.insert_message(
            db,
            dispute_id=dispute.id,
            kind=DisputeMessageKind.INBOUND_EMAIL,
            body=inbound_body.strip(),
            gmail_message_id=gmail_message_id,
            ingested_email_id=ingested_email_id,
            created_by=actor,
        )
    if intake_notes:  # what the reader could not settle: shown to the person who classifies it
        await disputes_repo.insert_message(
            db,
            dispute_id=dispute.id,
            kind=DisputeMessageKind.INTERNAL_NOTE,
            body="Reader's notes:\n- " + "\n- ".join(intake_notes),
        )
    assignment.log_state(
        db, dispute, None, "Complaint received" + (f" (follows dispute {earlier.id})" if earlier else ""),
        user_id=actor,
    )
    if confident:
        await categorise_and_assign(
            db, dispute, actor=actor,
            reason=f"Categorised as {category.value}" + ("" if classified_by_person else f" ({confidence}% sure)"),
        )
    return Created(dispute, appended=False)
