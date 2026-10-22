import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_current_user
from core.services import notifications
from data.clients.db import get_db
from data.models.postgres import User
from schemas.notifications.notifications import MarkedRead, NotificationOut, UnreadCount

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=list[NotificationOut],
    summary="My notifications",
    description=(
        "Any signed-in user; only your own. Newest first. `unread_only` hides the ones you have "
        "read. `entity_type` / `entity_id` say what a notice is about (for example a dispute)."
    ),
)
async def my_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    return await notifications.list_mine(db, user, unread_only=unread_only, limit=limit, offset=offset)


@router.get(
    "/unread-count",
    response_model=UnreadCount,
    summary="How many unread notifications I have",
)
async def unread_count(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UnreadCount:
    return await notifications.unread(db, user)


@router.post(
    "/read-all",
    response_model=MarkedRead,
    summary="Mark all my notifications as read",
)
async def read_all(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MarkedRead:
    return await notifications.mark_all_read(db, user)


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark one notification as read",
    responses={404: {"description": "Not found (or it is someone else's)"}},
)
async def read_one(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationOut:
    return await notifications.mark_read(db, user, notification_id)
