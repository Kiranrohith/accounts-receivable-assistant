"""Shared building blocks of the document pipelines, plus the invoice steps.

Every uploaded document goes through core.services.documents.intake (parse, classify, route),
run by a worker from the job queue (core.services.jobs). This module holds what the kinds share
(ending a job, the parse step, step logging) and the invoice pipeline itself: extract, validate,
match a customer, route. Progress and failures are queryable from jobs, state_log and step_log.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.extraction import ExtractionError, extract_invoice
from core.services.jobs import queue
from core.services.jobs.queue import ENTITY_TYPE
from core.services.invoices.parsing import InvoiceParseError, ParsedDocument, parse_document
from core.services.invoices.routing import Disposition, route_extracted_invoice
from core.services.invoices.validation import validate_extraction
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import ErrorClass, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.platform import jobs_repo, logs_repo
from schemas.invoices.validation import Outcome

VALIDATE_STEP = "validate"
MATCH_STEP = "customer_match"
ROUTE_STEP = "route"

_DISPOSITION_TO_STEP_RESULT = {
    Disposition.INVOICE_CREATED: StepResult.SUCCESS,
    Disposition.VERSION_APPLIED: StepResult.SUCCESS,
    Disposition.REVIEW: StepResult.WARNING,
    Disposition.REJECTED: StepResult.ERROR,
}
_VERDICT_TO_STEP_RESULT = {
    Outcome.PASS: StepResult.SUCCESS,
    Outcome.REVIEW: StepResult.WARNING,
    Outcome.FAIL: StepResult.ERROR,
}

# Docling's converter is shared and heavy: parse one document at a time per process.
PARSE_LOCK = asyncio.Semaphore(1)


def elapsed_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


async def finish_job(
    db: AsyncSession,
    job: Job,
    state: JobState,
    *,
    error_class: ErrorClass | None = None,
    error: str | None = None,
) -> None:
    await queue.finish(db, job, state, error_class=error_class, error=error)


async def retry_job(db: AsyncSession, job: Job, error: str) -> None:
    """A retryable failure (every model failed, a service is down): the queue retries it later
    with backoff, and dead-letters it when the attempts run out."""
    await queue.retry_or_dead_letter(db, job, error)


def log_step(db: AsyncSession, job: Job, document_id: uuid.UUID, started: datetime, **fields) -> None:  # noqa: ANN003
    logs_repo.add_step_log(
        db,
        job_id=job.id,
        entity_type=ENTITY_TYPE,
        entity_id=document_id,
        occurred_at=datetime.now(UTC),
        duration_ms=elapsed_ms(started),
        **fields,
    )


async def parse_step(db: AsyncSession, job: Job, document: SourceDocument) -> ParsedDocument | None:
    """Parse the file once. Returns the parsed document, or None after failing the job."""
    started = datetime.now(UTC)
    try:
        async with PARSE_LOCK:
            parsed = await parse_document(document.file_path)
    except InvoiceParseError as exc:
        log_step(
            db, job, document.id, started, step_name="parse", result=StepResult.ERROR,
            input_summary={"file_name": document.file_name}, error_message=str(exc),
        )
        await finish_job(
            db, job, JobState.FAILED, error_class=ErrorClass.NON_RETRYABLE, error=str(exc)
        )
        return None
    log_step(
        db, job, document.id, started, step_name="parse", result=StepResult.SUCCESS,
        input_summary={"file_name": document.file_name},
        output_summary={"page_count": parsed.page_count, "characters": len(parsed.markdown)},
    )
    await unit_of_work.commit(db)
    return parsed


async def run_invoice_steps(
    db: AsyncSession, job: Job, document: SourceDocument, markdown: str
) -> None:
    """extract, validate, match, route (everything after parsing and classification)."""
    # --- extract ---
    started = datetime.now(UTC)
    try:
        outcome = await extract_invoice(markdown)
    except ExtractionError as exc:
        log_step(
            db, job, document.id, started, step_name="extract", result=StepResult.ERROR,
            error_message=str(exc),
        )
        await retry_job(db, job, str(exc))
        return

    invoice = outcome.invoice
    documents_repo.save_extraction(
        document,
        invoice.model_dump(mode="json"),
        invoice.confidence.model_dump(mode="json"),
    )
    jobs_repo.update_job(job, model_used=outcome.model_used)
    log_step(
        db, job, document.id, started, step_name="extract", result=StepResult.SUCCESS,
        output_summary={"invoice_number": invoice.invoice_number, "attempts": outcome.attempts},
        model_used=outcome.model_used,
    )

    # --- validate --- the verdict is a business outcome, not a job failure: the job
    # still Succeeds (the document was processed); FAIL/REVIEW are recorded on the step.
    started = datetime.now(UTC)
    verdict = validate_extraction(invoice)
    log_step(
        db, job, document.id, started, step_name=VALIDATE_STEP,
        result=_VERDICT_TO_STEP_RESULT[verdict.outcome],
        output_summary=verdict.model_dump(mode="json"),
    )
    # Extraction and verdict are safe in the database before routing writes business rows.
    await unit_of_work.commit(db)

    # --- match + route ---
    # Take the job's row lock before any business row is written: the watchdog skips a locked job,
    # so it cannot be reclaimed (and run a second time) halfway through, and a worker that already
    # lost its job writes nothing.
    if not await queue.hold(db, job):
        return
    started = datetime.now(UTC)
    route = await route_extracted_invoice(db, document, invoice, verdict)
    if route.match is not None:
        log_step(
            db, job, document.id, started, step_name=MATCH_STEP,
            result=StepResult.SUCCESS if route.match.matched else StepResult.WARNING,
            output_summary={
                "matched": route.match.matched,
                "method": route.match.method,
                "customer_id": str(route.customer_id) if route.customer_id else None,
                "issues": route.match.issues,
            },
        )
    log_step(
        db, job, document.id, started, step_name=ROUTE_STEP,
        result=_DISPOSITION_TO_STEP_RESULT[route.disposition],
        output_summary={
            "disposition": route.disposition.value,
            "reasons": route.reasons,
            "customer_id": str(route.customer_id) if route.customer_id else None,
            "match_method": route.match.method if route.match else None,
            "invoice_id": str(route.invoice_id) if route.invoice_id else None,
            "version": route.version,
            "fx": route.fx,
            "pending_change_id": str(route.pending_change_id) if route.pending_change_id else None,
            "rejected_invoice_id": (
                str(route.rejected_invoice_id) if route.rejected_invoice_id else None
            ),
        },
    )
    await unit_of_work.commit(db)
    await finish_job(db, job, JobState.SUCCEEDED)
