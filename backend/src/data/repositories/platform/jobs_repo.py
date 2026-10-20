import uuid
from typing import Any

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Job
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
