import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, StringConstraints, field_validator

from constants.constants import GSTIN_PATTERN

_GSTIN = re.compile(GSTIN_PATTERN)


def _collapse_spaces(value: object) -> object:
    return " ".join(value.split()) if isinstance(value, str) else value


# Trimmed, inner whitespace collapsed ("Acme   Traders" -> "Acme Traders"), never empty.
Text = Annotated[
    str,
    BeforeValidator(_collapse_spaces),
    StringConstraints(min_length=1, max_length=200),
]


class CustomerCreate(BaseModel):
    """Every field is required."""

    customer_name: Text
    contact: Text
    email: EmailStr
    gstin: str
    deducts_tds: bool
    billing_currency: str
    country: Text

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, value: str) -> str:
        value = value.strip().upper()
        if not _GSTIN.match(value):
            raise ValueError("GSTIN must be 15 characters, e.g. 29ABCDE1234F1Z5")
        return value

    @field_validator("billing_currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("billing_currency must be a 3-letter ISO code, e.g. INR")
        return value


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_name: str
    contact: str | None
    email: str | None
    gstin: str | None
    deducts_tds: bool
    billing_currency: str | None
    country: str | None
    is_active: bool
    created_at: datetime | None
