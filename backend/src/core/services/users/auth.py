"""Password hashing, JWT issue/verify, login."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import JWT_EXPIRY_HOURS
from core.exceptions.errors import AuthenticationError
from data.models.postgres import User
from data.repositories.users import users_repo

_ALGORITHM = "HS256"
# Compared against when the email is unknown, so a miss costs the same as a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"unused", bcrypt.gensalt()).decode()


async def hash_password(password: str) -> str:
    # bcrypt is CPU-bound; keep it off the event loop.
    hashed = await asyncio.to_thread(
        bcrypt.hashpw, password.encode(), bcrypt.gensalt()
    )
    return hashed.decode()


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(
        bcrypt.checkpw, password.encode(), password_hash.encode()
    )


def create_access_token(user_id: uuid.UUID, role_code: str) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = now + timedelta(hours=JWT_EXPIRY_HOURS)
    token = jwt.encode(
        {"sub": str(user_id), "role": role_code, "iat": now, "exp": expires},
        get_settings().jwt_secret,
        algorithm=_ALGORITHM,
    )
    return token, JWT_EXPIRY_HOURS * 3600


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    user = await users_repo.get_user_by_email(db, email.lower())
    ok = await verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        raise AuthenticationError("Invalid email or password")
    return user


async def load_active_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await users_repo.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or deactivated")
    return user
