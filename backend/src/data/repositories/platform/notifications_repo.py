import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Notification
from data.repositories import unit_of_work


async def insert_notification(db: AsyncSession, **fields: Any) -> Notification:
    notification = Notification(**fields)
    db.add(notification)
    await unit_of_work.flush(db)
    return notification


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, unread_only: bool, limit: int, offset: int
) -> list[Notification]:
    query = select(Notification).where(Notification.recipient_user_id == user_id)
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    result = await db.scalars(
        query.order_by(Notification.created_at.desc(), Notification.id).limit(limit).offset(offset)
    )
    return list(result)


async def unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_user_id == user_id, Notification.is_read.is_(False))
        )
        or 0
    )


async def get_own_for_update(
    db: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID
) -> Notification | None:
    return await db.scalar(
        select(Notification)
        .where(Notification.id == notification_id, Notification.recipient_user_id == user_id)
        .with_for_update()
    )


async def mark_all_read(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.recipient_user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True, updated_by=user_id)
    )
    return result.rowcount or 0


async def exists_for_entity(
    db: AsyncSession, recipient_id: uuid.UUID, type_: Any, entity_type: str, entity_id: uuid.UUID
) -> bool:
    """Has this user already been told this (used to keep a scan from repeating a warning)?"""
    found = await db.scalar(
        select(Notification.id)
        .where(
            Notification.recipient_user_id == recipient_id,
            Notification.type == type_,
            Notification.entity_type == entity_type,
            Notification.entity_id == entity_id,
        )
        .limit(1)
    )
    return found is not None
