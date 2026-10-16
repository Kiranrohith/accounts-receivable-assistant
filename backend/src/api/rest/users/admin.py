from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.users.users import create_user, list_active_users
from data.clients.db import get_db
from data.models.postgres import User
from schemas.users.users import UserCreate, UserOut

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
