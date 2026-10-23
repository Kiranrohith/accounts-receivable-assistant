from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_current_user
from core.services.queues import queues
from data.clients.db import get_db
from data.models.postgres import User
from schemas.common import Page
from schemas.queues.queues import QueueItem, QueueSummary

router = APIRouter(prefix="/queues", tags=["queues"])


@router.get(
    "",
    response_model=list[QueueSummary],
    summary="The queues I can act on, with how many items each holds",
    description=(
        "Any signed-in user. Only the queues your role works are listed, in screen order; a queue with nothing in it "
        "is listed with count 0. Several queues are yours alone (the reviews of your own uploads, the disputes and "
        "tasks assigned to you), so the same queue can hold different counts for different people."
    ),
)
async def my_queues(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[QueueSummary]:
    return await queues.catalog(db, user)


@router.get(
    "/items",
    response_model=Page[QueueItem],
    summary="Everything waiting for me, in one list",
    description=(
        "Any signed-in user. One queue (`queue=`) or every queue you can act on, merged. `q` searches the number or "
        "name and the customer; `sort` is newest, oldest, amount (largest first) or deadline (soonest first, items "
        "without one last). Sorting, searching and paging all happen in the database; `total` is the whole match. "
        "Open an item with the endpoint of its queue, using `item_id`."
    ),
    responses={403: {"description": "Your role does not work that queue"}, 404: {"description": "No such queue"}},
)
async def queue_items(
    queue: str | None = Query(None, description="A queue key from GET /queues; omit for all"),
    q: str | None = Query(None, max_length=100),
    sort: Literal["newest", "oldest", "amount", "deadline"] = Query("newest"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[QueueItem]:
    return await queues.items(db, user, queue=queue, search=q, sort=sort, limit=limit, offset=offset)
