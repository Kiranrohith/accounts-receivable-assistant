from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_db, require_any_permission
from core.services.mail import live
from data.models.postgres import User
from schemas.mail.live import LiveMailFeed

router = APIRouter(prefix="/mail/live", tags=["mail"])


@router.get(
    "",
    response_model=LiveMailFeed,
    summary="Mail being processed right now",
    description=(
        "Requires mail.triage.classify (FA, ADM) or mail.log.view (FTM). Every mail that is waiting to be read, being read, or whose attachments are "
        "being read, with the step each is at, and for `recent_seconds` (default 30) after it ends, how it ended: `done`, "
        "`ignored` (general mail set aside), `review` (a person decides: Take Action), `rejected` or `failed` (Failed Items). "
        "Meant to be polled every few seconds; it writes nothing. `as_of` is the server's clock: measure the age of an ended "
        "mail against it. Only mail is covered: a file a person uploads is followed on the Upload Documents page."
    ),
)
async def live_mail(
    recent_seconds: int = Query(30, ge=0, le=300),
    _: User = Depends(require_any_permission("mail.triage.classify", "mail.log.view")),
    db: AsyncSession = Depends(get_db),
) -> LiveMailFeed:
    return await live.live_feed(db, recent_seconds=recent_seconds)
