import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

NotificationTypeName = Literal[
    "Escalation", "Assignment", "ShortPayment", "TDSFlip", "PaymentDuringDispute",
    "SendFailure", "SuggestionTask", "DeadlineWarning", "DisputeFollowUp",
]


class NotificationOut(BaseModel):
    notification_id: uuid.UUID
    type: NotificationTypeName
    message: str | None
    entity_type: str | None  # what it is about: dispute, payment, ...
    entity_id: uuid.UUID | None
    is_read: bool
    created_at: datetime | None


class UnreadCount(BaseModel):
    unread: int


class MarkedRead(BaseModel):
    marked: int
