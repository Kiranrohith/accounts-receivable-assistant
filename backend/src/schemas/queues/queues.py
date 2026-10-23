import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class QueueSummary(BaseModel):
    key: str
    label: str
    description: str
    count: int


class QueueItem(BaseModel):
    """One thing waiting for the person, whatever queue it is in. `item_id` is the id the queue's own endpoints take."""

    queue: str
    item_id: uuid.UUID
    subject: str | None = Field(description="The number or name that identifies it (invoice no., reference, file name)")
    customer_name: str | None
    amount: Decimal | None
    currency: str | None
    reason: str | None = Field(description="Why it is waiting, in plain English")
    created_at: datetime
    due_at: datetime | None = Field(description="A deadline, where the queue has one (disputes)")
