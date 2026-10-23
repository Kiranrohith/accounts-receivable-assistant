"""Password hashing, JWT issue/verify, login, refresh."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import ACCESS_TOKEN_MINUTES, REFRESH_TOKEN_DAYS
from core.exceptions.errors import AuthenticationError
from data.models.postgres import User
from data.repositories.users import users_repo

_ALGORITHM = "HS256"
# Which kind of token a JWT is. A refresh token must never open an API call, nor an access token mint a new one.
ACCESS = "access"
REFRESH = "refresh"
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


def _issue(user_id: uuid.UUID, kind: str, lifetime: timedelta, **claims: str) -> tuple[str, int]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(user_id), "typ": kind, **claims, "iat": now, "exp": now + lifetime},
        get_settings().jwt_secret,
        algorithm=_ALGORITHM,
    )
    return token, int(lifetime.total_seconds())


def create_access_token(user_id: uuid.UUID, role_code: str) -> tuple[str, int]:
    return _issue(user_id, ACCESS, timedelta(minutes=ACCESS_TOKEN_MINUTES), role=role_code)


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Signed, not stored: it cannot be revoked before it expires. It carries no role, so a role is always read fresh."""
    return _issue(user_id, REFRESH, timedelta(days=REFRESH_TOKEN_DAYS))


def decode_token(token: str, kind: str = ACCESS) -> dict:
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc
    # Tokens issued before refresh existed carry no type: they were access tokens, so they stay valid as such.
    if claims.get("typ", ACCESS) != kind:
        raise AuthenticationError("Invalid or expired token")
    return claims


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


async def refresh_session(db: AsyncSession, refresh_token: str) -> tuple[User, str, int, str, int]:
    """Trade a valid refresh token for a new access token and a new refresh token (it rotates, so an active user stays
    signed in; the new one expires REFRESH_TOKEN_DAYS from now). The user is read again: a deactivated user is refused."""
    claims = decode_token(refresh_token, REFRESH)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid or expired token") from exc
    user = await load_active_user(db, user_id)
    access, access_seconds = create_access_token(user.id, user.role.code)
    refresh, refresh_seconds = create_refresh_token(user.id)
    return user, access, access_seconds, refresh, refresh_seconds
