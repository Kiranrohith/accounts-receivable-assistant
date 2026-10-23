"""The mail registry: one row per polled Gmail message (its id is the idempotency anchor)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
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


def waiting_for_person_condition(now: datetime | None = None):  # noqa: ANN201
    """Mails a person must route: the reader was not sure (HumanTriage), or it has not classified the mail at all
    for UNCLASSIFIED_MAIL_MINUTES (no status, no class: the models are down or keep failing). The queue counts and
    its own list both use this, so they cannot drift apart."""
    from constants.constants import UNCLASSIFIED_MAIL_MINUTES
    from data.models.postgres.enums import EmailStatus

    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=UNCLASSIFIED_MAIL_MINUTES)
    return or_(
        IngestedEmail.status == EmailStatus.HUMAN_TRIAGE,
        and_(IngestedEmail.status.is_(None), IngestedEmail.classification.is_(None), IngestedEmail.created_at < cutoff),
    )


async def list_by_state(
    db: AsyncSession, *, waiting_for_person: bool, limit: int, offset: int
) -> list[IngestedEmail]:
    """The human triage queue (waiting_for_person), or the ignored list (general mail, newest first)."""
    from data.models.postgres.enums import EmailClassification, EmailStatus

    if waiting_for_person:
        condition = waiting_for_person_condition()
    else:
        condition = (IngestedEmail.status == EmailStatus.PROCESSED) & (IngestedEmail.classification == EmailClassification.GENERAL)
    result = await db.scalars(
        select(IngestedEmail).where(condition).order_by(IngestedEmail.received_at.desc().nulls_last(), IngestedEmail.id).limit(limit).offset(offset)
    )
    return list(result)


_LOG_SORTS = {"received_at": IngestedEmail.received_at, "sender": IngestedEmail.sender, "subject": IngestedEmail.subject}


def _log_condition(outcome: str):  # noqa: ANN202
    """What each outcome of the mail log means, in terms of the mail's status and class."""
    from data.models.postgres.enums import EmailClassification, EmailStatus

    waiting = waiting_for_person_condition()
    if outcome == "waiting":
        return waiting
    if outcome == "routed":
        return and_(IngestedEmail.status == EmailStatus.PROCESSED, IngestedEmail.classification != EmailClassification.GENERAL)
    if outcome == "ignored":
        return and_(IngestedEmail.status == EmailStatus.PROCESSED, IngestedEmail.classification == EmailClassification.GENERAL)
    if outcome == "failed":
        return IngestedEmail.status == EmailStatus.FAILED
    if outcome == "reading":  # registered, not yet decided, and not waiting for a person either
        return and_(IngestedEmail.status.is_(None), ~func.coalesce(waiting, False))
    return None


async def search_log(
    db: AsyncSession, *, outcome: str, search: str | None, sort: str, descending: bool, limit: int, offset: int
) -> tuple[list[IngestedEmail], int]:
    """(mails, total) for the mail log: every mail we have read, filtered by what became of it, searched, sorted and paged."""
    query = select(IngestedEmail)
    condition = _log_condition(outcome)
    if condition is not None:
        query = query.where(condition)
    if search and search.strip():
        # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
        escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        query = query.where(or_(IngestedEmail.sender.ilike(like, escape="\\"), IngestedEmail.subject.ilike(like, escape="\\")))
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    column = _LOG_SORTS.get(sort, IngestedEmail.received_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    result = await db.scalars(query.order_by(ordered, IngestedEmail.id).limit(limit).offset(offset))
    return list(result), total or 0


async def emails_by_ids(db: AsyncSession, email_ids: set[uuid.UUID]) -> dict[uuid.UUID, IngestedEmail]:
    if not email_ids:
        return {}
    return {mail.id: mail for mail in await db.scalars(select(IngestedEmail).where(IngestedEmail.id.in_(email_ids)))}
