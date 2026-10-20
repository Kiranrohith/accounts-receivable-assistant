import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class TypeChoice(BaseModel):
    doc_type: Literal["invoice", "po", "payment"]
    reason: Reason | None = None  # optional: the choice itself is the decision


class TypeReject(BaseModel):
    reason: Reason


class TypeReviewSummary(BaseModel):
    review_id: uuid.UUID
    source_document_id: uuid.UUID | None
    file_name: str | None
    uploaded_at: datetime | None
    status: Literal["Pending", "Approved", "Rejected"]
    reasons: list[str]  # why a person has to decide
    suggested_type: str | None = None  # what the reader leaned towards: invoice, po, payment, other, unclear
    confidence: int | None = None
    reader_note: str | None = None


class TypeReviewDetail(TypeReviewSummary):
    chosen_type: str | None = None  # set once decided as a type
    decision_reason: str | None = None
    decided_at: datetime | None = None


class TypeDecision(BaseModel):
    review_id: uuid.UUID
    status: Literal["Approved", "Rejected"]
    chosen_type: str | None = None
    processing: bool = False  # true: it is being read again as that type; poll the upload status


class SendBackResult(BaseModel):
    rejected_id: uuid.UUID
    review_id: uuid.UUID  # the new doc-type review item; decide it at /documents/type-reviews
