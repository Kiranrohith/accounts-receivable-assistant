"""Payment reminders (drafted by the daily scan, approved and sent by a person) and the tasks the 60-day handoff makes."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, uuid_pk
from data.models.postgres.enums import (
    ReminderState,
    TaskClosedReason,
    TaskStatus,
    TaskType,
)


def _enum(cls: type, name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Reminder(Base, AuditMixin):
    """One reminder of one invoice at one tier (1 / 15 / 30 days past due).

    At most one ACTIVE row per (invoice, tier): a cancelled or rejected one does not count, so the scan can draft
    the tier again once whatever cancelled it is over. `draft_body` is the text a person edits: its first line is
    "Subject: ..." and the figures are markers ([[balance]], [[days_past_due]] ...) that are filled in live when it
    is shown and again when it is sent, so what the customer reads is never stale.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        Index(
            "uq_reminders_invoice_tier_active",
            "invoice_id",
            "reminder_tier",
            unique=True,
            postgresql_where=text("state NOT IN ('AutoCancelled', 'Rejected')"),
        ),
        Index("ix_reminders_invoice_tier_created", "invoice_id", "reminder_tier", "created_at"),
        Index("ix_reminders_state", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False)
    reminder_tier: Mapped[int] = mapped_column(Integer, nullable=False, comment="1 / 15 / 30")
    scheduled_date: Mapped[date | None] = mapped_column(Date, comment="the day the tier was crossed (due date + tier)")
    sent_date: Mapped[date | None] = mapped_column(Date)
    weekend_shift_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    days_overdue_at_send: Mapped[int | None] = mapped_column(Integer, comment="the true count, at the moment it was sent")
    draft_body: Mapped[str | None] = mapped_column(Text, comment="'Subject: ...' then the text, with figure markers")
    state: Mapped[ReminderState] = mapped_column(
        _enum(ReminderState, "reminder_state"), default=ReminderState.DRAFTED, server_default="Drafted", nullable=False
    )
    gmail_thread_id: Mapped[str | None] = mapped_column(String, comment="learned from the first reply in the thread")
    handled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_to: Mapped[str | None] = mapped_column(String)
    sent_subject: Mapped[str | None] = mapped_column(String)
    sent_body: Mapped[str | None] = mapped_column(Text, comment="the text exactly as the customer received it")
    message_id: Mapped[str | None] = mapped_column(String, comment="the RFC Message-ID we sent: replies point back to it")
    cancel_reason: Mapped[str | None] = mapped_column(Text, comment="why it was rejected or cancelled")


class Task(Base, AuditMixin):
    """Work handed to a person. COLLECTION_60D: an invoice still unpaid 60 days past due, with its history bundled."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "uq_tasks_invoice_type_open",
            "invoice_id",
            "type",
            unique=True,
            postgresql_where=text("status = 'Open'"),
        ),
        Index("ix_tasks_assigned_status", "assigned_to", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    type: Mapped[TaskType] = mapped_column(_enum(TaskType, "task_type"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    payload: Mapped[dict | None] = mapped_column(JSONB, comment="the bundle: invoice, payments, reminders, contact")
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.OPEN, server_default="Open", nullable=False
    )
    closed_reason: Mapped[TaskClosedReason | None] = mapped_column(_enum(TaskClosedReason, "task_closed_reason"))
    closed_note: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), comment="null = the system")
