"""The mail registry: one row per polled Gmail message (its id is the idempotency anchor)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import IngestedEmail


async def get_email(db: AsyncSession, email_id: uuid.UUID) -> IngestedEmail | None:
    return await db.get(IngestedEmail, email_id)


async def known_message_ids(db: AsyncSession, gmail_ids: list[str]) -> set[str]:
    """Which of these Gmail ids are already in the registry."""
    if not gmail_ids:
        return set()
    result = await db.scalars(select(IngestedEmail.gmail_message_id).where(IngestedEmail.gmail_message_id.in_(gmail_ids)))
    return set(result)


async def insert_if_new(db: AsyncSession, **fields: Any) -> uuid.UUID | None:
    """Register a message. None when it is already registered (an overlapping poll got there first)."""
    row = await db.execute(
        insert(IngestedEmail)
        .values(id=uuid.uuid4(), **fields)
        .on_conflict_do_nothing(index_elements=["gmail_message_id"])
        .returning(IngestedEmail.id)
    )
    return row.scalar()


async def lock_email(db: AsyncSession, email_id: uuid.UUID) -> IngestedEmail | None:
    return await db.scalar(
        select(IngestedEmail).where(IngestedEmail.id == email_id).with_for_update().execution_options(populate_existing=True)
    )


async def list_classified_waiting(db: AsyncSession) -> list[uuid.UUID]:
    """Classified to payment / dispute / both / purchase_order but not handed off (no status yet): oldest first."""
    from data.models.postgres.enums import EmailClassification

    result = await db.scalars(
        select(IngestedEmail.id)
        .where(
            IngestedEmail.status.is_(None),
            IngestedEmail.classification.is_not(None),
            IngestedEmail.classification != EmailClassification.GENERAL,
        )
        .order_by(IngestedEmail.received_at.nulls_last())
    )
    return list(result)


async def list_routed(db: AsyncSession, *, limit: int, offset: int) -> list[IngestedEmail]:
    """Mails handed to a pipeline (payment / dispute / both / purchase order), newest first: where a wrong call is found."""
    from data.models.postgres.enums import EmailClassification, EmailStatus

    result = await db.scalars(
        select(IngestedEmail)
        .where(IngestedEmail.status == EmailStatus.PROCESSED, IngestedEmail.classification != EmailClassification.GENERAL)
        .order_by(IngestedEmail.received_at.desc().nulls_last(), IngestedEmail.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result)


async def list_by_state(
    db: AsyncSession, *, waiting_for_person: bool, limit: int, offset: int
) -> list[IngestedEmail]:
    """The human triage queue (waiting_for_person), or the ignored list (general mail, newest first)."""
    from data.models.postgres.enums import EmailClassification, EmailStatus

    if waiting_for_person:
        condition = IngestedEmail.status == EmailStatus.HUMAN_TRIAGE
    else:
        condition = (IngestedEmail.status == EmailStatus.PROCESSED) & (IngestedEmail.classification == EmailClassification.GENERAL)
    result = await db.scalars(
        select(IngestedEmail).where(condition).order_by(IngestedEmail.received_at.desc().nulls_last(), IngestedEmail.id).limit(limit).offset(offset)
    )
    return list(result)
