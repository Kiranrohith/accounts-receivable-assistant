"""The triage job for a polled e-mail: decide what it is and where it goes.

Steps (the same transaction ends with the registry row, the logs and the job's own finish):
  1. read the kept .eml, qualify its attachments, look up what our records say about the sender and the text;
  2. rules that need no model (classification.pre_decide), else the model (classification.classify_mail);
  3. the route: AUTO (confident: handed to its pipelines), HUMAN (the triage queue,
     label AR/Triage) or IGNORE (general mail: status Processed, label AR/Ignored).

A confident payment / dispute / both / PO mail is HANDED OFF in this same transaction (handoff.py: its documents and
jobs, status Processed); a mail that is still classified with no status is one a crash left halfway, and the cycle's
sweep finishes it. HumanTriage means a person must decide; Processed means finished. If every model fails the job is retried later by the queue (it is never guessed).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import GMAIL_LABEL_IGNORED, GMAIL_LABEL_PROCESSED_DISPUTE, GMAIL_LABEL_TRIAGE
from core.services.invoices.processing import elapsed_ms, finish_job, retry_job
from core.services.jobs import queue
from core.services.mail import classification, handoff, ingest, threads
from core.services.mail.attachments import Qualification
from core.services.mail.context import MailContext, find_context
from data.models.postgres import IngestedEmail, Job
from data.models.postgres.enums import DisputeMessageKind, EmailStatus, ErrorClass, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.platform import jobs_repo, logs_repo

logger = logging.getLogger(__name__)
STEP = "triage"
LABELS = {classification.Route.IGNORE: GMAIL_LABEL_IGNORED, classification.Route.HUMAN: GMAIL_LABEL_TRIAGE}


def apply_decision(db: AsyncSession, email: IngestedEmail, decision: classification.Decision, *, by: str) -> str | None:
    """Write the decision onto the registry row and log it. Returns the Gmail label to put on, if any."""
    before = email.status.value if email.status else "Received"
    email.classification = decision.mail_class
    email.classification_confidence = decision.confidence
    label = LABELS.get(decision.route)
    email.label_applied = label
    email.status = {
        classification.Route.IGNORE: EmailStatus.PROCESSED,
        classification.Route.HUMAN: EmailStatus.HUMAN_TRIAGE,
        classification.Route.AUTO: None,
    }[decision.route]
    after = email.status.value if email.status else "Classified (waiting for its pipeline)"
    cls = decision.mail_class.value if decision.mail_class else "unknown"
    logs_repo.add_state_log(
        db, entity_type="ingested_email", entity_id=email.id, from_state=before, to_state=after,
        reason=f"{by}: {cls} ({decision.confidence}) - {decision.reason}", occurred_at=datetime.now(UTC),
    )
    return label


def summary(decision: classification.Decision, context: MailContext, verdict: Any, qualification: Qualification) -> dict:
    return {
        "route": decision.route.value,
        "class": decision.mail_class.value if decision.mail_class else None,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "context": {
            "sender_known": context.sender_known,
            "invoice_numbers": list(context.invoice_numbers),
            "customer_named": context.customer_named,
        },
        "thread": context.thread.as_json() if context.thread else None,
        "verdict": verdict.model_dump(mode="json") if verdict is not None else None,
        "qualifying_attachments": len(qualification.qualifying),
    }


async def handle(db: AsyncSession, job: Job, email: IngestedEmail) -> None:
    started = datetime.now(UTC)
    if email.classification is not None or email.status is not None:  # decided already (an earlier attempt, or a person)
        await finish_job(db, job, JobState.SUCCEEDED)
        return
    try:
        mail, qualification = handoff.load_mail(email)
    except OSError as exc:
        await finish_job(
            db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=f"The kept mail file is missing: {exc}"
        )
        return
    context = await find_context(db, mail, email.gmail_thread_id)

    decision = classification.pre_decide(mail, context, qualification)
    verdict, model, attempts = None, None, 0
    if decision is None:
        try:
            verdict, model, attempts = await classification.classify_mail(
                mail, context, qualification, is_reply=bool(mail.in_reply_to)
            )
        except classification.ExtractionError as exc:
            logs_repo.add_step_log(
                db, job_id=job.id, entity_type=queue.ENTITY_MAIL, entity_id=email.id, step_name=STEP,
                result=StepResult.ERROR, occurred_at=datetime.now(UTC), duration_ms=elapsed_ms(started), error_message=str(exc),
            )
            await retry_job(db, job, str(exc))
            return
        decision = classification.decide(verdict, qualification)

    if not await queue.hold(db, job):  # a worker that lost its job writes nothing
        return
    label = apply_decision(db, email, decision, by="Triage")
    labels = (label,) if label else ()
    handed = None
    if decision.route is classification.Route.IGNORE and context.thread is not None:
        # B7: a reply in a conversation of ours is never dropped: it becomes a note on it, and its owner is told
        label = GMAIL_LABEL_PROCESSED_DISPUTE
        email.label_applied, labels = label, (label,)
        await threads.note_reply(
            db, context.thread, email, threads.reply_note("general"),
            kind=DisputeMessageKind.INBOUND_EMAIL, excerpt=mail.body,
        )
        logs_repo.add_state_log(
            db, entity_type="ingested_email", entity_id=email.id, from_state=EmailStatus.PROCESSED.value,
            to_state=EmailStatus.PROCESSED.value,
            reason=f"Added to the conversation of dispute {context.thread.reference} (found by {context.thread.how})",
            occurred_at=datetime.now(UTC),
        )
    if decision.route is classification.Route.AUTO:  # a confident payment / dispute / both / PO: its jobs, now
        handed = await handoff.hand_off(db, email, decision.mail_class)
        labels = handed.labels
    if model:
        jobs_repo.update_job(job, model_used=model)
    logs_repo.add_step_log(
        db, job_id=job.id, entity_type=queue.ENTITY_MAIL, entity_id=email.id, step_name=STEP,
        result=StepResult.SUCCESS if decision.route is not classification.Route.HUMAN else StepResult.WARNING,
        occurred_at=datetime.now(UTC), duration_ms=elapsed_ms(started), model_used=model,
        output_summary={
            **summary(decision, context, verdict, qualification), "attempts": attempts,
            "handed_off_jobs": len(handed.jobs) if handed else None,
        },
    )
    await finish_job(db, job, JobState.SUCCEEDED)
    await unit_of_work.commit(db)
    await handoff.label_all(email, labels)
