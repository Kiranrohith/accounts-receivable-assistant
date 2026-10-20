import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class VersionApprove(BaseModel):
    reason: Reason


class VersionReject(BaseModel):
    reason: Reason


class ChangeOut(BaseModel):
    field: str
    label: str
    old: Any
    new: Any


class VersionReviewSummary(BaseModel):
    review_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str | None
    source_document_id: uuid.UUID | None
    file_name: str | None
    uploaded_at: datetime | None
    status: Literal["Pending", "Approved", "Rejected"]
    reasons: list[str]  # why it needs a person
    can_approve: bool  # false when the invoice is already paid (closed): only a reject is possible
    base_version: int  # the saved version this document was compared with
    changes: list[ChangeOut]  # what differs from the saved invoice


class VersionReviewDetail(VersionReviewSummary):
    current: dict  # the saved invoice as it is now
    proposed: dict  # the values read from the new document
    decision_reason: str | None = None
    decided_at: datetime | None = None


class VersionDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Approved", "Rejected"]
    invoice_id: uuid.UUID
    version: int | None = None  # the new version number after an approval
    po_hold: bool = False  # applied, but the new lines do not match the purchase order
    po_reasons: list[str] = []
