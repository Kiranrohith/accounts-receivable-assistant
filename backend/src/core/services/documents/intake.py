"""The combined intake pipeline for one uploaded document (invoice or purchase order).

parse once -> classify (invoice | po | other | unclear + confidence) -> route:
  invoice / po, confident   -> that kind's extraction, validation, matching and routing
  other, confident          -> rejected-uploads entry, file kept, no extraction
  low confidence / unclear  -> doc-type review queue (a person picks the type or rejects)

A person's choice from the queue re-enters here as a job of type invoice_extraction or
po_extraction, which skips the classifier. Whatever the route, the job ends Succeeded: a business
outcome is not a job failure. A failure that may pass (every model down) is retried by the queue.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.services.documents.classification import (
    ClassificationOutcome,
    ExtractionError,
    Route,
    classify_document,
    decide_route,
)
from core.services.documents.routing import reject_as_other, send_to_type_review
from core.services.invoices.processing import (
    finish_job,
    log_step,
    parse_step,
    retry_job,
    run_invoice_steps,
)
from core.services.jobs import queue
from core.services.purchase_orders.processing import run_po_steps
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import DocKind, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.platform import jobs_repo
from schemas.documents.classification import DocumentClass

CLASSIFY_STEP = "classify"
ROUTE_STEP = "route"

_KIND_TO_CLASS = {DocKind.INVOICE: DocumentClass.INVOICE, DocKind.PO: DocumentClass.PO}
_ROUTE_TO_KIND = {Route.INVOICE: DocKind.INVOICE, Route.PO: DocKind.PO}
# A job of these types was queued by a person choosing the kind: the classifier is skipped.
_DECIDED_KIND = {queue.JOB_INVOICE: DocKind.INVOICE, queue.JOB_PO: DocKind.PO}


async def handle(db: AsyncSession, job: Job, document: SourceDocument) -> None:
    """Job handler for document_intake / invoice_extraction / po_extraction."""
    if (job.attempt_count or 0) > 1 and await documents_repo.has_outcome_since(
        db, document.id, job.queued_at
    ):
        # An earlier attempt got as far as writing the outcome before its worker died: do not
        # write it twice.
        await finish_job(db, job, JobState.SUCCEEDED)
        return
    decided_kind = _DECIDED_KIND.get(job.job_type)
    await _run_steps(db, job, document, decided_kind, job.created_by if decided_kind else None)


async def _run_steps(
    db: AsyncSession,
    job: Job,
    document: SourceDocument,
    decided_kind: DocKind | None,
    decided_by: uuid.UUID | None,
) -> None:
    parsed = await parse_step(db, job, document)
    if parsed is None:
        return

    if decided_kind is not None:
        kind = decided_kind
        log_step(
            db, job, document.id, datetime.now(UTC), step_name=CLASSIFY_STEP,
            result=StepResult.SUCCESS,
            output_summary={
                "source": "human",
                "doc_class": _KIND_TO_CLASS[kind].value,
                "decided_by": str(decided_by) if decided_by else None,
            },
        )
    else:
        started = datetime.now(UTC)
        try:
            outcome = await classify_document(parsed.markdown)
        except ExtractionError as exc:
            log_step(
                db, job, document.id, started, step_name=CLASSIFY_STEP, result=StepResult.ERROR,
                error_message=str(exc),
            )
            await retry_job(db, job, str(exc))
            return
        route = decide_route(outcome.result)
        documents_repo.save_classification(document, outcome.result.model_dump(mode="json"))
        jobs_repo.update_job(job, model_used=outcome.model_used)
        _log_classification(db, job, document, started, outcome, route)
        await unit_of_work.commit(db)

        if route in (Route.REJECT_OTHER, Route.REVIEW):
            await _stop_after_classification(db, job, document, outcome, route)
            return
        kind = _ROUTE_TO_KIND[route]

    if kind is DocKind.PO:
        documents_repo.set_kind(document, DocKind.PO)
        await unit_of_work.commit(db)
        await run_po_steps(db, job, document, parsed.markdown)
    else:
        await run_invoice_steps(db, job, document, parsed.markdown)


def _log_classification(
    db: AsyncSession,
    job: Job,
    document: SourceDocument,
    started: datetime,
    outcome: ClassificationOutcome,
    route: Route,
) -> None:
    log_step(
        db, job, document.id, started, step_name=CLASSIFY_STEP,
        result=StepResult.SUCCESS if route in (Route.INVOICE, Route.PO) else StepResult.WARNING,
        output_summary={
            "source": "model",
            "doc_class": outcome.result.doc_class.value,
            "confidence": outcome.result.confidence,
            "reason": outcome.result.reason,
            "threshold": TRIAGE_HUMAN_THRESHOLD,
            "route": route.value,
            "attempts": outcome.attempts,
        },
        model_used=outcome.model_used,
    )


async def _stop_after_classification(
    db: AsyncSession,
    job: Job,
    document: SourceDocument,
    outcome: ClassificationOutcome,
    route: Route,
) -> None:
    """The two exits that run no field extraction."""
    if not await queue.hold(db, job):  # a worker that lost its job writes no business rows
        return
    started = datetime.now(UTC)
    if route is Route.REJECT_OTHER:
        rejected_id = await reject_as_other(db, document, outcome.result)
        summary = {
            "disposition": "rejected",
            "stage": "classification",
            "reasons": ["neither an invoice nor a purchase order"],
            "rejected_invoice_id": str(rejected_id),
        }
        result = StepResult.ERROR
    else:
        change_id, reasons = await send_to_type_review(db, document, outcome.result)
        summary = {
            "disposition": "review",
            "stage": "classification",
            "reasons": reasons,
            "pending_change_id": str(change_id),
        }
        result = StepResult.WARNING
    log_step(db, job, document.id, started, step_name=ROUTE_STEP, result=result, output_summary=summary)
    await unit_of_work.commit(db)
    await finish_job(db, job, JobState.SUCCEEDED)
