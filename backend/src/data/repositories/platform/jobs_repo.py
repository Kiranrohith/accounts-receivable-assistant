import uuid
from typing import Any

from datetime import datetime

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from data.models.postgres import Customer, Dispute, IngestedEmail, Job, Note, SourceDocument
from data.models.postgres.enums import JobState
from data.repositories import unit_of_work


async def insert_job(db: AsyncSession, **fields: Any) -> Job:
    job = Job(**fields)
    db.add(job)
    await unit_of_work.flush(db)
    return job


def update_job(job: Job, **fields: Any) -> None:
    for name, value in fields.items():
        setattr(job, name, value)


async def dispute_jobs_in_flight(db: AsyncSession, email_id: uuid.UUID, exclude_job_id: uuid.UUID) -> int:
    """Dispute jobs raised from this mail that are still queued or running (the payment waits for them)."""
    from data.models.postgres import SourceDocument

    return await db.scalar(
        select(func.count())
        .select_from(Job)
        .join(SourceDocument, SourceDocument.id == Job.entity_id)
        .where(
            SourceDocument.ingested_email_id == email_id,
            Job.job_type.in_(("dispute_extraction", "dispute_payment_extraction")),
            Job.state.in_((JobState.QUEUED, JobState.RUNNING)),
            Job.id != exclude_job_id,
        )
    ) or 0


async def pending_for_entity(db: AsyncSession, entity_id: uuid.UUID) -> bool:
    """Is a job on this entity still queued or running?"""
    return bool(await db.scalar(select(func.count()).select_from(Job).where(Job.entity_id == entity_id, Job.state.in_((JobState.QUEUED, JobState.RUNNING)))))


async def open_jobs_for(db: AsyncSession, entity_ids: list[uuid.UUID]) -> list[Job]:
    """The jobs on these entities that are still queued or running."""
    if not entity_ids:
        return []
    result = await db.scalars(
        select(Job).where(Job.entity_id.in_(entity_ids), Job.state.in_((JobState.QUEUED, JobState.RUNNING)))
    )
    return list(result)


async def reload_job(db: AsyncSession, job: Job) -> None:
    await db.refresh(job)


async def latest_jobs_for_entities(
    db: AsyncSession, job_type: str | None, entity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Job]:
    """Latest job per entity; `job_type=None` means any type (a document may have had several)."""
    query = select(Job).where(Job.entity_id.in_(entity_ids))
    if job_type is not None:
        query = query.where(Job.job_type == job_type)
    jobs = await db.scalars(query.order_by(Job.created_at))
    return {job.entity_id: job for job in jobs}  # later rows overwrite earlier ones


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await db.get(Job, job_id)


# ---------------------------------------------------------------- failed items
FAILED_STATES = (JobState.FAILED, JobState.DEAD_LETTERED)
ENTITY_BY_KIND = {"document": "source_document", "mail": "ingested_email", "dispute": "dispute", "credit_note": "note"}
_SORTS = {"failed_at": Job.finished_at, "attempts": Job.attempt_count, "kind": Job.entity_type}


def _newer_job_exists(job: type[Job], *, same_type: bool = False):  # noqa: ANN202
    """A later job on the same entity (of the same kind, when `same_type`): this one was re-run, or the work moved on."""
    later = aliased(Job)
    conditions = [
        later.entity_id == job.entity_id,
        or_(later.created_at > job.created_at, and_(later.created_at == job.created_at, later.id > job.id)),
    ]
    if same_type:
        conditions.append(later.job_type == job.job_type)
    return exists().where(*conditions)


async def has_newer_job(db: AsyncSession, job: Job) -> bool:
    return bool(await db.scalar(select(_newer_job_exists(Job)).where(Job.id == job.id)))


