import uuid
from datetime import datetime

from pydantic import BaseModel


class UploadResult(BaseModel):
    file_name: str
    status: str  # "accepted"
    source_document_id: uuid.UUID


class StepOut(BaseModel):
    step_name: str
    result: str | None
    duration_ms: int | None
    model_used: str | None
    error_message: str | None
    occurred_at: datetime | None
    output_summary: dict | None = None
