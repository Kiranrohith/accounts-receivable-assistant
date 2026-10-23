"""The audit screens: what happened to a record (state log) and what each background step did (step log)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StateEntry(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    entity_label: str | None  # the invoice number, file name, mail subject...: None when the record is gone or has no name
    from_state: str | None
    to_state: str
    triggered_by_type: str | None  # user / system / scheduler
    triggered_by_id: uuid.UUID | None
    triggered_by_name: str | None
    reason: str | None
    occurred_at: datetime


class StepEntry(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_label: str | None
    step_name: str
    result: str | None  # success / warning / error
    error_message: str | None
    model_used: str | None
    duration_ms: int | None
    occurred_at: datetime | None


class StepDetail(StepEntry):
    input_summary: dict[str, Any] | None
    output_summary: dict[str, Any] | None
    attempt_history: Any | None


class LogFacets(BaseModel):
    """What the filters can offer, read from the logs themselves."""

    state_entity_types: list[str]
    step_entity_types: list[str]
    step_names: list[str]
    results: list[str]
    triggers: list[str]
