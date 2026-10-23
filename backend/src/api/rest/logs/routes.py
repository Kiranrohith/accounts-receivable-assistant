import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_db, require_permission
from core.services.logs import audit
from data.models.postgres import User
from data.models.postgres.enums import StepResult, TriggerType
from schemas.common import Page
from schemas.logs.logs import LogFacets, StateEntry, StepDetail, StepEntry

router = APIRouter(prefix="/logs", tags=["logs"])

_DAYS = "`from` and `to` are IST calendar days, both included."


@router.get(
    "/facets",
    response_model=LogFacets,
    summary="What the log filters can offer",
    description="Requires logs.timeline.view (ADM). The record kinds that have state changes or steps, the step names, and the fixed results and triggers.",
)
async def facets(_: User = Depends(require_permission("logs.timeline.view")), db: AsyncSession = Depends(get_db)) -> LogFacets:
    return await audit.facets(db)


@router.get(
    "/state",
    response_model=Page[StateEntry],
    summary="The state log: what happened to records, a page at a time",
    description=(
        "Requires logs.timeline.view (ADM). Every recorded change of state, with the record's name, who or what did it and why. "
        "`entity_type` and `entity_id` narrow it to one kind or to one record's whole timeline; `trigger` is user, system or scheduler; "
        f"`q` matches the reason or the states as plain text. {_DAYS} Newest first by default (`dir=asc` for the order it happened)."
    ),
)
async def state_log(
    entity_type: str | None = Query(None, max_length=60),
    entity_id: uuid.UUID | None = Query(None),
    trigger: TriggerType | None = Query(None),
    q: str | None = Query(None, max_length=100),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    dir: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("logs.timeline.view")),
    db: AsyncSession = Depends(get_db),
) -> Page[StateEntry]:
    return await audit.list_state(
        db, entity_type=entity_type, entity_id=entity_id, trigger=trigger.value if trigger else None, search=q, first=from_, last=to,
        descending=dir == "desc", limit=limit, offset=offset,
    )


@router.get(
    "/steps",
    response_model=Page[StepEntry],
    summary="The step log: what each background step did, a page at a time",
    description=(
        "Requires logs.steplog.view (ADM). One row per step of a job (reading a document, deciding a mail, a scan...), with its result, "
        "model, time taken and error. Inputs, outputs and the retry history come with GET /logs/steps/{id}. Filters: `entity_type`, `entity_id`, "
        "`job_id`, `step_name`, `result` (success, warning, error), `q` (step, error or model as plain text). "
        f"{_DAYS} `sort` is occurred_at (default) or duration; newest or longest first by default."
    ),
)
async def step_log(
    entity_type: str | None = Query(None, max_length=60),
    entity_id: uuid.UUID | None = Query(None),
    job_id: uuid.UUID | None = Query(None),
    step_name: str | None = Query(None, max_length=80),
    result: StepResult | None = Query(None),
    q: str | None = Query(None, max_length=100),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    sort: Literal["occurred_at", "duration"] = Query("occurred_at"),
    dir: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("logs.steplog.view")),
    db: AsyncSession = Depends(get_db),
) -> Page[StepEntry]:
    return await audit.list_steps(
        db, entity_type=entity_type, entity_id=entity_id, job_id=job_id, step_name=step_name, result=result.value if result else None,
        search=q, first=from_, last=to, sort=sort, descending=dir == "desc", limit=limit, offset=offset,
    )


@router.get(
    "/steps/{step_id}",
    response_model=StepDetail,
    summary="One step in full",
    description="Requires logs.steplog.view (ADM). The step with its input summary, output summary and retry history.",
    responses={404: {"description": "No such step"}},
)
async def step_detail(
    step_id: uuid.UUID, _: User = Depends(require_permission("logs.steplog.view")), db: AsyncSession = Depends(get_db)
) -> StepDetail:
    return await audit.step_detail(db, step_id)
