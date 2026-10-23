"""The live view of mail being processed: what is in the queue, what a worker is doing with each mail and its attachments
right now, and, for a short while after, how each ended."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from schemas.documents.uploads import DocumentUploadSummary

LiveState = Literal["waiting", "retrying", "running", "finished"]
# done: processed; ignored: general mail set aside; review: a person must decide (Take Action); rejected: an attachment was
# rejected; failed: it gave up (Failed Items)
FinishedHow = Literal["done", "ignored", "review", "rejected", "failed"]


class LiveDocument(BaseModel):
    """One attachment (or the mail's own text) being read."""

    state: LiveState
    stage: str  # "Working out what it is", "Checking the details"...
    ahead: int | None = None
    finished_how: FinishedHow | None = None
    upload: DocumentUploadSummary  # what it is so far and where it went, as the upload list shows it


class LiveMail(BaseModel):
    email_id: uuid.UUID
    mail_link: str | None
    sender: str | None
    subject: str | None
    received_at: datetime | None
    mail_class: str | None  # payment / dispute / both / purchase_order / general; None until it is read
    confidence: int | None
    state: LiveState
    stage: str  # what is happening to the mail as a whole, in words
    ahead: int | None = None  # waiting to be read: how many are in front of it (0 = next)
    finished_at: datetime | None = None
    finished_how: FinishedHow | None = None
    review_queue: str | None = None  # the Take Action queue to decide it in, when finished_how is review
    review_id: uuid.UUID | None = None
    documents: list[LiveDocument]


class LiveMailFeed(BaseModel):
    as_of: datetime  # the server's clock: ages are measured against this, not the browser's
    items: list[LiveMail]
