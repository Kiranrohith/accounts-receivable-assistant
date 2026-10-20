import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CategoryName = Literal[
    "Pricing", "Quality", "Delivery", "AlreadyPaid", "WrongDuplicateInvoice",
    "TaxDiscrepancy", "POMismatch", "CreditNotePending", "Other",
]
StatusName = Literal["Raised", "Categorised", "ClarificationSent", "Assigned", "Escalated", "Resolved"]
MessageKindName = Literal["inbound_email", "draft", "sent", "internal_note"]


# ---- requests -------------------------------------------------------------------------------------
class CreateDispute(BaseModel):
    """A complaint entered by hand. Give the invoice (the customer follows from it) or only the
    customer (a complaint about the account as a whole)."""

    invoice_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    summary: str = Field(min_length=3, max_length=4000, description="what the customer complains about")
    category: CategoryName | None = Field(
        None, description="leave empty to send it to the classify queue"
    )
    payment_related: bool = Field(
        False, description="the customer withheld or short-paid on purpose, giving this as the reason"
    )
    from_email: str | None = Field(None, max_length=320)
    received_at: datetime | None = Field(None, description="when the customer raised it; default now")
    gmail_thread_id: str | None = Field(
        None,
        max_length=200,
        description=(
            "the mail thread it came from. A complaint in the thread of an OPEN dispute is added "
            "to it; in the thread of a resolved one it opens a new dispute linked to it. Without "
            "a thread (or a new thread) it is always its own dispute."
        ),
    )

    @model_validator(mode="after")
    def _needs_a_subject(self) -> "CreateDispute":
        if self.invoice_id is None and self.customer_id is None:
            raise ValueError("give an invoice_id, or a customer_id for a complaint about the account")
        return self


class ClassifyDispute(BaseModel):
    category: CategoryName
    payment_related: bool = False
    customer_id: uuid.UUID | None = Field(
        None, description="only for a dispute that has no customer yet"
    )
    invoice_id: uuid.UUID | None = Field(
        None, description="only for a dispute that has no invoice yet (the customer follows from it)"
    )


class ReassignDispute(BaseModel):
    assigned_to: uuid.UUID
    reason: str | None = Field(None, max_length=500)


class AddNote(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ResolveDispute(BaseModel):
    narrative: str = Field(min_length=3, max_length=4000, description="how it was settled")


# ---- responses ------------------------------------------------------------------------------------
class DisputeListItem(BaseModel):
    dispute_id: uuid.UUID
    status: StatusName
    category: CategoryName | None
    payment_related: bool
    classification_confidence: int | None
    summary: str | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    invoice_id: uuid.UUID | None
    invoice_number: str | None
    assigned_to: uuid.UUID | None
    assigned_to_name: str | None
    received_at: datetime
    age_days: int  # calendar days since it was received (IST dates; frozen at resolution)
    first_response_due: date | None
    first_response_at: datetime | None
    first_response_overdue: bool
    resolution_due: date | None
    resolution_overdue: bool
    escalated_at: datetime | None
    resolved_at: datetime | None
    linked_dispute_id: uuid.UUID | None


class DisputeMessageOut(BaseModel):
    message_id: uuid.UUID
    kind: MessageKindName
    body: str
    sent_at: datetime | None
    author_id: uuid.UUID | None
    author_name: str | None
    created_at: datetime | None


class DisputeInvoice(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    status: str
    invoice_date: date
    due_date: date
    invoice_currency: str
    amount_inr: Decimal
    collectible_total: Decimal
    amount_paid: Decimal
    collectible_balance: Decimal
    po_number: str | None
    purchase_order_id: uuid.UUID | None
    po_hold: bool


class DisputePayment(BaseModel):
    payment_id: uuid.UUID | None
    payment_reference: str | None
    note_number: str | None
    funded_by: Literal["payment", "credit_note"]
    allocated_amount: Decimal
    status: str
    created_at: datetime | None


class HeldPayment(BaseModel):
    payment_id: uuid.UUID
    payment_reference: str | None
    state: str
    held_amount: Decimal


class TimelineEntry(BaseModel):
    from_state: str | None
    to_state: str
    triggered_by_type: str | None
    triggered_by_id: uuid.UUID | None
    reason: str | None
    occurred_at: datetime


class RelatedDispute(BaseModel):
    dispute_id: uuid.UUID
    status: StatusName
    category: CategoryName | None
    received_at: datetime


class PossiblyRelated(BaseModel):
    """Another dispute on the same invoice. A suggestion only: nothing is merged or shared."""

    dispute_id: uuid.UUID
    link: str
    status: StatusName
    category: CategoryName | None
    assigned_to: uuid.UUID | None
    assigned_to_name: str | None
    received_at: datetime
    resolved_at: datetime | None  # the resolution date, for a closed one
    in_same_chain: bool  # it is this dispute's earlier or later reply-chain link (E3)


class DisputeDetail(DisputeListItem):
    from_email: str | None
    clarification_confidence: int | None
    classified_by: uuid.UUID | None
    classified_at: datetime | None
    resolved_by: uuid.UUID | None
    resolution_narrative: str | None
    source_document_id: uuid.UUID | None
    messages: list[DisputeMessageOut]
    invoice: DisputeInvoice | None
    payments: list[DisputePayment]  # what has been paid against the invoice: the supporting facts
    held_payments: list[HeldPayment]
    earlier_dispute: RelatedDispute | None
    later_disputes: list[RelatedDispute]
    possibly_related: list[PossiblyRelated]  # other disputes on the same invoice, any status
    timeline: list[TimelineEntry]


class CreateResult(BaseModel):
    dispute_id: uuid.UUID
    appended_to_existing: bool  # the thread already has an open dispute: this became a message on it
    status: StatusName
    assigned_to: uuid.UUID | None
    linked_dispute_id: uuid.UUID | None


class ActionResult(BaseModel):
    dispute_id: uuid.UUID
    status: StatusName
    assigned_to: uuid.UUID | None


class NoteResult(BaseModel):
    dispute_id: uuid.UUID
    message_id: uuid.UUID


# ---- dashboard ------------------------------------------------------------------------------------
class CountBy(BaseModel):
    key: str
    open: int
    total: int


class DayVolume(BaseModel):
    day: date
    raised: int
    resolved: int


class OwnerLoad(BaseModel):
    user_id: uuid.UUID
    name: str
    open: int
    overdue: int


class DisputeDashboard(BaseModel):
    open: int
    resolved: int
    unclassified: int  # waiting in the classify queue
    escalated: int
    first_response_overdue: int
    resolution_overdue: int
    average_days_to_resolve: float | None  # calendar days, over resolved disputes
    by_status: list[CountBy]
    by_category: list[CountBy]  # `key` "Uncategorised" for those still in the queue
    open_by_age: list[CountBy]  # `key` is the age bucket; `total` = `open`
    volume: list[DayVolume]  # per day over the requested window
    owners: list[OwnerLoad]
