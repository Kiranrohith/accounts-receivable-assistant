"""What becomes of a document the classifier did not send straight to a kind's extraction.

- confidently neither an invoice nor a purchase order -> a rejected-uploads entry (the file is
  kept; no field extraction is run, so no LLM spend)
- doubtful (under the threshold, or "unclear") -> the doc-type review queue: a person picks the
  type or rejects it

Both reuse existing tables: rejected_invoices doubles as the rejected-uploads list, and the
review queue is a pending_invoice_changes row tagged `kind: doc_type` in its snapshot.
Callers commit.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from data.models.postgres import SourceDocument
from data.models.postgres.enums import PendingChangeReason
from data.repositories.invoices import invoices_repo
from schemas.documents.classification import DocClassification, DocumentClass

OTHER_KIND = "other"  # tag on rejected snapshots: neither an invoice nor a purchase order
DOC_TYPE_KIND = "doc_type"  # tag on pending-change snapshots: the document type is undecided

_LABEL = {
    DocumentClass.INVOICE: "an invoice",
    DocumentClass.PO: "a purchase order",
    DocumentClass.PAYMENT: "a payment advice",
    DocumentClass.DISPUTE: "a customer complaint",
    DocumentClass.OTHER: "neither an invoice nor a purchase order",
    DocumentClass.UNCLEAR: "unclear",
}

NOT_INVOICE_OR_PO = "neither an invoice nor a purchase order"


def other_reason(result: DocClassification) -> str:
    return f"This document is {NOT_INVOICE_OR_PO} (it looks like: {result.reason})"


def review_reasons(result: DocClassification) -> list[str]:
    if result.doc_class is DocumentClass.UNCLEAR:
        head = (
            "The reader could not tell whether this is an invoice, a purchase order, "
            "a payment advice, a customer complaint or something else"
        )
    else:
        head = (
            f"The reader thinks this is {_LABEL[result.doc_class]} but is only "
            f"{result.confidence}% sure (needs at least {TRIAGE_HUMAN_THRESHOLD}%)"
        )
    return [head, f"Reader's note: {result.reason}"]


async def reject_as_other(
    db: AsyncSession, document: SourceDocument, result: DocClassification
) -> uuid.UUID:
    rejected = await invoices_repo.insert_rejected(
        db,
        source_document_id=document.id,
        attempted_file_name=document.file_name,
        extracted_snapshot={"kind": OTHER_KIND, "classification": result.model_dump(mode="json")},
        rejection_reasons=[
            {
                "code": "not_invoice_or_po",
                "field": None,
                "severity": "fail",
                "message": other_reason(result),
            }
        ],
        created_by=document.created_by,
        updated_by=document.created_by,
    )
    return rejected.id


async def send_to_type_review(
    db: AsyncSession,
    document: SourceDocument,
    result: DocClassification | None,
    extra_reasons: list[str] | None = None,
) -> tuple[uuid.UUID, list[str]]:
    reasons = [*(extra_reasons or []), *(review_reasons(result) if result else [])]
    change = await invoices_repo.insert_pending_change(
        db,
        invoice_id=None,
        source_document_id=document.id,
        reason=PendingChangeReason.EXTRACTION_REVIEW,
        proposed_snapshot={
            "kind": DOC_TYPE_KIND,
            "classification": result.model_dump(mode="json") if result else None,
            "matched_customer_id": None,
            "reasons": reasons,
        },
        created_by=document.created_by,
    )
    return change.id, reasons
