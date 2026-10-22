import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.reminders import tasks
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import TaskStatus
from schemas.reminders.reminders import CloseTask, TaskDetail, TaskItem

router = APIRouter(prefix="/tasks", tags=["collection tasks"])


@router.get(
    "",
    response_model=list[TaskItem],
    summary="List 60-day collection tasks",
    description=(
        "Requires tasks.collection.work (finance team members, admins). An invoice still unpaid 60 days past its due date "
        "(and not in dispute) becomes one task, assigned to the team member with the least work. Filter by `status` "
        "(Open / Closed) or `assigned_to`. Newest first."
    ),
)
async def list_tasks(
    status: Literal["Open", "Closed"] | None = Query(None),
    assigned_to: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("tasks.collection.work")),
    db: AsyncSession = Depends(get_db),
) -> list[TaskItem]:
    return await tasks.list_tasks(
        db, status=TaskStatus(status) if status else None, assigned_to=assigned_to, limit=limit, offset=offset
    )


@router.get(
    "/{task_id}",
    response_model=TaskDetail,
    summary="One collection task with its bundle",
    description=(
        "Requires tasks.collection.work. `payload` is everything the person needs: the invoice and what is left, the "
        "customer's contact, the payments applied so far and every reminder with its state."
    ),
    responses={404: {"description": "No such task"}},
)
async def task_detail(
    task_id: uuid.UUID,
    _: User = Depends(require_permission("tasks.collection.work")),
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    return await tasks.get_task(db, task_id)


@router.post(
    "/{task_id}/close",
    response_model=TaskDetail,
    summary="Close a collection task by hand",
    description=(
        "Requires tasks.collection.work. `ResolvedManually` (you got the money or settled it another way) or `Dismissed` "
        "(it should not have been chased), with a note. A task also closes by itself when its invoice is paid."
    ),
    responses={404: {"description": "No such task"}, 409: {"description": "Already closed"}},
)
async def close_task(
    task_id: uuid.UUID,
    body: CloseTask,
    user: User = Depends(require_permission("tasks.collection.work")),
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    return await tasks.close(db, user, task_id, body)
