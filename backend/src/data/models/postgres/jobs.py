"""Reliability tables: jobs (claim loop) and scheduled_runs (scheduler tick)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, uuid_pk
from data.models.postgres.enums import ErrorClass, JobState


class Job(Base, AuditMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_state_run_after", "state", "run_after"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, name="job_state", values_callable=lambda e: [m.value for m in e]),
        default=JobState.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    model_used: Mapped[str | None] = mapped_column(String)
    last_error_class: Mapped[ErrorClass | None] = mapped_column(
        Enum(ErrorClass, name="error_class", values_callable=lambda e: [m.value for m in e])
    )
    last_error_message: Mapped[str | None] = mapped_column(Text)
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    run_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="claimable when <= now(); requeues push it forward by cause (contention/outage backoff)",
    )
    locked_by: Mapped[str | None] = mapped_column(
        String, comment="claiming worker id (FOR UPDATE SKIP LOCKED)"
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="written every HEARTBEAT_SECONDS by a background thread, not at step boundaries",
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledRun(Base, AuditMixin):
    __tablename__ = "scheduled_runs"
    __doc__ = (
        "One row per recurring schedule: gmail_poll / reminder_scan / deadline_scan / "
        "watchdog. A worker ticks due schedules by claiming the row FOR UPDATE SKIP LOCKED."
    )

    schedule_name: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="'interval' (now >= last_run_at + INTERVAL) or 'daily_ist' (IST-anchored scan)",
    )
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="seeded at epoch so every schedule fires on first boot",
    )
