from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_db, require_permission
from core.services.mail import triage_queue
from data.models.postgres import User
from schemas.common import Page
from schemas.mail.triage import TriageItem

router = APIRouter(prefix="/mail/log", tags=["mail"])


@router.get(
    "",
    response_model=Page[TriageItem],
    summary="Every mail we have read, a page at a time",
    description=(
        "Requires mail.triage.classify (FA, ADM). `outcome` says what became of the mail: `waiting` (a person has to route it, "
        "including mails the reader never classified), `routed` (handed to the payment, dispute or purchase order pipeline), "
        "`ignored` (general mail, set aside), `failed`, `reading` (registered and not decided yet) or `all` (default). `q` matches "
        "the sender or the subject as plain text. `sort` is received_at (default), sender or subject; `dir` asc or desc "
        "(newest first by default). Open one with GET /mail/triage/{id}."
    ),
)
async def mail_log(
    outcome: Literal["all", "waiting", "routed", "ignored", "failed", "reading"] = Query("all"),
    q: str | None = Query(None, max_length=100),
    sort: Literal["received_at", "sender", "subject"] = Query("received_at"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("mail.triage.classify")),
    db: AsyncSession = Depends(get_db),
) -> Page[TriageItem]:
    return await triage_queue.mail_log(
        db, outcome=outcome, search=q, sort=sort, descending=(dir != "asc") if dir else sort == "received_at", limit=limit, offset=offset
    )
