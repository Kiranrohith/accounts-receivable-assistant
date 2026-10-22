import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
NoteStatusName = Literal["Open", "PartiallyUsed", "Closed", "Voided"]


class NoteListItem(BaseModel):
    note_id: uuid.UUID
    note_number: str
    customer_id: uuid.UUID
    customer_name: str
    amount: Decimal
    remaining_amount: Decimal
    status: NoteStatusName
    reason: str | None
    linked_invoice_id: uuid.UUID | None
    linked_payment_id: uuid.UUID | None
    created_at: datetime | None


class NoteSpendOut(BaseModel):
    allocation_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
    amount: Decimal
    status: Literal["Active", "Reversed"]
    created_at: datetime | None


class NoteTimelineEntry(BaseModel):
    at: datetime
    from_state: str | None
    to_state: str
    reason: str | None


class NoteEmail(BaseModel):
    state: Literal["queued", "sent", "failed", "not_sent"]  # not_sent: a note made before notes were e-mailed
    to: str | None = None
    at: datetime | None = None
    error: str | None = None  # the plain reason when it failed


class NoteDetail(NoteListItem):
    email: NoteEmail  # was the customer told?
    linked_invoice_number: str | None
    linked_payment_reference: str | None
    spends: list[NoteSpendOut]  # what this note has paid, oldest first
    timeline: list[NoteTimelineEntry]
    money_identity_ok: bool  # remaining = amount - what it has paid


class NewNote(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    reason: Reason
    linked_invoice_id: uuid.UUID | None = Field(
        default=None, description="The invoice this credit is about (must be the customer's)"
    )


class SpendNote(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal | None = Field(
        default=None, gt=0, description="Default: the most that fits (the note's remainder or the invoice's balance)"
    )
    reason: Reason


class SpendResult(BaseModel):
    note_number: str
    note_status: str
    note_remaining: Decimal
    invoice_number: str
    invoice_status: str
    invoice_balance: Decimal
    spent: Decimal


class ConvertUnapplied(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, description="Default: all of the unapplied amount")
    reason: Reason


class ConvertResult(BaseModel):
    payment_id: uuid.UUID
    payment_state: str
    unapplied_amount: Decimal
    credited_amount: Decimal
    note_number: str
    note_amount: Decimal
