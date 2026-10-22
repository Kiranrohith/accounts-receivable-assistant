"""The combined intake pipeline for one uploaded document (invoice, purchase order, payment advice
or customer complaint).

parse once -> classify (invoice | po | payment | dispute | other | unclear + confidence) -> route:
  invoice / po / payment / dispute, confident -> that kind's extraction, validation, matching, routing
  a complaint that also holds a payment advice (or the reverse) -> BOTH, the dispute first
  other, confident          -> rejected-uploads entry, file kept, no extraction
  low confidence / unclear  -> doc-type review queue (a person picks the type or rejects)

A person's choice from the queue re-enters here as a job of type invoice_extraction, po_extraction,
payment_extraction, dispute_extraction or dispute_payment_extraction, which skips the classifier.
Whatever the route, the job ends Succeeded: a business outcome is not a job failure. A failure that
may pass (every model down) is retried by the queue.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import PAYMENT_AFTER_DISPUTE_SECONDS, TRIAGE_HUMAN_THRESHOLD
from core.services.disputes.processing import run_dispute_steps
from core.services.documents.classification import (
    ClassificationOutcome,
    ExtractionError,
    Route,
    classify_document,
    decide_route,
    kinds_for,
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
from core.services.payments.processing import resume_matching, run_payment_steps
from core.services.purchase_orders.processing import run_po_steps
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import DocKind, JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo
from schemas.documents.classification import DocClassification

CLASSIFY_STEP = "classify"
ROUTE_STEP = "route"

# A job of these types was queued by a person choosing the kind(s): the classifier is skipped.
# The dispute always runs before the payment.
_DECIDED_KINDS: dict[str, list[DocKind]] = {
    queue.JOB_INVOICE: [DocKind.INVOICE],
    queue.JOB_PO: [DocKind.PO],
    queue.JOB_PAYMENT: [DocKind.PAYMENT],
    queue.JOB_DISPUTE: [DocKind.DISPUTE],
    queue.JOB_DISPUTE_PAYMENT: [DocKind.DISPUTE, DocKind.PAYMENT],
}


def _intended_kinds(job: Job, document: SourceDocument) -> list[DocKind]:
    """What this document is to be processed as, from a person's choice or the stored verdict."""
    decided = _DECIDED_KINDS.get(job.job_type)
    if decided:
        return decided
    verdict = (document.raw_extraction_json or {}).get("classification")
    return kinds_for(DocClassification.model_validate(verdict)) if verdict else []


async def handle(db: AsyncSession, job: Job, document: SourceDocument) -> None:
    """Job handler for document_intake and the kind-decided extraction jobs."""
    if (
        document.ingested_email_id is not None
        and document.doc_kind is DocKind.PAYMENT
        and await jobs_repo.dispute_jobs_in_flight(db, document.ingested_email_id, job.id)
    ):  # the same mail also holds a complaint: it is raised first, so the hold on money is in place
        await queue.defer(db, job, PAYMENT_AFTER_DISPUTE_SECONDS, "waiting for the dispute raised from the same mail")
        return
    kinds_left: list[DocKind] | None = None
    if (job.attempt_count or 0) > 1 and await documents_repo.has_outcome_since(
        db, document.id, job.queued_at
    ):
        # An earlier attempt got as far as writing an outcome before its worker died: do not
        # write it twice. (A payment still waiting to be matched is the one thing left to do.)
        if document.doc_kind is DocKind.PAYMENT and await resume_matching(db, job, document):
            return
        intended = _intended_kinds(job, document)
        if (
            DocKind.DISPUTE in intended
            and DocKind.PAYMENT in intended
            and document.id not in await payments_repo.payments_by_document(db, [document.id])
        ):
            kinds_left = intended  # the complaint was raised, its payment was not: carry on
        else:
            await finish_job(db, job, JobState.SUCCEEDED)
            return
    decided = kinds_left or _DECIDED_KINDS.get(job.job_type)
    await _run_steps(db, job, document, decided, job.created_by if decided else None)


async def _run_steps(
    db: AsyncSession,
    job: Job,
    document: SourceDocument,
    decided_kinds: list[DocKind] | None,
    decided_by: uuid.UUID | None,
) -> None:
    parsed = await parse_step(db, job, document)
    if parsed is None:
        return

    if decided_kinds is not None:
        kinds = decided_kinds
        log_step(
            db, job, document.id, datetime.now(UTC), step_name=CLASSIFY_STEP,
            result=StepResult.SUCCESS,
            output_summary={
                "source": "human",
                "doc_class": kinds[-1].value,
                "kinds": [k.value for k in kinds],
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
        kinds = kinds_for(outcome.result)
        if document.ingested_email_id is not None and kinds == [DocKind.INVOICE]:
            # an invoice that arrived by mail is out of scope (a person decides what it is): never created on its own
            if not await queue.hold(db, job):
                return
            started = datetime.now(UTC)
            change_id, reasons = await send_to_type_review(
                db, document, outcome.result,
                ["An invoice that arrived by e-mail is not read automatically: please say what this document is"],
            )
            log_step(
                db, job, document.id, started, step_name=ROUTE_STEP, result=StepResult.WARNING,
                output_summary={"disposition": "review", "stage": "classification", "reasons": reasons, "pending_change_id": str(change_id)},
            )
            await unit_of_work.commit(db)
            await finish_job(db, job, JobState.SUCCEEDED)
            return

    if DocKind.DISPUTE in kinds:  # the complaint first, so a payment in the same document sees it
        with_payment = DocKind.PAYMENT in kinds
        documents_repo.set_kind(document, DocKind.PAYMENT if with_payment else DocKind.DISPUTE)
        await unit_of_work.commit(db)
        if not await run_dispute_steps(db, job, document, parsed.markdown, primary=not with_payment):
            return  # handed back for a retry, or the job was lost
        if not with_payment:
            await finish_job(db, job, JobState.SUCCEEDED)
            return
    if DocKind.PAYMENT in kinds:
        documents_repo.set_kind(document, DocKind.PAYMENT)
        await unit_of_work.commit(db)
        await run_payment_steps(db, job, document, parsed.markdown)
    elif DocKind.PO in kinds:
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
        result=(
            StepResult.WARNING if route in (Route.REJECT_OTHER, Route.REVIEW) else StepResult.SUCCESS
        ),
        output_summary={
            "source": "model",
            "doc_class": outcome.result.doc_class.value,
            "confidence": outcome.result.confidence,
            "also_payment": outcome.result.also_payment,
            "also_dispute": outcome.result.also_dispute,
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
