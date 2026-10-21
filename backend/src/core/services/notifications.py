"""In-app notifications: a short notice to one user about something that needs them.

create() only flushes: the caller commits it together with whatever caused it, so a notice never
exists for a change that was rolled back.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from data.models.postgres import Notification, User
from data.models.postgres.enums import NotificationType
from data.repositories import unit_of_work
from data.repositories.platform import notifications_repo
from schemas.notifications.notifications import MarkedRead, NotificationOut, UnreadCount


async def create(
    db: AsyncSession,
    recipient_id: uuid.UUID,
    type_: NotificationType,
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor: uuid.UUID | None = None,
) -> Notification:
    return await notifications_repo.insert_notification(
        db,
        recipient_user_id=recipient_id,
        type=type_,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        # stamped here, not by the database: notices made in one transaction must still sort in order
        created_at=datetime.now(UTC),
        created_by=actor,
        updated_by=actor,
    )


async def already_told(
    db: AsyncSession,
    recipient_id: uuid.UUID,
    type_: NotificationType,
    entity_type: str,
    entity_id: uuid.UUID,
    marker: str | None = None,
) -> bool:
    return await notifications_repo.exists_for_entity(db, recipient_id, type_, entity_type, entity_id, marker)


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        notification_id=n.id,
        type=n.type.value,
        message=n.message,
        entity_type=n.entity_type,
        entity_id=n.entity_id,
        is_read=n.is_read,
        created_at=n.created_at,
    )


async def list_mine(
    db: AsyncSession, user: User, *, unread_only: bool, limit: int, offset: int
) -> list[NotificationOut]:
    rows = await notifications_repo.list_for_user(
        db, user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return [_out(n) for n in rows]


async def unread(db: AsyncSession, user: User) -> UnreadCount:
    return UnreadCount(unread=await notifications_repo.unread_count(db, user.id))


async def mark_read(db: AsyncSession, user: User, notification_id: uuid.UUID) -> NotificationOut:
    notification = await notifications_repo.get_own_for_update(db, user.id, notification_id)
    if notification is None:  # someone else's notice looks exactly like a missing one
        raise NotFoundError("Notification not found")
    notification.is_read = True
    notification.updated_by = user.id
    result = _out(notification)
    await unit_of_work.commit(db)
    return result


async def mark_all_read(db: AsyncSession, user: User) -> MarkedRead:
    marked = await notifications_repo.mark_all_read(db, user.id)
    await unit_of_work.commit(db)
    return MarkedRead(marked=marked)
