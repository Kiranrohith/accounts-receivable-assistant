"""Failed items: background jobs that ended Failed or DeadLettered and have nothing after them, listed for a person, and
the re-run of them. A re-run is a new job of the same type on the same entity, linked to the failed one (rerun_of);
the failed one then has something after it and leaves the list. Anyone with failed_items.rerun may list and re-run."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import AppError, ConflictError, NotFoundError
from core.services.jobs import queue
from data.models.postgres import Job, User
from data.models.postgres.enums import JobState
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import documents_repo
from data.repositories.mail import emails_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo
from schemas.common import Page
from schemas.jobs.failed_items import FailedItem, FailedKind, RerunOutcome, RerunResults

_TASKS = {
    queue.JOB_INTAKE: "Reading an uploaded document",
    queue.JOB_INVOICE: "Reading an uploaded invoice",
    queue.JOB_PO: "Reading an uploaded purchase order",
    queue.JOB_PAYMENT: "Reading an uploaded payment advice",
    queue.JOB_DISPUTE: "Reading an uploaded complaint",
    queue.JOB_DISPUTE_PAYMENT: "Reading a complaint that holds a payment advice",
    queue.JOB_DISPUTE_CLARIFY: "Drafting the first reply to a complaint",
    queue.JOB_PAYMENT_REMATCH: "Matching a held payment again",
    queue.JOB_CREDIT_NOTE_MAIL: "Sending a credit note by e-mail",
    queue.JOB_MAIL_TRIAGE: "Deciding what a mail is",
    queue.JOB_MAIL_DUPLICATE: "Recording a re-sent file as a duplicate",
}
# these read the stored file again, so without it there is nothing to run
_NEEDS_FILE = {queue.JOB_INTAKE, queue.JOB_INVOICE, queue.JOB_PO, queue.JOB_PAYMENT, queue.JOB_DISPUTE, queue.JOB_DISPUTE_PAYMENT}
_KIND = {value: key for key, value in jobs_repo.ENTITY_BY_KIND.items()}


def _item(row: Any) -> FailedItem:
    job: Job = row.Job
    kind: FailedKind = _KIND[job.entity_type or queue.ENTITY_TYPE]
    if kind == "document":
        subject, detail = row.doc_name, None
    elif kind == "mail":
        subject, detail = row.mail_subject or "(no subject)", row.mail_sender
    elif kind == "dispute":
        subject, detail = row.dispute_summary, row.dispute_customer
    else:
        subject, detail = row.note_number, row.note_customer
    because = None
    if not row.record_exists:
        because = "The record this was working on is gone."
    elif job.job_type in _NEEDS_FILE and not row.doc_path:
        because = "The file is no longer available, so it cannot be read again."
    return FailedItem(
        job_id=job.id,
        kind=kind,
        task=_TASKS.get(job.job_type, job.job_type),
        subject=subject,
        detail=detail,
        entity_id=job.entity_id,
        state=job.state.value,
        error=job.last_error_message,
        attempts=job.attempt_count or 0,
        failed_at=job.finished_at,
        earlier_runs=row.earlier_runs or 0,
        can_rerun=because is None,
        cannot_rerun_because=because,
    )


async def list_failed(
    db: AsyncSession, *, kind: FailedKind | None, search: str | None, sort: str, descending: bool, limit: int, offset: int
) -> Page[FailedItem]:
    rows, total = await jobs_repo.search_failed(
        db, kind=kind, search=search, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return Page(items=[_item(row) for row in rows], total=total)


async def _entity(db: AsyncSession, job: Job) -> Any | None:
    if job.entity_id is None:
        return None
    if job.entity_type == queue.ENTITY_MAIL:
        return await emails_repo.get_email(db, job.entity_id)
    if job.entity_type == queue.ENTITY_DISPUTE:
        return await disputes_repo.get_dispute(db, job.entity_id)
    if job.entity_type == queue.ENTITY_NOTE:
        return await payments_repo.get_note(db, job.entity_id)
    return await documents_repo.get_document(db, job.entity_id)


async def rerun(db: AsyncSession, user: User, job_id: uuid.UUID) -> uuid.UUID:
    """Queue the failed job again. Returns the new job's id. Commits."""
    job = await jobs_repo.lock_job(db, job_id)
    if job is None:
        raise NotFoundError("Failed item not found")
    if job.state not in jobs_repo.FAILED_STATES:
        raise ConflictError("This item has not failed")
    if await jobs_repo.has_newer_job(db, job):
        raise ConflictError("This item was already run again, or the work has moved on")
    entity = await _entity(db, job)
    if entity is None:
        raise ConflictError("The record this was working on is gone")
    if job.job_type in _NEEDS_FILE and not getattr(entity, "file_path", None):
        raise ConflictError("The file is no longer available, so it cannot be read again")
    fresh = await queue.enqueue(
        db,
        job_type=job.job_type,
        entity_id=job.entity_id,
        entity_type=job.entity_type or queue.ENTITY_TYPE,
        # same kind of job as the failed one, so a person's earlier type choice still applies; a new upload is read for
        # the person who asks, as the upload re-run does
        created_by=user.id if job.job_type == queue.JOB_INTAKE else job.created_by,
        rerun_of=job.id,
    )
    await unit_of_work.commit(db)
    return fresh.id


async def rerun_many(db: AsyncSession, user: User, job_ids: list[uuid.UUID]) -> RerunResults:
    """Each one on its own, so one that cannot be re-run does not stop the rest."""
    results: list[RerunOutcome] = []
    for job_id in dict.fromkeys(job_ids):
        try:
            results.append(RerunOutcome(job_id=job_id, ok=True, new_job_id=await rerun(db, user, job_id)))
        except AppError as exc:
            await db.rollback()
            results.append(RerunOutcome(job_id=job_id, ok=False, message=exc.message))
    return RerunResults(results=results, queued=sum(r.ok for r in results))
