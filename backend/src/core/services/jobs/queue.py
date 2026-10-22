"""The Postgres-backed job queue: the jobs table is both the truth and the transport.

- enqueue: a Queued row, written in the same transaction as the thing that needs the work
- claim: FOR UPDATE SKIP LOCKED, so any number of workers never take the same job
- heartbeat: the running worker stamps the row every HEARTBEAT_SECONDS
- retry: a retryable failure goes back to Queued after OUTAGE_BACKOFF_SECONDS (jittered);
  when the attempts run out it is DeadLettered (re-runnable by a person)
- watchdog: a Running job whose heartbeat went quiet is reclaimed; one running past
  MAX_JOB_RUNTIME_MINUTES is failed
- fencing: every write that ends or requeues a job first re-reads the row under lock and does
  nothing if the job is no longer this worker's (the watchdog may have taken it)
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import inspect
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import (
    BACKOFF_JITTER,
    HEARTBEAT_SECONDS,
    MAX_JOB_RUNTIME_MINUTES,
    OUTAGE_BACKOFF_SECONDS,
    STALE_HEARTBEAT_SECONDS,
)
from data.clients.db import session_scope
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import ErrorClass, JobState
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import documents_repo
from data.repositories.mail import emails_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo, logs_repo

logger = logging.getLogger(__name__)

ENTITY_TYPE = "source_document"  # what a job works on, unless it says otherwise
ENTITY_DISPUTE = "dispute"  # a job about a dispute (its entity_id is the dispute)
ENTITY_MAIL = "ingested_email"  # a job about a polled e-mail (its entity_id is the registry row)
ENTITY_NOTE = "note"  # a job about a credit note (its entity_id is the note)

# Job types. The type says what the worker should do with the document:
JOB_INTAKE = "document_intake"  # new upload: parse, classify, then extract and route
JOB_INVOICE = "invoice_extraction"  # a person decided it is an invoice: skip the classifier
JOB_PO = "po_extraction"  # a person decided it is a purchase order: skip the classifier
JOB_PAYMENT = "payment_extraction"  # a person decided it is a payment advice: skip the classifier
JOB_DISPUTE = "dispute_extraction"  # a person decided it is a complaint: skip the classifier
JOB_DISPUTE_PAYMENT = "dispute_payment_extraction"  # a complaint that also holds a payment advice
JOB_DISPUTE_CLARIFY = "dispute_clarification"  # draft (and maybe send) the first clarification
JOB_MAIL_TRIAGE = "mail_triage"  # classify a polled e-mail and spawn what it needs
JOB_MAIL_DUPLICATE = "mail_duplicate"  # record a re-sent file as a duplicate once the first copy has its payment
JOB_CREDIT_NOTE_MAIL = "credit_note_mail"  # render a credit note's PDF and e-mail it to the customer
JOB_PAYMENT_REMATCH = "payment_rematch"  # a parked payment: match it again (its hold was released)

MAX_ATTEMPTS = 1 + len(OUTAGE_BACKOFF_SECONDS)  # the first run plus one per backoff step

# Module-level so tests can shorten them.
heartbeat_seconds: float = HEARTBEAT_SECONDS
stale_heartbeat_seconds: float = STALE_HEARTBEAT_SECONDS
max_job_runtime_minutes: float = MAX_JOB_RUNTIME_MINUTES
retry_delays_seconds: tuple[float, ...] = OUTAGE_BACKOFF_SECONDS

# Which worker slot is running in the current task (for fencing).
CURRENT_WORKER: ContextVar[str | None] = ContextVar("current_worker", default=None)

# the third argument is the job's entity: a SourceDocument, or a Dispute for ENTITY_DISPUTE jobs
Handler = Callable[[AsyncSession, Job, Any], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


def _jittered(seconds: float) -> float:
    return seconds * (1 + random.uniform(-float(BACKOFF_JITTER), float(BACKOFF_JITTER)))


def _log(db: AsyncSession, job: Job, from_state: str | None, to_state: str, reason: str | None) -> None:
    logs_repo.add_state_log(
        db,
        # a dispute's or e-mail's job states are the job's, not the entity's: they must not read as its own states
        entity_type={ENTITY_DISPUTE: "dispute_job", ENTITY_MAIL: "mail_job", ENTITY_NOTE: "note_job"}.get(job.entity_type, job.entity_type or ENTITY_TYPE),
        entity_id=job.entity_id,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        occurred_at=_now(),
    )


# ---------------------------------------------------------------- enqueue / claim
async def enqueue(
    db: AsyncSession,
    *,
    job_type: str,
    entity_id: uuid.UUID,
    created_by: uuid.UUID | None,
    rerun_of: uuid.UUID | None = None,
    entity_type: str = ENTITY_TYPE,
) -> Job:
    """Add a Queued job. Flushes only: the caller commits it together with its own write."""
    now = _now()
    job = await jobs_repo.insert_job(
        db,
        job_type=job_type,
        entity_type=entity_type,
        entity_id=entity_id,
        state=JobState.QUEUED,
        attempt_count=0,
        queued_at=now,
        run_after=now,
        rerun_of=rerun_of,
        created_by=created_by,
    )
    _log(db, job, None, JobState.QUEUED.value, "Rerun" if rerun_of else None)
    return job


async def cancel(db: AsyncSession, job_id: uuid.UUID, reason: str) -> bool:
    """Withdraw a job that has not started: it ends Failed (not retryable) with the reason. False when a worker is
    running it right now (the caller must wait). Flushes only; the caller commits."""
    job = await jobs_repo.lock_job(db, job_id)
    if job is None or job.state is not JobState.QUEUED:
        return job is None or job.state is not JobState.RUNNING  # finished already: nothing to withdraw
    jobs_repo.update_job(
        job, state=JobState.FAILED, finished_at=_now(), last_error_class=ErrorClass.NON_RETRYABLE, last_error_message=reason
    )
    _log(db, job, JobState.QUEUED.value, JobState.FAILED.value, reason)
    await unit_of_work.flush(db)
    return True


async def claim(db: AsyncSession, worker_id: str) -> Job | None:
    """Take the oldest due job for this worker, or None. Commits."""
    now = _now()
    job = await jobs_repo.claim_next(db, now)
    if job is None:
        await db.rollback()  # end the read transaction
        return None
    attempt = (job.attempt_count or 0) + 1
    jobs_repo.update_job(
        job,
        state=JobState.RUNNING,
        locked_by=worker_id,
        started_at=now,
        heartbeat_at=now,
        finished_at=None,
        attempt_count=attempt,
    )
    _log(db, job, JobState.QUEUED.value, JobState.RUNNING.value, f"attempt {attempt} by {worker_id}")
    await unit_of_work.commit(db)
    return job


# ---------------------------------------------------------------- fenced state changes
async def hold(db: AsyncSession, job: Job) -> bool:
    """Lock the job row and confirm it is still this worker's running job.

    False (after a rollback) when it is not: the watchdog reclaimed it, so whatever this worker
    was about to record about it must not be written.
    """
    await db.flush()
    job_id = inspect(job).identity[0]  # works on an expired instance, unlike job.id
    fresh = await jobs_repo.lock_job(db, job_id)
    worker = CURRENT_WORKER.get()
    mine = (
        fresh is not None
        and fresh.state is JobState.RUNNING
        and (worker is None or fresh.locked_by == worker)
    )
    if not mine:
        await db.rollback()
        logger.warning("job %s is no longer owned by %s; leaving it alone", job_id, worker)
    return mine


async def finish(
    db: AsyncSession,
    job: Job,
    state: JobState,
    *,
    error_class: ErrorClass | None = None,
    error: str | None = None,
) -> bool:
    """End the job (Succeeded / Failed / DeadLettered). Commits. False if it was not ours."""
    if not await hold(db, job):
        return False
    previous = job.state.value
    jobs_repo.update_job(
        job,
        state=state,
        finished_at=_now(),
        last_error_class=error_class,
        last_error_message=error,
    )
    _log(db, job, previous, state.value, error)
    await unit_of_work.commit(db)
    return True


async def retry_or_dead_letter(db: AsyncSession, job: Job, error: str) -> bool:
    """A retryable failure: back to Queued after the next backoff step, or DeadLettered when
    the attempts are used up. Commits. False if the job was not ours."""
    if not await hold(db, job):
        return False
    attempt = job.attempt_count or 1
    if attempt < MAX_ATTEMPTS:
        delay = _jittered(retry_delays_seconds[min(attempt, len(retry_delays_seconds)) - 1])
        jobs_repo.update_job(
            job,
            state=JobState.QUEUED,
            run_after=_now() + timedelta(seconds=delay),
            locked_by=None,
            heartbeat_at=None,
            last_error_class=ErrorClass.RETRYABLE,
            last_error_message=error,
        )
        _log(
            db, job, JobState.RUNNING.value, JobState.QUEUED.value,
            f"Retrying in {delay:.0f}s (attempt {attempt} of {MAX_ATTEMPTS} failed): {error}",
        )
    else:
        jobs_repo.update_job(
            job,
            state=JobState.DEAD_LETTERED,
            finished_at=_now(),
            last_error_class=ErrorClass.RETRYABLE,
            last_error_message=error,
        )
        _log(
            db, job, JobState.RUNNING.value, JobState.DEAD_LETTERED.value,
            f"Gave up after {attempt} attempts: {error}",
        )
    await unit_of_work.commit(db)
    return True


async def requeue_for_shutdown(db: AsyncSession, job: Job) -> bool:
    """The worker is stopping mid-job: put it straight back, and do not count the attempt."""
    if not await hold(db, job):
        return False
    jobs_repo.update_job(
        job,
        state=JobState.QUEUED,
        run_after=_now(),
        locked_by=None,
        heartbeat_at=None,
        attempt_count=max((job.attempt_count or 1) - 1, 0),
    )
    _log(db, job, JobState.RUNNING.value, JobState.QUEUED.value, "Worker shutting down; requeued")
    await unit_of_work.commit(db)
    return True


async def defer(db: AsyncSession, job: Job, seconds: float, reason: str) -> bool:
    """Put the running job back for later because it must wait for something else (not a failure: the attempt is not
    counted). Commits. False if the job was not ours."""
    if not await hold(db, job):
        return False
    jobs_repo.update_job(
        job,
        state=JobState.QUEUED,
        run_after=_now() + timedelta(seconds=seconds),
        locked_by=None,
        heartbeat_at=None,
        attempt_count=max((job.attempt_count or 1) - 1, 0),
    )
    _log(db, job, JobState.RUNNING.value, JobState.QUEUED.value, f"Deferred {seconds:.0f}s: {reason}")
    await unit_of_work.commit(db)
    return True


# ---------------------------------------------------------------- running a job
async def heartbeat_loop(job_id: uuid.UUID, worker_id: str) -> None:
    while True:
        await asyncio.sleep(heartbeat_seconds)
        try:
            async with session_scope() as db:
                still_ours = await jobs_repo.touch_heartbeat(db, job_id, worker_id)
                await db.commit()
        except Exception:  # a missed beat is survivable; the next one may land
            logger.exception("heartbeat write failed for job %s", job_id)
            continue
        if not still_ours:
            logger.warning("job %s was taken from %s", job_id, worker_id)
            return


def _is_transient(exc: Exception) -> bool:
    """Worth retrying later: the database or the network blinked. A bug will fail again."""
    return isinstance(exc, (OperationalError, InterfaceError, ConnectionError, TimeoutError))


async def execute(job_id: uuid.UUID, worker_id: str, handlers: dict[str, Handler]) -> None:
    """Run one claimed job to an outcome. Never raises (except to propagate cancellation)."""
    CURRENT_WORKER.set(worker_id)
    heartbeat: asyncio.Task | None = None
    async with session_scope() as db:
        job = await jobs_repo.get_job(db, job_id)
        if job is None:
            return
        try:
            heartbeat = asyncio.create_task(heartbeat_loop(job_id, worker_id))
            if job.entity_type == ENTITY_DISPUTE:
                document = await disputes_repo.get_dispute(db, job.entity_id) if job.entity_id else None
            elif job.entity_type == ENTITY_MAIL:
                document = await emails_repo.get_email(db, job.entity_id) if job.entity_id else None
            elif job.entity_type == ENTITY_NOTE:
                document = await payments_repo.get_note(db, job.entity_id) if job.entity_id else None
            else:
                document = await documents_repo.get_document(db, job.entity_id) if job.entity_id else None
            handler = handlers.get(job.job_type)
            if document is None or handler is None:
                reason = "the record is gone" if document is None else f"no handler for {job.job_type}"
                await finish(db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=reason)
                return
            await handler(db, job, document)
            await db.refresh(job)
            if job.state is JobState.RUNNING and job.locked_by == worker_id:
                await finish(
                    db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE,
                    error="The pipeline ended without a result",
                )
        except asyncio.CancelledError:
            await db.rollback()
            with suppress(Exception):
                await requeue_for_shutdown(db, job)
            raise
        except Exception as exc:  # unexpected: record it, never lose it silently
            logger.exception("job %s crashed", job_id)
            await db.rollback()
            message = f"Unexpected error: {exc}"
            if _is_transient(exc):
                await retry_or_dead_letter(db, job, message)
            else:
                await finish(db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=message)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat


# ---------------------------------------------------------------- watchdog
@dataclass
class WatchdogResult:
    requeued: int = 0
    dead_lettered: int = 0
    timed_out: int = 0


async def watchdog_pass(db: AsyncSession) -> WatchdogResult:
    """Two checks. Past the runtime cap: fail it (pathologically long). Heartbeat gone quiet:
    the worker died, so give the job back (or dead-letter it when the attempts are used up).
    Safe to run from many workers at once: rows are taken with SKIP LOCKED. Commits."""
    now = _now()
    result = WatchdogResult()

    for job in await jobs_repo.running_started_before(
        db, now - timedelta(minutes=max_job_runtime_minutes)
    ):
        jobs_repo.update_job(
            job,
            state=JobState.FAILED,
            finished_at=now,
            last_error_class=ErrorClass.NON_RETRYABLE,
            last_error_message=f"Ran longer than {max_job_runtime_minutes:g} minutes and was stopped",
        )
        _log(db, job, JobState.RUNNING.value, JobState.FAILED.value, job.last_error_message)
        result.timed_out += 1
    await db.flush()  # the second query must not see the jobs just failed

    for job in await jobs_repo.running_without_heartbeat(
        db, now - timedelta(seconds=stale_heartbeat_seconds)
    ):
        lost = f"The worker stopped responding ({job.locked_by}); no heartbeat for {stale_heartbeat_seconds:g}s"
        if (job.attempt_count or 0) < MAX_ATTEMPTS:
            jobs_repo.update_job(
                job,
                state=JobState.QUEUED,
                run_after=now,
                locked_by=None,
                heartbeat_at=None,
                last_error_class=ErrorClass.RETRYABLE,
                last_error_message=lost,
            )
            _log(db, job, JobState.RUNNING.value, JobState.QUEUED.value, f"Reclaimed: {lost}")
            result.requeued += 1
        else:
            jobs_repo.update_job(
                job,
                state=JobState.DEAD_LETTERED,
                finished_at=now,
                last_error_class=ErrorClass.RETRYABLE,
                last_error_message=lost,
            )
            _log(db, job, JobState.RUNNING.value, JobState.DEAD_LETTERED.value, lost)
            result.dead_lettered += 1
    await unit_of_work.commit(db)
    return result
