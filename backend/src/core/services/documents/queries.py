"""Read side for uploaded documents of every kind (invoice, purchase order, payment, complaint, neither).

What became of a document (`disposition`) is read from the business tables themselves
(invoice / purchase order / pending review / rejected row), so it stays correct after a review
decision, not just as it was when processing finished.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.exceptions.errors import NotFoundError
from core.services.invoices.processing import ENTITY_TYPE, ROUTE_STEP, VALIDATE_STEP
from data.models.postgres import (
    Dispute,
    Invoice,
    InvoiceVersion,
    Job,
    Payment,
    PendingInvoiceChange,
    PurchaseOrder,
    RejectedInvoice,
    SourceDocument,
    StepLog,
    User,
)
from data.models.postgres.enums import DisputeStatus, DocKind, JobState, PaymentState, PendingChangeStatus
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import jobs_repo, logs_repo
from data.repositories.purchase_orders import purchase_orders_repo
from schemas.common import Page
from schemas.documents.uploads import DocumentUploadDetail, DocumentUploadSummary
from schemas.disputes.extraction import ExtractedDispute
from schemas.invoices.extraction import ExtractedInvoice
from schemas.invoices.responses import StepOut
from schemas.invoices.validation import ValidationResult
from schemas.payments.extraction import ExtractedPayment
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

_STATUS = {"Queued": "Running", "Running": "Running", "Succeeded": "Succeeded"}


@dataclass
class Outcome:
    disposition: str | None = None  # invoice_created / po_created / payment_* / review / rejected / None
    invoice_id: uuid.UUID | None = None
    invoice_version: int | None = None
    po_hold: bool = False
    purchase_order_id: uuid.UUID | None = None
    review_id: uuid.UUID | None = None
    review_kind: str | None = None  # document_type / extraction / version / payment
    payment_id: uuid.UUID | None = None
    payment_state: str | None = None
    customer_id: uuid.UUID | None = None
    dispute_ids: list[uuid.UUID] = field(default_factory=list)  # complaints this document raised
    reasons: list[str] = field(default_factory=list)


def _uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _outcome(
    invoice: Invoice | None,
    order: PurchaseOrder | None,
    pending: PendingInvoiceChange | None,
    rejected: RejectedInvoice | None,
    route: dict,
    superseded: InvoiceVersion | None = None,
    payment: Payment | None = None,
    disputes: list[Dispute] | None = None,
) -> Outcome:
    if invoice is not None:
        return Outcome(
            "version_applied" if invoice.version > 1 else "invoice_created",
            invoice_id=invoice.id,
            invoice_version=invoice.version,
            customer_id=invoice.customer_id,
            po_hold=invoice.po_hold,
        )
    if superseded is not None:  # its invoice has since moved on to a newer version
        return Outcome(
            "version_applied" if superseded.version_no > 1 else "invoice_created",
            invoice_id=superseded.invoice_id,
            invoice_version=superseded.version_no,
            customer_id=_uuid((superseded.snapshot or {}).get("customer_id")),
        )
    if order is not None:
        return Outcome("po_created", purchase_order_id=order.id, customer_id=order.customer_id)
    if payment is not None:  # a complaint in the same document is listed beside the payment
        outcome = _payment_outcome(payment)
        outcome.dispute_ids = [d.id for d in disputes or []]
        return outcome
    if disputes:
        return _dispute_outcome(disputes)
    if pending is not None and pending.status is PendingChangeStatus.PENDING:
        snapshot = pending.proposed_snapshot or {}
        return Outcome(
            "review",
            review_id=pending.id,
            review_kind={"doc_type": "document_type", "invoice_version": "version"}.get(
                snapshot.get("kind"), "extraction"
            ),
            customer_id=_uuid(snapshot.get("matched_customer_id")),
            reasons=snapshot.get("reasons", []),
        )
    if rejected is not None:
        return Outcome(
            "rejected",
            customer_id=_uuid(route.get("customer_id")),
            reasons=[r.get("message", "") for r in (rejected.rejection_reasons or [])],
        )
    return Outcome()


def _dispute_outcome(disputes: list[Dispute]) -> Outcome:
    waiting = [d for d in disputes if d.status is DisputeStatus.RAISED]
    common = dict(
        dispute_ids=[d.id for d in disputes],
        customer_id=next((d.customer_id for d in disputes if d.customer_id), None),
    )
    if waiting:  # nobody has classified it yet: it sits in the classify queue
        return Outcome(
            "dispute_queued", review_id=waiting[0].id, review_kind="dispute_classification",
            reasons=["Waiting in the dispute classify queue"], **common,
        )
    return Outcome("dispute_created", **common)


_PAYMENT_DISPOSITION = {
    PaymentState.APPLIED: "payment_applied",
    PaymentState.PARTIALLY_APPLIED: "payment_partly_applied",
    PaymentState.UNAPPLIED: "payment_unapplied",
}


def _payment_outcome(payment: Payment) -> Outcome:
    common = dict(
        payment_id=payment.id, payment_state=payment.state.value, customer_id=payment.customer_id
    )
    if payment.state is PaymentState.NEEDS_REVIEW:
        return Outcome(
            "review", review_id=payment.id, review_kind="payment",
            reasons=list(payment.review_reasons or []), **common,
        )
    if payment.state is PaymentState.REJECTED:
        return Outcome("rejected", reasons=[payment.match_reason or "Rejected"], **common)
    # Received / Reading / Matching are on their way; the others are decided outcomes
    return Outcome(_PAYMENT_DISPOSITION.get(payment.state, "payment_received"), **common)


def plain_status(processing_status: str, outcome: Outcome) -> str:
    """One label for a user, from what really happened to the document."""
    if processing_status == "Failed":
        return "Failed"
    if processing_status == "Running":
        return "Processing"  # also true on a re-run, when an older outcome still exists
    labels = {
        "invoice_created": "Invoice created",
        "version_applied": "New version applied",
        "rejected": "Rejected",
        "po_created": "Purchase order created",
        "dispute_created": "Dispute created",
        "dispute_queued": "Dispute needs classification",
        "payment_received": "Payment received",
        "payment_applied": "Payment applied",
        "payment_partly_applied": "Payment partly applied",
        "payment_unapplied": "Payment unapplied",
        "review": "Needs review",
    }
    return labels.get(outcome.disposition or "", "Processing")  # queued, or no outcome yet


def _extraction_fields(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if k not in ("classification", "dispute_extraction")}


def _kind(document: SourceDocument, outcome: Outcome, raw: dict) -> str:
    if document.doc_kind is DocKind.PO:
        return "po"
    if document.doc_kind is DocKind.PAYMENT:
        return "payment"
    if document.doc_kind is DocKind.DISPUTE:
        return "dispute"
    if outcome.disposition in ("invoice_created", "version_applied") or _extraction_fields(raw):
        return "invoice"
    classification = raw.get("classification") or {}
    if (
        classification.get("doc_class") == "invoice"
        and (classification.get("confidence") or 0) >= TRIAGE_HUMAN_THRESHOLD
    ):
        return "invoice"
    return "unknown"


def _validation(step: StepLog | None) -> ValidationResult | None:
    if step is None or not step.output_summary:
        return None
    return ValidationResult.model_validate(step.output_summary)


def _summary(
    document: SourceDocument, job: Job | None, validation: ValidationResult | None, outcome: Outcome
) -> DocumentUploadSummary:
    raw = document.raw_extraction_json or {}
    classification = raw.get("classification") or {}
    status = "Pending" if job is None else _STATUS.get(job.state.value, "Failed")
    retrying = job is not None and job.state is JobState.QUEUED and (job.attempt_count or 0) > 0
    return DocumentUploadSummary(
        source_document_id=document.id,
        status=plain_status(status, outcome),
        document_kind=_kind(document, outcome, raw),
        file_name=document.file_name,
        doc_type=document.doc_type.value if document.doc_type else None,
        uploaded_at=document.created_at,
        processing_status=status,
        validation_outcome=validation.outcome.value if validation else None,
        disposition=outcome.disposition,
        invoice_id=outcome.invoice_id,
        invoice_version=outcome.invoice_version,
        po_hold=outcome.po_hold,
        purchase_order_id=outcome.purchase_order_id,
        review_id=outcome.review_id,
        review_kind=outcome.review_kind,
        payment_id=outcome.payment_id,
        payment_state=outcome.payment_state,
        dispute_id=outcome.dispute_ids[0] if outcome.dispute_ids else None,
        dispute_ids=outcome.dispute_ids,
        detected_type=classification.get("doc_class"),
        detected_confidence=classification.get("confidence"),
        customer_id=outcome.customer_id,
        error=job.last_error_message if job and (status == "Failed" or retrying) else None,
        retry_at=job.run_after if retrying else None,
        invoice_number=raw.get("invoice_number"),
        po_number=raw.get("po_number"),
        customer_name=raw.get("customer_name") or raw.get("payer_name"),
        total_amount=raw.get("total_amount") or raw.get("amount"),
        reference_number=raw.get("reference_number"),
        disposition_reasons=outcome.reasons,
    )


async def _outcomes(
    db: AsyncSession, ids: list[uuid.UUID], routes: dict[uuid.UUID, StepLog]
) -> dict[uuid.UUID, Outcome]:
    invoices = await invoices_repo.invoices_by_document(db, ids)
    orders = await purchase_orders_repo.purchase_orders_by_document(db, ids)
    pending = await invoices_repo.latest_pending_by_document(db, ids)
    rejected = await invoices_repo.rejected_by_document(db, ids)
    superseded = await invoices_repo.superseded_by_document(db, ids)
    payments = await payments_repo.payments_by_document(db, ids)
    disputes = await disputes_repo.disputes_by_documents(db, ids)
    return {
        i: _outcome(
            invoices.get(i),
            orders.get(i),
            pending.get(i),
            rejected.get(i),
            (routes[i].output_summary or {}) if i in routes else {},
            superseded.get(i),
            payments.get(i),
            disputes.get(i),
        )
        for i in ids
    }


async def _summaries(db: AsyncSession, documents: list[SourceDocument]) -> list[DocumentUploadSummary]:
    ids = [d.id for d in documents]
    jobs = await jobs_repo.latest_jobs_for_entities(db, None, ids)
    verdicts = await logs_repo.latest_step_for_entities(db, ENTITY_TYPE, VALIDATE_STEP, ids)
    routes = await logs_repo.latest_step_for_entities(db, ENTITY_TYPE, ROUTE_STEP, ids)
    outcomes = await _outcomes(db, ids, routes)
    return [
        _summary(d, jobs.get(d.id), _validation(verdicts.get(d.id)), outcomes[d.id])
        for d in documents
    ]


async def summarize(db: AsyncSession, documents: list[SourceDocument]) -> list[DocumentUploadSummary]:
    """The upload-list row of each of these documents (what it is so far, and where it went)."""
    return await _summaries(db, documents)


async def list_uploads(db: AsyncSession, limit: int = 100) -> list[DocumentUploadSummary]:
    return await _summaries(db, await documents_repo.list_uploaded_documents(db, limit, None))


async def list_my_uploads(db: AsyncSession, user: User, *, limit: int, offset: int) -> Page[DocumentUploadSummary]:
    """The person's own uploads, newest first, one page and the total."""
    documents = await documents_repo.list_uploaded_documents(db, limit, None, offset=offset, created_by=user.id)
    total = await documents_repo.count_uploaded_documents(db, user.id)
    return Page[DocumentUploadSummary](items=await _summaries(db, documents), total=total)


