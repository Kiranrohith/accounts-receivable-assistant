"""E-mailing a credit note to its customer (decision 68): the note's PDF attached, sent by a worker.

Every note is born inside a money transaction, so the mail is a JOB queued in that same transaction
(`queue_mail`, called from create_credit_note): an SMTP outage can never delay or undo the money, and a failed send is
retried by the queue. The handler:

  - sends nothing twice: a note whose mail already went out is left alone (a person's resend is a rerun and sends)
  - needs a valid customer e-mail address; without one the job fails at once and the people who can fix it are told
  - a refusal that will not change (address not on the demo allowlist) fails at once; a transport failure is retried by
    the queue with backoff and, when the attempts run out, the job is dead-lettered and the people are told
  - every attempt leaves a step-log row; the one that sent it records the message id
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import ConflictError, NotFoundError
from core.services.invoices.amounts import format_money
from core.services.invoices.processing import finish_job, retry_job
from core.services.jobs import queue
from core.services.mail import sending, smtp, templates
from core.services.payments import note_documents
from data.models.postgres import Job, Note, User
from data.models.postgres.enums import ErrorClass, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo, logs_repo

logger = logging.getLogger(__name__)
STEP = "send_credit_note_email"


async def queue_mail(db: AsyncSession, note: Note, actor: uuid.UUID | None, *, rerun_of: uuid.UUID | None = None) -> Job:
    """Queue the mail for this note. Flushes only: it commits with the money that made the note."""
    return await queue.enqueue(
        db, job_type=queue.JOB_CREDIT_NOTE_MAIL, entity_type=queue.ENTITY_NOTE, entity_id=note.id, created_by=actor, rerun_of=rerun_of
    )


async def sent_log(db: AsyncSession, note: Note) -> Any | None:
    """The step-log row of the send that went out, if any."""
    rows = await logs_repo.list_step_logs(db, queue.ENTITY_NOTE, note.id)
    done = [r for r in rows if r.step_name == STEP and r.result is StepResult.SUCCESS]
    return done[-1] if done else None


def _tell(note: Note) -> list[uuid.UUID | None]:
    return [note.created_by]  # the person who made it; nobody (system-made) -> the admins


async def handle(db: AsyncSession, job: Job, note: Note) -> None:
    if job.rerun_of is None and await sent_log(db, note) is not None:  # sent already: never twice
        await finish_job(db, job, JobState.SUCCEEDED)
        return
    customer = await customers_repo.get_by_id(db, note.customer_id)
    to = sending.clean_address(customer.email) if customer else None
    if not await queue.hold(db, job):
        return
    if customer is None or to is None:
        reason = f"{customer.customer_name if customer else 'The customer'} has no valid e-mail address on file"
        sending.log_send(
            db, entity_type=queue.ENTITY_NOTE, entity_id=note.id, step_name=STEP,
            to=(customer.email if customer else "") or "", error=smtp.SmtpError(reason, permanent=True), job_id=job.id,
        )
        await sending.tell_failure(
            db, _tell(note), f"Credit note {note.note_number} was not e-mailed: {reason}. Fix the address, then resend it.",
            entity_type=queue.ENTITY_NOTE, entity_id=note.id,
        )
        await finish_job(db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=reason)
        return

    pdf = await note_documents.ensure_pdf(db, note, customer)
    rendered = templates.render_email(
        "credit_note_email",
        note_number=note.note_number,
        note_amount=format_money(note.amount, note.currency),
        note_reason=note.reason or "",
        contact_name=customer.contact,
        customer_name=customer.customer_name,
    )
    message = smtp.build_message(
        to=to, subject=rendered.subject, body=rendered.body, sender=get_settings().smtp_from,
        attachments=[(note_documents.file_name(note), pdf, "application/pdf")],
    )
    delivery = await sending.deliver(db, message, entity_type=queue.ENTITY_NOTE, entity_id=note.id, step_name=STEP, job_id=job.id)
    if delivery.ok:
        logs_repo.add_state_log(
            db, entity_type="note", entity_id=note.id, from_state=note.status.value, to_state=note.status.value,
            reason=f"Credit note e-mailed to {to}", occurred_at=datetime.now(UTC),
        )
        await finish_job(db, job, JobState.SUCCEEDED)
        return
    text = f"Credit note {note.note_number} was not e-mailed to {to}: {delivery.reason}."
    if delivery.permanent:
        await sending.tell_failure(db, _tell(note), text + " Resend it once that is fixed.", entity_type=queue.ENTITY_NOTE, entity_id=note.id)
        await finish_job(db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=delivery.reason)
        return
    await retry_job(db, job, delivery.reason)  # the queue retries it with backoff, and dead-letters it at the end
    if job.state is JobState.DEAD_LETTERED:
        await sending.tell_failure(
            db, _tell(note), text + " It was tried several times. Resend it once the mail server is back.",
            entity_type=queue.ENTITY_NOTE, entity_id=note.id,
        )
        await unit_of_work.commit(db)


async def resend(db: AsyncSession, user: User, note_id: uuid.UUID) -> uuid.UUID:
    """A person asks for the credit-note mail to be sent (again). One at a time per note. Returns the job id."""
    note = await payments_repo.get_note(db, note_id)
    if note is None:
        raise NotFoundError("Credit note not found")
    if await jobs_repo.pending_for_entity(db, note.id):
        raise ConflictError("This credit note's e-mail is already waiting to be sent")
    prior = (await jobs_repo.latest_jobs_for_entities(db, queue.JOB_CREDIT_NOTE_MAIL, [note.id])).get(note.id)
    job = await queue_mail(db, note, user.id, rerun_of=prior.id if prior else None)
    await unit_of_work.commit(db)
    return job.id


async def status(db: AsyncSession, note: Note) -> dict:
    """What a person sees: queued, sent (when, to whom), failed (why) or not sent."""
    if await jobs_repo.pending_for_entity(db, note.id):
        return {"state": "queued", "to": None, "at": None, "error": None}
    rows = [r for r in await logs_repo.list_step_logs(db, queue.ENTITY_NOTE, note.id) if r.step_name == STEP]
    if not rows:
        return {"state": "not_sent", "to": None, "at": None, "error": None}
    last = rows[-1]
    if last.result is StepResult.SUCCESS:
        return {"state": "sent", "to": (last.output_summary or {}).get("to"), "at": last.occurred_at, "error": None}
    return {"state": "failed", "to": (last.input_summary or {}).get("to"), "at": last.occurred_at, "error": last.error_message}
