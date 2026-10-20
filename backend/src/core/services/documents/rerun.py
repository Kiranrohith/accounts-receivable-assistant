"""Re-run an upload whose processing failed (Failed, or DeadLettered after the retries ran out)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError, ValidationFailure
from core.services.jobs import queue
from data.models.postgres import User
from data.models.postgres.enums import JobState
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.platform import jobs_repo
from schemas.documents.uploads import RerunResult


async def rerun_upload(db: AsyncSession, user: User, document_id: uuid.UUID) -> RerunResult:
    document = await documents_repo.get_document_for_update(db, document_id)
    if document is None:
        raise NotFoundError("Uploaded document not found")
    latest = (await jobs_repo.latest_jobs_for_entities(db, None, [document.id])).get(document.id)
    if latest is None or latest.state not in (JobState.FAILED, JobState.DEAD_LETTERED):
        raise ConflictError("Only an upload whose processing failed can be re-run")
    if not document.file_path:
        raise ValidationFailure("The file is no longer available, so it cannot be processed again")
    job = await queue.enqueue(
        db,
        # same kind of job as the failed one, so a person's earlier type choice still applies
        job_type=latest.job_type,
        entity_id=document.id,
        created_by=user.id if latest.job_type == queue.JOB_INTAKE else latest.created_by,
        rerun_of=latest.id,
    )
    await unit_of_work.commit(db)
    return RerunResult(source_document_id=document.id, job_id=job.id)
