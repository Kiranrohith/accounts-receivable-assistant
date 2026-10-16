import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

RoleCode = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]


class UserCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: EmailStr
    password: str = Field(min_length=8)
    role: RoleCode = Field(description="Role code: FA, FTM or ADM")

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, value: str) -> str:
        if len(value.encode()) > 72:  # bcrypt ignores/rejects anything past 72 bytes
            raise ValueError("Password must be at most 72 bytes")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime | None = None

    @classmethod
    def from_user(cls, user) -> "UserOut":  # noqa: ANN001
        return cls(
            id=user.id,
            name=user.username,
            email=user.email,
            role=user.role.code,
            is_active=user.is_active,
            created_at=user.created_at,
        )
