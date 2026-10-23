import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.users.users import create_user, list_active_users, reset_password, update_user
from data.clients.db import get_db
from data.models.postgres import User
from schemas.users.users import PasswordReset, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/admin/users", tags=["admin: users"])


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    description="Admin only (users.manage). The password is stored as a bcrypt hash.",
)
async def create(
    body: UserCreate,
    actor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    return UserOut.from_user(await create_user(db, actor, body))


@router.get(
    "",
    response_model=list[UserOut],
    summary="List active users",
    description="Admin only (users.manage). Deactivated users are not listed.",
)
async def list_users(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    return [UserOut.from_user(u) for u in await list_active_users(db)]


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Edit a user",
    description=(
        "Admin only (users.manage). Send only the fields to change: name, e-mail or role. The e-mail must stay unique "
        "(409 otherwise). A role change applies at once: the next request the person makes is checked against the new "
        "role. The only active administrator cannot be given another role (409)."
    ),
    responses={
        404: {"description": "No such user"},
        409: {"description": "E-mail already in use, or the last administrator"},
        422: {"description": "Unknown role"},
    },
)
async def edit(
    user_id: uuid.UUID,
    body: UserUpdate,
    actor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    return UserOut.from_user(await update_user(db, actor, user_id, body))


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password for a user",
    description=(
        "Admin only (users.manage). For someone who forgot theirs: the new password is stored as a bcrypt hash and the "
        "old one stops working. Sessions already signed in keep working until their access token expires."
    ),
    responses={404: {"description": "No such user"}},
)
async def set_password(
    user_id: uuid.UUID,
    body: PasswordReset,
    actor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await reset_password(db, actor, user_id, body)
