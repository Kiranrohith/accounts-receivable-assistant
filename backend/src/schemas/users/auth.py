from pydantic import BaseModel, EmailStr

from schemas.users.users import UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(UserOut):
    permissions: list[str]


class MessageResponse(BaseModel):
    message: str
