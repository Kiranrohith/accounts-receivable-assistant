"""The audit screens (admin only): the state log (what happened to a record, and who or what did it) and the step log (what
each background step did, with its inputs, outputs, model and timing). Read-only: both logs are append-only."""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from core.services.disputes.calendar import IST
from data.models.postgres import StateLog, StepLog
from data.models.postgres.enums import StepResult, TriggerType
from data.repositories.platform import logs_repo
from schemas.common import Page
from schemas.logs.logs import LogFacets, StateEntry, StepDetail, StepEntry


def _bounds(first: date | None, last: date | None) -> tuple[datetime | None, datetime | None]:
    """Whole IST days: from the start of `first` up to (not including) the day after `last`."""
    since = datetime.combine(first, datetime.min.time(), tzinfo=IST) if first else None
    until = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=IST) if last else None
    return since, until


def _state(row: StateLog, labels: dict, names: dict) -> StateEntry:
    return StateEntry(
        id=row.id, entity_type=row.entity_type, entity_id=row.entity_id, entity_label=labels.get((row.entity_type, row.entity_id)),
        from_state=row.from_state, to_state=row.to_state,
        triggered_by_type=row.triggered_by_type.value if row.triggered_by_type else None,
        triggered_by_id=row.triggered_by_id, triggered_by_name=names.get(row.triggered_by_id), reason=row.reason, occurred_at=row.occurred_at,
    )


def _step(row: StepLog, labels: dict) -> dict:
    return dict(
        id=row.id, job_id=row.job_id, entity_type=row.entity_type, entity_id=row.entity_id,
        entity_label=labels.get((row.entity_type, row.entity_id)) if row.entity_type and row.entity_id else None,
        step_name=row.step_name, result=row.result.value if row.result else None, error_message=row.error_message,
        model_used=row.model_used, duration_ms=row.duration_ms, occurred_at=row.occurred_at,
    )


async def list_state(
    db: AsyncSession, *, entity_type: str | None, entity_id: uuid.UUID | None, trigger: str | None, search: str | None,
    first: date | None, last: date | None, descending: bool, limit: int, offset: int,
) -> Page[StateEntry]:
    since, until = _bounds(first, last)
    rows, total = await logs_repo.search_state(
        db, entity_type=entity_type, entity_id=entity_id, trigger=trigger, search=search, since=since, until=until,
        descending=descending, limit=limit, offset=offset,
    )
    labels = await logs_repo.labels_for(db, {(r.entity_type, r.entity_id) for r in rows})
    names = await logs_repo.user_names(db, {r.triggered_by_id for r in rows if r.triggered_by_id})
    return Page(items=[_state(r, labels, names) for r in rows], total=total)


async def list_steps(
    db: AsyncSession, *, entity_type: str | None, entity_id: uuid.UUID | None, job_id: uuid.UUID | None, step_name: str | None,
    result: str | None, search: str | None, first: date | None, last: date | None, sort: str, descending: bool, limit: int, offset: int,
) -> Page[StepEntry]:
    since, until = _bounds(first, last)
    rows, total = await logs_repo.search_steps(
        db, entity_type=entity_type, entity_id=entity_id, job_id=job_id, step_name=step_name, result=result, search=search,
        since=since, until=until, sort=sort, descending=descending, limit=limit, offset=offset,
    )
    labels = await logs_repo.labels_for(db, {(r.entity_type, r.entity_id) for r in rows if r.entity_type and r.entity_id})
    return Page(items=[StepEntry(**_step(r, labels)) for r in rows], total=total)


async def step_detail(db: AsyncSession, step_id: uuid.UUID) -> StepDetail:
    row = await logs_repo.get_step(db, step_id)
    if row is None:
        raise NotFoundError("Step not found")
    labels = await logs_repo.labels_for(db, {(row.entity_type, row.entity_id)} if row.entity_type and row.entity_id else set())
    return StepDetail(**_step(row, labels), input_summary=row.input_summary, output_summary=row.output_summary, attempt_history=row.attempt_history)


async def facets(db: AsyncSession) -> LogFacets:
    found = await logs_repo.facets(db)
    return LogFacets(**found, results=[r.value for r in StepResult], triggers=[t.value for t in TriggerType])
