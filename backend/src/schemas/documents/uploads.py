"""Responses for the combined upload entry: one shape for every kind of document."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from schemas.invoices.responses import StepOut
from schemas.invoices.validation import ValidationIssue

UploadStatus = Literal[
    "Processing",
    "Failed",
    "Rejected",
    "Needs review",
    "Invoice created",
    "New version applied",
    "Purchase order created",
]


class DocumentUploadSummary(BaseModel):
    source_document_id: uuid.UUID
    status: UploadStatus  # the one thing to show a user; the fields below explain it
    # What the document turned out to be: "unknown" while it is being read, when it waits for a
    # type decision, or when it was rejected as neither an invoice nor a purchase order.
    document_kind: Literal["invoice", "po", "unknown"]
    file_name: str | None
    doc_type: str | None  # pdf / docx / image
    uploaded_at: datetime | None
    processing_status: str  # Pending, Running, Succeeded, Failed
    validation_outcome: Literal["PASS", "REVIEW", "FAIL"] | None = None  # None until validated
    disposition: Literal["invoice_created", "version_applied", "po_created", "review", "rejected"] | None = None
    invoice_id: uuid.UUID | None = None  # set when an invoice was created or a new version applied
    invoice_version: int | None = None  # the version of that invoice this document produced
    po_hold: bool = False  # invoice created but on hold: it does not match its purchase order
    purchase_order_id: uuid.UUID | None = None  # set when a purchase order was created
    review_id: uuid.UUID | None = None  # set while a decision is waiting for the uploader
    review_kind: Literal["document_type", "extraction", "version"] | None = None  # which screen decides it
    detected_type: str | None = None  # the reader's verdict: invoice, po, other or unclear
    detected_confidence: int | None = None
    customer_id: uuid.UUID | None = None  # the matched customer, when there is one
    error: str | None = None  # why it failed, or why the last attempt failed while a retry is pending
    retry_at: datetime | None = None  # set while a failed attempt waits to be retried
    invoice_number: str | None = None
    po_number: str | None = None  # a purchase order's own number, or the PO an invoice quotes
    customer_name: str | None = None
    total_amount: str | None = None


class DocumentUploadDetail(DocumentUploadSummary):
    extraction: dict | None = None  # the fields read (invoice or purchase-order shape); None if none were
    classification: dict | None = None  # the reader's raw verdict: doc_class, confidence, reason
    validation_issues: list[ValidationIssue] = []
    disposition_reasons: list[str] = []  # why it was rejected or sent to review
    linked_invoices: list[dict] = []  # purchase orders: invoices linked (and checked) on creation
    steps: list[StepOut] = []


class RerunResult(BaseModel):
    source_document_id: uuid.UUID
    job_id: uuid.UUID  # the new job; poll GET /documents/uploads/{source_document_id}
