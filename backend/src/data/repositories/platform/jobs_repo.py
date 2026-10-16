import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Job
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
