"""Failed items: background work that gave up, as a person sees it, and the re-run of it."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FailedKind = Literal["document", "mail", "dispute", "credit_note"]


class FailedItem(BaseModel):
    job_id: uuid.UUID
    kind: FailedKind
    task: str  # what was being done, in words: "Reading an uploaded document"
    subject: str | None  # what it was done to: the file name, the mail subject, the complaint, the credit note number
    detail: str | None  # who it concerns: the sender or the customer
    entity_id: uuid.UUID | None
    state: Literal["Failed", "DeadLettered"]  # Failed: it cannot work as it stands; DeadLettered: tried until the attempts ran out
    error: str | None
    attempts: int
    failed_at: datetime | None
    earlier_runs: int  # how many times a person already re-ran it before this one
    can_rerun: bool
    cannot_rerun_because: str | None = None


class RerunRequest(BaseModel):
    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class RerunOutcome(BaseModel):
    job_id: uuid.UUID  # the failed item that was asked for
    ok: bool
    new_job_id: uuid.UUID | None = None
    message: str | None = None  # why it could not be re-run


class RerunResults(BaseModel):
    results: list[RerunOutcome]
    queued: int
