import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import (
    Customer, Dispute, IngestedEmail, Invoice, Note, Payment, PurchaseOrder, Reminder, SourceDocument, StateLog, StepLog, Task, User,
)


def add_state_log(db: AsyncSession, **fields: Any) -> None:
    db.add(StateLog(**fields))


def add_step_log(db: AsyncSession, **fields: Any) -> None:
    db.add(StepLog(**fields))


async def list_state_log(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[StateLog]:
    result = await db.scalars(
        select(StateLog)
        .where(StateLog.entity_type == entity_type, StateLog.entity_id == entity_id)
        .order_by(StateLog.occurred_at, StateLog.created_at)
    )
    return list(result)


async def latest_step_for_entities(
    db: AsyncSession, entity_type: str, step_name: str, entity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StepLog]:
    result = await db.scalars(
        select(StepLog)
        .where(
            StepLog.entity_type == entity_type,
            StepLog.step_name == step_name,
            StepLog.entity_id.in_(entity_ids),
        )
        .order_by(StepLog.occurred_at)
    )
    return {step.entity_id: step for step in result}  # later rows overwrite earlier ones


async def list_step_logs(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[StepLog]:
    result = await db.scalars(
        select(StepLog)
        .where(StepLog.entity_type == entity_type, StepLog.entity_id == entity_id)
        .order_by(StepLog.occurred_at)
    )
    return list(result)


# ---------------------------------------------------------------- the audit screens
# What names a record of each kind, so a row can say "INV-1001" rather than a bare id. The job entity types are the job's
# own states (a mail's or a dispute's job), named after the record the job worked on.
_LABELS: dict[str, tuple[Any, Any]] = {
    "invoice": (Invoice, Invoice.invoice_number),
    "payment": (Payment, Payment.reference_number),
    "purchase_order": (PurchaseOrder, PurchaseOrder.po_number),
    "customer": (Customer, Customer.customer_name),
    "source_document": (SourceDocument, SourceDocument.file_name),
    "ingested_email": (IngestedEmail, IngestedEmail.subject),
    "mail_job": (IngestedEmail, IngestedEmail.subject),
    "dispute": (Dispute, Dispute.summary),
    "dispute_job": (Dispute, Dispute.summary),
    "note": (Note, Note.note_number),
    "note_job": (Note, Note.note_number),
    "task": (Task, cast(Task.type, String)),
    "reminder": (Reminder, func.concat("Day-", cast(Reminder.reminder_tier, String), " reminder")),
}


async def labels_for(db: AsyncSession, pairs: set[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], str]:
    """The name of each (kind, id) that has one; a record that is gone, or has no name, is left out."""
    found: dict[tuple[str, uuid.UUID], str] = {}
    by_kind: dict[str, set[uuid.UUID]] = {}
    for kind, entity_id in pairs:
        if kind in _LABELS:
            by_kind.setdefault(kind, set()).add(entity_id)
    for kind, ids in by_kind.items():
        model, column = _LABELS[kind]
        for row_id, label in (await db.execute(select(model.id, column).where(model.id.in_(ids)))).all():
            if label:
                found[(kind, row_id)] = label
    return found


async def user_names(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {row_id: name for row_id, name in (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()}


def _like(search: str) -> str:
    # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
    return "%" + search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


async def search_state(
    db: AsyncSession, *, entity_type: str | None, entity_id: uuid.UUID | None, trigger: str | None, search: str | None,
    since: datetime | None, until: datetime | None, descending: bool, limit: int, offset: int,
) -> tuple[list[StateLog], int]:
    query = select(StateLog)
    if entity_type:
        query = query.where(StateLog.entity_type == entity_type)
    if entity_id:
        query = query.where(StateLog.entity_id == entity_id)
    if trigger:
        query = query.where(StateLog.triggered_by_type == trigger)
    if since:
        query = query.where(StateLog.occurred_at >= since)
    if until:
        query = query.where(StateLog.occurred_at < until)
    if search and search.strip():
        like = _like(search)
        query = query.where(or_(StateLog.reason.ilike(like, escape="\\"), StateLog.to_state.ilike(like, escape="\\"), StateLog.from_state.ilike(like, escape="\\")))
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    order = StateLog.occurred_at.desc() if descending else StateLog.occurred_at.asc()
    result = await db.scalars(query.order_by(order, StateLog.created_at, StateLog.id).limit(limit).offset(offset))
    return list(result), total or 0


_STEP_SORTS = {"occurred_at": StepLog.occurred_at, "duration": StepLog.duration_ms}


async def search_steps(
    db: AsyncSession, *, entity_type: str | None, entity_id: uuid.UUID | None, job_id: uuid.UUID | None, step_name: str | None,
    result: str | None, search: str | None, since: datetime | None, until: datetime | None, sort: str, descending: bool, limit: int, offset: int,
) -> tuple[list[StepLog], int]:
    query = select(StepLog)
    if entity_type:
        query = query.where(StepLog.entity_type == entity_type)
    if entity_id:
        query = query.where(StepLog.entity_id == entity_id)
    if job_id:
        query = query.where(StepLog.job_id == job_id)
    if step_name:
        query = query.where(StepLog.step_name == step_name)
    if result:
        query = query.where(StepLog.result == result)
    if since:
        query = query.where(StepLog.occurred_at >= since)
    if until:
        query = query.where(StepLog.occurred_at < until)
    if search and search.strip():
        like = _like(search)
        query = query.where(or_(StepLog.step_name.ilike(like, escape="\\"), StepLog.error_message.ilike(like, escape="\\"), StepLog.model_used.ilike(like, escape="\\")))
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    column = _STEP_SORTS.get(sort, StepLog.occurred_at)
    order = column.desc().nullslast() if descending else column.asc().nullslast()
    rows = await db.scalars(query.order_by(order, StepLog.id).limit(limit).offset(offset))
    return list(rows), total or 0


async def get_step(db: AsyncSession, step_id: uuid.UUID) -> StepLog | None:
    return await db.get(StepLog, step_id)


async def facets(db: AsyncSession) -> dict[str, list[str]]:
    def distinct(column: Any) -> Any:
        return select(column).where(column.is_not(None)).distinct().order_by(column)

    return {
        "state_entity_types": list(await db.scalars(distinct(StateLog.entity_type))),
        "step_entity_types": list(await db.scalars(distinct(StepLog.entity_type))),
        "step_names": list(await db.scalars(distinct(StepLog.step_name))),
    }


async def steps_of_jobs(db: AsyncSession, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[tuple[str, datetime | None]]]:
    """(step name, when) of every step each job has logged so far, oldest first."""
    found: dict[uuid.UUID, list[tuple[str, datetime | None]]] = {}
    if not job_ids:
        return found
    rows = await db.execute(select(StepLog.job_id, StepLog.step_name, StepLog.occurred_at).where(StepLog.job_id.in_(job_ids)).order_by(StepLog.occurred_at))
    for job_id, name, when in rows.all():
        found.setdefault(job_id, []).append((name, when))
    return found