def _extraction(kind: str, raw: dict) -> dict | None:
    fields = _extraction_fields(raw)
    if not fields:
        return None
    model = {"po": ExtractedPurchaseOrder, "payment": ExtractedPayment, "dispute": ExtractedDispute}.get(
        kind, ExtractedInvoice
    )
    try:
        return model.model_validate(fields).model_dump(mode="json")
    except ValueError:  # an extraction of the other shape: show what was stored
        return fields


async def get_upload(db: AsyncSession, document_id: uuid.UUID) -> DocumentUploadDetail:
    document = await documents_repo.get_document(db, document_id)
    if document is None:
        raise NotFoundError("Uploaded document not found")
    job = (await jobs_repo.latest_jobs_for_entities(db, None, [document.id])).get(document.id)
    steps = await logs_repo.list_step_logs(db, ENTITY_TYPE, document.id)
    validation = _validation(next((s for s in reversed(steps) if s.step_name == VALIDATE_STEP), None))
    route_step = next((s for s in reversed(steps) if s.step_name == ROUTE_STEP), None)
    outcome = (await _outcomes(db, [document.id], {document.id: route_step} if route_step else {}))[
        document.id
    ]
    summary = _summary(document, job, validation, outcome)
    raw = document.raw_extraction_json or {}
    return DocumentUploadDetail(
        **summary.model_dump(),
        extraction=_extraction(summary.document_kind, raw),
        classification=raw.get("classification"),
        dispute_extraction=raw.get("dispute_extraction"),
        validation_issues=validation.issues if validation else [],
        linked_invoices=(
            (route_step.output_summary or {}).get("linked_invoices", [])
            if route_step and summary.document_kind == "po"
            else []
        ),
        steps=[
            StepOut(
                step_name=s.step_name,
                result=s.result.value if s.result else None,
                duration_ms=s.duration_ms,
                model_used=s.model_used,
                error_message=s.error_message,
                occurred_at=s.occurred_at,
                output_summary=s.output_summary,
            )
            for s in steps
        ],
    )
