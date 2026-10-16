"""Append-only tables: state_log (entity timelines) and step_log (job steps)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import Base, CreatedMixin, uuid_pk
from data.models.postgres.enums import StepResult, TriggerType


class StateLog(Base, CreatedMixin):
    __tablename__ = "state_log"
    __table_args__ = (
        Index("ix_state_log_entity_occurred", "entity_type", "entity_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    triggered_by_type: Mapped[TriggerType | None] = mapped_column(
        Enum(TriggerType, name="trigger_type", values_callable=lambda e: [m.value for m in e])
    )
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StepLog(Base, CreatedMixin):
    __tablename__ = "step_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    entity_type: Mapped[str | None] = mapped_column(String)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[dict | None] = mapped_column(JSONB)
    output_summary: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[StepResult | None] = mapped_column(
        Enum(StepResult, name="step_result", values_callable=lambda e: [m.value for m in e])
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String)
    attempt_history: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
