import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import AuthenticationError, PermissionDeniedError
from core.services.users.auth import decode_token, load_active_user
from core.services.users.users import permission_codes_for
from data.clients.db import get_db
from data.models.postgres import User

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    claims = decode_token(credentials.credentials)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid token") from exc
    return await load_active_user(db, user_id)


def require_permission(code: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: the current user's role must hold permission `code`."""

    async def dependency(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if code not in await permission_codes_for(db, user):
            raise PermissionDeniedError(f"Missing permission: {code}")
        return user

    return dependency
