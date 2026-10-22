"""Shapes of the reminder and collection-task APIs."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ReminderStateName = Literal["Drafted", "Approved", "Rejected", "Sent", "AutoCancelled", "SkippedPastThreshold"]


class ReminderItem(BaseModel):
    reminder_id: uuid.UUID
    reference: str  # "REM-1A2B3C4D", in the subject of the mail that goes out
    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    tier: int  # 1 / 15 / 30
    state: ReminderStateName
    scheduled_date: date | None  # the day the tier was crossed
    weekend_shift_days: int
    days_overdue: int | None  # live while it waits; as it was when sent afterwards
    balance: str | None  # live while it waits ("INR 1,15,758.00")
    recipient: str | None  # the customer's address: locked, a person cannot change it
    created_at: datetime
    sent_at: datetime | None
    handled_by: str | None
    note: str | None  # why it was rejected, cancelled or skipped


class HistoryEntry(BaseModel):
    at: datetime
    from_state: str | None
    to_state: str
    reason: str | None


class ReminderDetail(ReminderItem):
    subject: str  # as stored: figures are markers ([[balance]], [[days_past_due]])
    body: str
    preview_subject: str  # exactly what would go out now, figures live
    preview_body: str
    can_send: bool  # false when it is no longer needed or the customer has no valid address
    blocked_reason: str | None
    sent_subject: str | None
    sent_body: str | None
    history: list[HistoryEntry]


class EditReminder(BaseModel):
    subject: Text | None = Field(default=None, max_length=300)
    body: Text | None = None


class RejectReminder(BaseModel):
    reason: Reason


class ReminderResult(BaseModel):
    reminder_id: uuid.UUID
    state: ReminderStateName
    message: str


class ScanResult(BaseModel):
    as_of: datetime
    drafted: int
    skipped_tiers: int
    handed_off: int
    cancelled: int
    tasks_closed: int
    suppressed: int
    failed: int
    errors: list[str]


# ------------------------------------------------------------------ collection tasks
class TaskItem(BaseModel):
    task_id: uuid.UUID
    type: str
    status: Literal["Open", "Closed"]
    invoice_id: uuid.UUID | None
    invoice_number: str | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    assigned_to: uuid.UUID | None
    assigned_to_name: str | None
    created_at: datetime
    closed_reason: str | None
    closed_at: datetime | None


class TaskDetail(TaskItem):
    payload: dict[str, Any] | None  # the bundle: invoice, customer, payments, reminders
    closed_note: str | None
    history: list[HistoryEntry]


class CloseTask(BaseModel):
    reason: Literal["ResolvedManually", "Dismissed"]
    note: Reason
