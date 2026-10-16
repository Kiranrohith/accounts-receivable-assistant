"""Read side for uploaded documents of every kind (invoice, purchase order, neither).

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
    Invoice,
    Job,
    PendingInvoiceChange,
    PurchaseOrder,
    RejectedInvoice,
    SourceDocument,
    StepLog,
)
from data.models.postgres.enums import DocKind, PendingChangeStatus
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.platform import jobs_repo, logs_repo
from data.repositories.purchase_orders import purchase_orders_repo
from schemas.documents.uploads import DocumentUploadDetail, DocumentUploadSummary
from schemas.invoices.extraction import ExtractedInvoice
from schemas.invoices.responses import StepOut
from schemas.invoices.validation import ValidationResult
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

_STATUS = {"Queued": "Running", "Running": "Running", "Succeeded": "Succeeded"}


@dataclass
class Outcome:
    disposition: str | None = None  # invoice_created / po_created / review / rejected / None
    invoice_id: uuid.UUID | None = None
    po_hold: bool = False
    purchase_order_id: uuid.UUID | None = None
    review_id: uuid.UUID | None = None
    review_kind: str | None = None  # document_type / extraction
    customer_id: uuid.UUID | None = None
    reasons: list[str] = field(default_factory=list)


def _uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _outcome(
    invoice: Invoice | None,
    order: PurchaseOrder | None,
    pending: PendingInvoiceChange | None,
    rejected: RejectedInvoice | None,
    route: dict,
) -> Outcome:
    if invoice is not None:
        return Outcome(
            "invoice_created",
            invoice_id=invoice.id,
            customer_id=invoice.customer_id,
            po_hold=invoice.po_hold,
        )
    if order is not None:
        return Outcome("po_created", purchase_order_id=order.id, customer_id=order.customer_id)
    if pending is not None and pending.status is PendingChangeStatus.PENDING:
        snapshot = pending.proposed_snapshot or {}
        return Outcome(
            "review",
            review_id=pending.id,
            review_kind="document_type" if snapshot.get("kind") == "doc_type" else "extraction",
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


def plain_status(processing_status: str, outcome: Outcome) -> str:
    """One label for a user, from what really happened to the document."""
    if processing_status == "Failed":
        return "Failed"
    if processing_status == "Running":
        return "Processing"  # also true on a re-run, when an older outcome still exists
    labels = {
        "invoice_created": "Invoice created",
        "rejected": "Rejected",
        "po_created": "Purchase order created",
        "review": "Needs review",
    }
    return labels.get(outcome.disposition or "", "Processing")  # queued, or no outcome yet


def _extraction_fields(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if k != "classification"}


def _kind(document: SourceDocument, outcome: Outcome, raw: dict) -> str:
    if document.doc_kind is DocKind.PO:
        return "po"
    if outcome.disposition == "invoice_created" or _extraction_fields(raw):
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
        po_hold=outcome.po_hold,
        purchase_order_id=outcome.purchase_order_id,
        review_id=outcome.review_id,
        review_kind=outcome.review_kind,
        detected_type=classification.get("doc_class"),
        detected_confidence=classification.get("confidence"),
        customer_id=outcome.customer_id,
        error=job.last_error_message if job and status == "Failed" else None,
        invoice_number=raw.get("invoice_number"),
        po_number=raw.get("po_number"),
        customer_name=raw.get("customer_name"),
        total_amount=raw.get("total_amount"),
    )


async def _outcomes(
    db: AsyncSession, ids: list[uuid.UUID], routes: dict[uuid.UUID, StepLog]
) -> dict[uuid.UUID, Outcome]:
    invoices = await invoices_repo.invoices_by_document(db, ids)
    orders = await purchase_orders_repo.purchase_orders_by_document(db, ids)
    pending = await invoices_repo.latest_pending_by_document(db, ids)
    rejected = await invoices_repo.rejected_by_document(db, ids)
    return {
        i: _outcome(
            invoices.get(i),
            orders.get(i),
            pending.get(i),
            rejected.get(i),
            (routes[i].output_summary or {}) if i in routes else {},
        )
        for i in ids
    }


async def list_uploads(db: AsyncSession, limit: int = 100) -> list[DocumentUploadSummary]:
    documents = await documents_repo.list_uploaded_documents(db, limit, None)
    ids = [d.id for d in documents]
    jobs = await jobs_repo.latest_jobs_for_entities(db, None, ids)
    verdicts = await logs_repo.latest_step_for_entities(db, ENTITY_TYPE, VALIDATE_STEP, ids)
    routes = await logs_repo.latest_step_for_entities(db, ENTITY_TYPE, ROUTE_STEP, ids)
    outcomes = await _outcomes(db, ids, routes)
    return [
        _summary(d, jobs.get(d.id), _validation(verdicts.get(d.id)), outcomes[d.id])
        for d in documents
    ]


def _extraction(kind: str, raw: dict) -> dict | None:
    fields = _extraction_fields(raw)
    if not fields:
        return None
    model = ExtractedPurchaseOrder if kind == "po" else ExtractedInvoice
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
        validation_issues=validation.issues if validation else [],
        disposition_reasons=outcome.reasons,
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
