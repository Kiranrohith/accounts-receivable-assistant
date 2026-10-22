"""Structured output of the mail classifier, and the shapes of the triage queue API."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MailClass(str, Enum):
    """The values of EmailClassification, spelled for the model."""

    PAYMENT = "payment"
    DISPUTE = "dispute"
    BOTH = "both"
    PURCHASE_ORDER = "purchase_order"
    GENERAL = "general"


class MailClassification(BaseModel):
    mail_class: MailClass = Field(
        description="payment, dispute, both (a payment and a complaint in one mail), purchase_order, or general"
    )
    confidence: int = Field(ge=0, le=100, description="How sure you are of mail_class, 0-100")
    mixed_po_and_payment: bool = Field(
        default=False,
        description="the same mail carries a purchase order AND a payment advice (set mail_class to the main purpose)",
    )
    reason: str = Field(description="One short sentence: what the mail is and what in it shows that")


# ------------------------------------------------------------------ the queue API
class AttachmentView(BaseModel):
    file_name: str
    size_bytes: int
    verdict: Literal["qualify", "skip", "reject"]
    reason: str


class TriageItem(BaseModel):
    email_id: uuid.UUID
    gmail_message_id: str
    mail_link: str  # opens the mail in Gmail
    sender: str | None
    subject: str | None
    received_at: datetime | None
    status: str | None  # HumanTriage waiting for a person; Processed (general) is the ignored list
    suggested_class: str | None  # what the classifier thought, even when it was not sure enough
    confidence: int | None
    reason: str | None  # why it is in the queue (or why it was ignored)
    attachments: int
    body_preview: str
    replies_to: str | None = None  # the conversation of ours it continues, e.g. "DSP-1A2B3C4D (Assigned), found by a reply to our mail"


class TriageDetail(TriageItem):
    body: str
    attachment_list: list[AttachmentView]
    timeline: list[dict]
    produced: list[dict] = Field(default_factory=list)  # the payments / disputes this mail made, with their state
    reroute_to: Literal["payment", "dispute"] | None = None  # the other pipeline it can be sent to (E9), if any


class ClassifyMail(BaseModel):
    mail_class: MailClass = Field(description="Where this mail belongs; general moves it to the ignored list")
    note: str | None = Field(default=None, max_length=500, description="Why, for the record")


class RerouteMail(BaseModel):
    mail_class: Literal["payment", "dispute"] = Field(description="The pipeline the mail should have gone to")
    note: str | None = Field(default=None, max_length=500, description="Why, for the record")


class RerouteResult(BaseModel):
    email_id: uuid.UUID
    mail_class: str
    undone: list[str]  # what was taken back from the first pipeline
    message: str


class ClassifyResult(BaseModel):
    email_id: uuid.UUID
    mail_class: str
    status: str | None  # Processed for general; empty while waiting to be handed to its pipeline
    message: str
