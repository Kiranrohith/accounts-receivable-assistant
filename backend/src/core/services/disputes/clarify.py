"""The job that writes (and, when it is safe, sends) a dispute's first clarification.

Queued when a dispute is categorised and handed to someone. It drafts the e-mail from our own
records, saves it as a draft, and sends it by itself ONLY when the gate in clarification.py says so
(first draft, confidence >= 85, nothing ungrounded, category not Other). Otherwise the owner sees
the draft, edits it and sends it. A failure to send leaves the draft and tells the owner; it never
fails the job (a business outcome, not a crash). A model that is down is retried by the queue.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import AppError
from core.services.disputes import clarification, outbound
from core.services.invoices.processing import elapsed_ms, finish_job, retry_job
from core.services.jobs import queue
from data.models.postgres import Dispute, Job
from data.models.postgres.enums import DisputeMessageKind, DisputeStatus, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.platform import jobs_repo, logs_repo

STEP = "clarification"
OPEN_FOR_FIRST_REPLY = (DisputeStatus.ASSIGNED, DisputeStatus.CATEGORISED)


def _step(
    db: AsyncSession, job: Job, dispute: Dispute, started: datetime, result: StepResult, **fields  # noqa: ANN003
) -> None:
    logs_repo.add_step_log(
        db,
        job_id=job.id,
        entity_type=queue.ENTITY_DISPUTE,
        entity_id=dispute.id,
        step_name=STEP,
        result=result,
        occurred_at=datetime.now(UTC),
        duration_ms=elapsed_ms(started),
        **fields,
    )


async def handle(db: AsyncSession, job: Job, dispute: Dispute) -> None:
    """Job handler for dispute_clarification."""
    if dispute.status not in OPEN_FOR_FIRST_REPLY or await disputes_repo.has_outgoing(db, dispute.id):
        await finish_job(db, job, JobState.SUCCEEDED)  # moved on, or an earlier attempt drafted it
        return

    started = datetime.now(UTC)
    try:
        draft = await clarification.generate(db, dispute)
    except clarification.ExtractionError as exc:
        _step(db, job, dispute, started, StepResult.ERROR, error_message=str(exc))
        await retry_job(db, job, str(exc))
        return
    if not await queue.hold(db, job):  # a worker that lost its job writes nothing
        return

    reference = clarification.reference_for(dispute.id)
    message = await disputes_repo.insert_message(
        db,
        dispute_id=dispute.id,
        kind=DisputeMessageKind.DRAFT,
        body=outbound.compose(outbound.subject_with_reference(draft.subject, reference), draft.body),
    )
    dispute.clarification_confidence = draft.confidence
    jobs_repo.update_job(job, model_used=draft.model_used)
    auto = clarification.auto_send_allowed(
        category=dispute.category, confidence=draft.confidence, grounding=draft.grounding, first_draft=True
    )
    summary = {
        "message_id": str(message.id),
        "model_confidence": draft.model_confidence,
        "confidence": draft.confidence,
        "claims_checked": draft.grounding.claims,
        "ungrounded": draft.grounding.ungrounded,
        "auto_send": auto,
        "category": dispute.category.value if dispute.category else None,
    }
    _step(db, job, dispute, started, StepResult.SUCCESS, output_summary=summary, model_used=draft.model_used)
    message_id = message.id
    await unit_of_work.commit(db)  # the draft is safe before anything is sent

    if auto:
        await _send(db, job, dispute.id, message_id, summary)
    await finish_job(db, job, JobState.SUCCEEDED)


async def _send(
    db: AsyncSession, job: Job, dispute_id: uuid.UUID, message_id: uuid.UUID, summary: dict
) -> None:
    """Send the draft just written, under the dispute's lock, if nothing changed meanwhile."""
    started = datetime.now(UTC)
    dispute = await disputes_repo.lock_dispute(db, dispute_id)
    message = await disputes_repo.get_message(db, message_id)
    if (
        dispute is None
        or message is None
        or message.kind is not DisputeMessageKind.DRAFT
        or dispute.status not in OPEN_FOR_FIRST_REPLY
    ):
        await db.rollback()
        return
    try:
        to = await outbound.send_draft(db, dispute, message, user_id=None)
    except AppError as exc:  # no address, or the mail server refused: the draft stays, the owner knows
        _step(db, job, dispute, started, StepResult.WARNING, output_summary={**summary, "sent": False}, error_message=exc.message)
        await unit_of_work.commit(db)
        return
    _step(db, job, dispute, started, StepResult.SUCCESS, output_summary={**summary, "sent": True, "to": to})
    await unit_of_work.commit(db)
