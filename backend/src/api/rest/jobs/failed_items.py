import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_db, require_permission
from core.services.jobs import failed_items
from data.models.postgres import User
from schemas.common import Page
from schemas.jobs.failed_items import FailedItem, FailedKind, RerunOutcome, RerunRequest, RerunResults

router = APIRouter(prefix="/failed-items", tags=["failed items"])


@router.get(
    "",
    response_model=Page[FailedItem],
    summary="Background work that gave up, a page at a time",
    description=(
        "Requires failed_items.rerun (FA, FTM, ADM). The jobs that ended Failed (they cannot work as they stand) or DeadLettered "
        "(tried until the attempts ran out) and have not been run again since. `kind` narrows to document (an uploaded file), mail, "
        "dispute (the first reply to a complaint) or credit_note (its e-mail). `q` matches the file, subject, sender, customer, "
        "credit note number or the error as plain text. `sort` is failed_at (default), attempts or kind; `dir` asc or desc "
        "(newest first by default). A mail whose reading failed but which a person has routed since is not listed."
    ),
)
async def list_failed_items(
    kind: FailedKind | None = Query(None),
    q: str | None = Query(None, max_length=100),
    sort: Literal["failed_at", "attempts", "kind"] = Query("failed_at"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("failed_items.rerun")),
    db: AsyncSession = Depends(get_db),
) -> Page[FailedItem]:
    return await failed_items.list_failed(
        db, kind=kind, search=q, sort=sort, descending=(dir != "asc") if dir else sort == "failed_at", limit=limit, offset=offset
    )


@router.post(
    "/rerun",
    response_model=RerunResults,
    summary="Run several failed items again",
    description=(
        "Requires failed_items.rerun. Up to 50 `job_ids` from the list. Each is queued again as a new job of the same kind on "
        "the same record; one that cannot be (already run again, the file or record gone) is reported in `results` and the rest "
        "still go ahead. Always 200: read `ok` per item."
    ),
)
async def rerun_many(
    body: RerunRequest,
    user: User = Depends(require_permission("failed_items.rerun")),
    db: AsyncSession = Depends(get_db),
) -> RerunResults:
    return await failed_items.rerun_many(db, user, body.job_ids)


@router.post(
    "/{job_id}/rerun",
    response_model=RerunOutcome,
    summary="Run one failed item again",
    description=(
        "Requires failed_items.rerun. Queues the failed job again as a new job. 404 when there is no such item, 409 when it has "
        "not failed, was already run again, or its file or record is gone."
    ),
    responses={404: {"description": "No such failed item"}, 409: {"description": "Cannot be run again"}},
)
async def rerun_one(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("failed_items.rerun")),
    db: AsyncSession = Depends(get_db),
) -> RerunOutcome:
    return RerunOutcome(job_id=job_id, ok=True, new_job_id=await failed_items.rerun(db, user, job_id))