async def search_failed(
    db: AsyncSession, *, kind: str | None, search: str | None, sort: str, descending: bool, limit: int, offset: int
) -> tuple[list[Any], int]:
    """(rows, total): the jobs that ended Failed or DeadLettered and have nothing after them, with the names of what
    they worked on. A mail whose triage failed but which a person has since routed is not listed: nothing is left to do."""
    entity_type = func.coalesce(Job.entity_type, "source_document")
    customer_of_dispute, customer_of_note = aliased(Customer), aliased(Customer)
    earlier = aliased(Job)
    query = (
        select(
            Job,
            SourceDocument.file_name.label("doc_name"),
            SourceDocument.file_path.label("doc_path"),
            IngestedEmail.subject.label("mail_subject"),
            IngestedEmail.sender.label("mail_sender"),
            Dispute.summary.label("dispute_summary"),
            customer_of_dispute.customer_name.label("dispute_customer"),
            Note.note_number.label("note_number"),
            customer_of_note.customer_name.label("note_customer"),
            func.coalesce(SourceDocument.id, IngestedEmail.id, Dispute.id, Note.id).is_not(None).label("record_exists"),
            select(func.count())
            .where(earlier.entity_id == Job.entity_id, earlier.job_type == Job.job_type, earlier.created_at < Job.created_at)
            .scalar_subquery()
            .label("earlier_runs"),
        )
        .outerjoin(SourceDocument, and_(entity_type == "source_document", SourceDocument.id == Job.entity_id))
        .outerjoin(IngestedEmail, and_(entity_type == "ingested_email", IngestedEmail.id == Job.entity_id))
        .outerjoin(Dispute, and_(entity_type == "dispute", Dispute.id == Job.entity_id))
        .outerjoin(customer_of_dispute, customer_of_dispute.id == Dispute.customer_id)
        .outerjoin(Note, and_(entity_type == "note", Note.id == Job.entity_id))
        .outerjoin(customer_of_note, customer_of_note.id == Note.customer_id)
        .where(
            Job.state.in_(FAILED_STATES),
            ~_newer_job_exists(Job),
            ~and_(
                Job.job_type == "mail_triage",
                or_(IngestedEmail.status.is_not(None), IngestedEmail.classification.is_not(None)),
            ),
        )
    )
    if kind:
        query = query.where(entity_type == ENTITY_BY_KIND[kind])
    if search and search.strip():
        # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
        escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        columns = (
            SourceDocument.file_name, IngestedEmail.subject, IngestedEmail.sender, Dispute.summary,
            customer_of_dispute.customer_name, Note.note_number, customer_of_note.customer_name, Job.last_error_message,
        )
        query = query.where(or_(*(column.ilike(like, escape="\\") for column in columns)))
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    column = _SORTS.get(sort, Job.finished_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    result = await db.execute(query.order_by(ordered, Job.id).limit(limit).offset(offset))
    return list(result.all()), total or 0


async def claim_next(db: AsyncSession, now: datetime) -> Job | None:
    """Oldest claimable job, row-locked; SKIP LOCKED so concurrent workers never take the same one."""
    return await db.scalar(
        select(Job)
        .where(Job.state == JobState.QUEUED, Job.run_after <= now)
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def lock_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Re-read the job from the database, row-locked (discards unflushed changes: flush first)."""
    return await db.scalar(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True)
    )


async def touch_heartbeat(db: AsyncSession, job_id: uuid.UUID, worker_id: str) -> bool:
    """False when the job is no longer this worker's running job (the watchdog took it)."""
    result = await db.execute(
        update(Job)
        .where(Job.id == job_id, Job.locked_by == worker_id, Job.state == JobState.RUNNING)
        .values(heartbeat_at=func.now())
    )
    return result.rowcount == 1


async def running_started_before(db: AsyncSession, cutoff: datetime) -> list[Job]:
    """Running jobs that have been going longer than the runtime cap."""
    result = await db.scalars(
        select(Job)
        .where(Job.state == JobState.RUNNING, Job.started_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    return list(result)


async def running_without_heartbeat(db: AsyncSession, cutoff: datetime) -> list[Job]:
    """Running jobs whose worker has gone quiet (never beat, or beat before the cutoff)."""
    result = await db.scalars(
        select(Job)
        .where(
            Job.state == JobState.RUNNING,
            func.coalesce(Job.heartbeat_at, Job.started_at) < cutoff,
        )
        .with_for_update(skip_locked=True)
    )
    return list(result)


# ---------------------------------------------------------------- live mail processing
def _in_flight_or_recent(finished_since: datetime):  # noqa: ANN202
    return or_(Job.state.in_((JobState.QUEUED, JobState.RUNNING)), Job.finished_at >= finished_since)


async def live_triage_jobs(db: AsyncSession, *, finished_since: datetime, limit: int) -> list[tuple[Job, IngestedEmail]]:
    """The newest triage job of each mail that is still queued or running, or that ended after `finished_since`."""
    query = (
        select(Job, IngestedEmail)
        .join(IngestedEmail, IngestedEmail.id == Job.entity_id)
        .where(Job.job_type == "mail_triage", Job.entity_type == "ingested_email", _in_flight_or_recent(finished_since), ~_newer_job_exists(Job, same_type=True))
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    )
    return [(job, mail) for job, mail in (await db.execute(query)).all()]


async def live_mail_document_jobs(
    db: AsyncSession, *, job_types: tuple[str, ...], finished_since: datetime, limit: int
) -> list[tuple[Job, SourceDocument]]:
    """The newest job of each document that came from a mail and is still queued or running, or ended after `finished_since`.
    A document run again has only its newest job here, so the earlier failure does not show beside the new attempt."""
    query = (
        select(Job, SourceDocument)
        .join(SourceDocument, SourceDocument.id == Job.entity_id)
        .where(
            Job.job_type.in_(job_types),
            or_(Job.entity_type == "source_document", Job.entity_type.is_(None)),
            SourceDocument.ingested_email_id.is_not(None),
            _in_flight_or_recent(finished_since),
            ~_newer_job_exists(Job),
        )
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    )
    return [(job, document) for job, document in (await db.execute(query)).all()]


async def waiting_line(db: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """Every queued job a worker could take now, in the order workers take them (the oldest first)."""
    rows = await db.scalars(
        select(Job.id).where(Job.state == JobState.QUEUED, Job.run_after <= now).order_by(Job.run_after, Job.created_at)
    )
    return list(rows)
