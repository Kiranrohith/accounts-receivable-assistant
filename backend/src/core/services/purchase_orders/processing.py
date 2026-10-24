"""The purchase-order steps of the document pipeline (after parsing and classification).

LLM extraction, validate, customer match, route, recorded in step_log like the invoice steps.
The job ends Succeeded even when the verdict is FAIL or REVIEW: a business outcome is not a
job failure.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.processing import finish_job, log_step, retry_job
from core.services.jobs import queue
from core.services.purchase_orders.extraction import ExtractionError, extract_purchase_order
from core.services.purchase_orders.routing import PODisposition, route_extracted_po
from core.services.purchase_orders.validation import validate_purchase_order
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import JobState, StepResult
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.platform import jobs_repo
from schemas.invoices.validation import Outcome

VALIDATE_STEP = "validate"
MATCH_STEP = "customer_match"
ROUTE_STEP = "route"

_DISPOSITION_TO_STEP_RESULT = {
    PODisposition.PO_CREATED: StepResult.SUCCESS,
    PODisposition.REVIEW: StepResult.WARNING,
    PODisposition.REJECTED: StepResult.ERROR,
}
_VERDICT_TO_STEP_RESULT = {
    Outcome.PASS: StepResult.SUCCESS,
    Outcome.REVIEW: StepResult.WARNING,
    Outcome.FAIL: StepResult.ERROR,
}


async def run_po_steps(
    db: AsyncSession,
    job: Job,
    document: SourceDocument,
    markdown: str,
) -> None:
    """extract, validate, match, route (everything after parsing and classification)."""
    # --- extract ---
    started = datetime.now(UTC)
    try:
        outcome = await extract_purchase_order(markdown)
    except ExtractionError as exc:
        log_step(
            db, job, document.id, started, step_name="extract", result=StepResult.ERROR,
            error_message=str(exc),
        )
        await retry_job(db, job, str(exc))
        return

    po = outcome.purchase_order
    documents_repo.save_extraction(
        document, po.model_dump(mode="json"), po.confidence.model_dump(mode="json")
    )
    jobs_repo.update_job(job, model_used=outcome.model_used)
    log_step(
        db, job, document.id, started, step_name="extract", result=StepResult.SUCCESS,
        output_summary={
            "po_number": po.po_number,
            "attempts": outcome.attempts,
        },
        model_used=outcome.model_used,
    )

    if outcome.tax_id_note:  # a tax ID that failed its shape was left out: say so in the log, the document goes on
        log_step(
            db, job, document.id, started, step_name="tax_id", result=StepResult.WARNING,
            output_summary={"note": outcome.tax_id_note},
        )

    # --- validate ---
    started = datetime.now(UTC)
    verdict = validate_purchase_order(po)
    log_step(
        db, job, document.id, started, step_name=VALIDATE_STEP,
        result=_VERDICT_TO_STEP_RESULT[verdict.outcome],
        output_summary=verdict.model_dump(mode="json"),
    )
    await unit_of_work.commit(db)  # extraction and verdict are safe before business rows

    # --- match + route ---
    if not await queue.hold(db, job):  # see run_invoice_steps: no business rows from a lost job
        return
    started = datetime.now(UTC)
    route = await route_extracted_po(db, document, po, verdict)
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
            "po_id": str(route.po_id) if route.po_id else None,
            "pending_change_id": str(route.pending_change_id) if route.pending_change_id else None,
            "rejected_invoice_id": (
                str(route.rejected_invoice_id) if route.rejected_invoice_id else None
            ),
            "linked_invoices": route.linked_invoices,
        },
    )
    await unit_of_work.commit(db)
    await finish_job(db, job, JobState.SUCCEEDED)
