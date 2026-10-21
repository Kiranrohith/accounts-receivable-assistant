"""The payment-advice steps of the document pipeline (after parsing and classification).

LLM extraction, validate, identify the payer, route: recorded in step_log like the invoice and
purchase-order steps. The job ends Succeeded even when the payment goes to review or is rejected
as a duplicate: a business outcome is not a job failure.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.processing import finish_job, log_step, retry_job
from core.services.jobs import queue
from core.services.payments.engine import MatchOutcome, Status, run_matching
from core.services.payments.extraction import ExtractionError, extract_payment
from core.services.payments.routing import PaymentDisposition, route_extracted_payment
from core.services.payments.validation import validate_payment
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import JobState, PaymentState, StepResult
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo
from schemas.invoices.validation import Outcome

VALIDATE_STEP = "validate"
IDENTIFY_STEP = "identify"
ROUTE_STEP = "route"
MATCH_STEP = "match"

_DISPOSITION_TO_STEP_RESULT = {
    PaymentDisposition.MATCHING: StepResult.SUCCESS,
    PaymentDisposition.REVIEW: StepResult.WARNING,
    PaymentDisposition.REJECTED: StepResult.ERROR,
}
_VERDICT_TO_STEP_RESULT = {
    Outcome.PASS: StepResult.SUCCESS,
    Outcome.REVIEW: StepResult.WARNING,
    Outcome.FAIL: StepResult.ERROR,
}


async def run_payment_steps(
    db: AsyncSession, job: Job, document: SourceDocument, markdown: str
) -> None:
    """extract, validate, identify, route (everything after parsing and classification)."""
    # --- extract ---
    started = datetime.now(UTC)
    try:
        outcome = await extract_payment(markdown)
    except ExtractionError as exc:
        log_step(
            db, job, document.id, started, step_name="extract", result=StepResult.ERROR,
            error_message=str(exc),
        )
        await retry_job(db, job, str(exc))
        return

    payment = outcome.payment
    documents_repo.save_extraction(
        document, payment.model_dump(mode="json"), payment.confidence.model_dump(mode="json")
    )
    jobs_repo.update_job(job, model_used=outcome.model_used)
    log_step(
        db, job, document.id, started, step_name="extract", result=StepResult.SUCCESS,
        output_summary={
            "reference_number": payment.reference_number,
            "amount": str(payment.amount) if payment.amount is not None else None,
            "currency": payment.currency,
            "attempts": outcome.attempts,
        },
        model_used=outcome.model_used,
    )

    # --- validate ---
    started = datetime.now(UTC)
    verdict = validate_payment(payment)
    log_step(
        db, job, document.id, started, step_name=VALIDATE_STEP,
        result=_VERDICT_TO_STEP_RESULT[verdict.outcome],
        output_summary=verdict.model_dump(mode="json"),
    )
    await unit_of_work.commit(db)  # extraction and verdict are safe before business rows

    # --- identify + route ---
    if not await queue.hold(db, job):  # see run_invoice_steps: no business rows from a lost job
        return
    started = datetime.now(UTC)
    route = await route_extracted_payment(db, document, payment, verdict)
    identity = route.identity
    if identity is not None:
        log_step(
            db, job, document.id, started, step_name=IDENTIFY_STEP,
            result=StepResult.SUCCESS if identity.matched else StepResult.WARNING,
            output_summary={
                "matched": identity.matched,
                "layer": identity.layer.value,
                "customer_id": str(route.customer_id) if route.customer_id else None,
                "issues": identity.issues,
                "unknown_invoice_numbers": identity.unknown_numbers,
            },
        )
    log_step(
        db, job, document.id, started, step_name=ROUTE_STEP,
        result=_DISPOSITION_TO_STEP_RESULT[route.disposition],
        output_summary={
            "disposition": route.disposition.value,
            "payment_id": str(route.payment_id),
            "state": route.state.value,
            "reasons": route.reasons,
            "customer_id": str(route.customer_id) if route.customer_id else None,
            "layer": identity.layer.value if identity else None,
            "duplicate_of": str(route.duplicate_of) if route.duplicate_of else None,
        },
    )
    await unit_of_work.commit(db)
    if route.disposition is PaymentDisposition.MATCHING:
        await match_payment_step(db, job, document, route.payment_id)
        return
    await finish_job(db, job, JobState.SUCCEEDED)


_MATCH_TO_STEP_RESULT = {
    Status.APPLIED: StepResult.SUCCESS,
    Status.PARKED: StepResult.WARNING,
    Status.REVIEW: StepResult.WARNING,
    Status.NOT_WAITING: StepResult.SUCCESS,
}


def match_summary(outcome: MatchOutcome) -> dict:
    return {
        "status": outcome.status.value,
        "state": outcome.state.value if outcome.state else None,
        "layer": outcome.layer.value if outcome.layer else None,
        "score": outcome.score,
        "reasons": outcome.reasons,
        "allocations": outcome.allocations,
        "credit_note": outcome.note_number,
        "credit_spent": outcome.credit_spent,
        "held": outcome.held,
    }


async def resume_matching(db: AsyncSession, job: Job, document: SourceDocument) -> bool:
    """A re-attempt found the payment written but never matched (its worker died in between):
    match it now. Returns False when there is nothing to resume."""
    payment = (await payments_repo.payments_by_document(db, [document.id])).get(document.id)
    if payment is None or payment.state is not PaymentState.MATCHING:
        return False
    await match_payment_step(db, job, document, payment.id)
    return True


async def match_payment_step(
    db: AsyncSession, job: Job, document: SourceDocument, payment_id
) -> None:
    """Match and apply an identified payment, then end the job (the outcome is business, not failure)."""
    if not await queue.hold(db, job):  # a worker that lost its job applies no money
        return
    started = datetime.now(UTC)
    outcome = await run_matching(db, payment_id, job.created_by)
    log_step(
        db, job, document.id, started, step_name=MATCH_STEP,
        result=_MATCH_TO_STEP_RESULT[outcome.status], output_summary=match_summary(outcome),
    )
    await unit_of_work.commit(db)
    await finish_job(db, job, JobState.SUCCEEDED)
