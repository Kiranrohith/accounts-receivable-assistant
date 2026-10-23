import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_db, require_permission
from core.services.mail import reroute, triage_queue
from data.models.postgres import User
from schemas.mail.triage import ClassifyMail, ClassifyResult, RerouteMail, RerouteResult, TriageDetail, TriageItem

router = APIRouter(prefix="/mail/triage", tags=["mail"])


@router.get(
    "",
    response_model=list[TriageItem],
    summary="Mails waiting for a person to route them, or the ignored list",
    description=(
        "Requires mail.triage.classify (FA, ADM). `list=queue` (default): mails the classifier was not sure about, "
        "a purchase order mixed with a payment advice, a PO mail with nothing usable attached, and mails the classifier never "
        "classified at all (no status, no suggestion) after they have waited UNCLASSIFIED_MAIL_MINUTES because the models were down; newest first. "
        "`list=ignored`: general mail that was set aside, so a wrong call can be undone. `list=routed`: mails "
        "already handed to a pipeline, newest first (where a wrong call is found and rerouted). Each item says what "
        "the classifier suggested and why, links to the mail in Gmail, and names the conversation of ours it continues."
    ),
)
async def list_triage(
    list: str = Query("queue", pattern="^(queue|ignored|routed)$"),  # noqa: A002
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("mail.triage.classify")),
    db: AsyncSession = Depends(get_db),
) -> list[TriageItem]:
    return await triage_queue.list_items(db, which=list, limit=limit, offset=offset)


@router.get(
    "/{email_id}",
    response_model=TriageDetail,
    summary="One mail with its text, attachments and history",
    description="Requires mail.triage.classify. The text is the newest message as received; each attachment shows what our system did with it and why.",
)
async def triage_detail(
    email_id: uuid.UUID,
    _: User = Depends(require_permission("mail.triage.classify")),
    db: AsyncSession = Depends(get_db),
) -> TriageDetail:
    return await triage_queue.get_detail(db, email_id)


@router.post(
    "/{email_id}/classify",
    response_model=ClassifyResult,
    summary="Route a mail",
    description=(
        "Requires mail.triage.classify. Works on a mail in the queue (including one never classified) or in the ignored list (409 otherwise). "
        "`general` moves it to the ignored list; payment, dispute, both or purchase_order records your decision "
        "and queues it for its pipeline. Two people routing the same mail at once: one wins, the other gets 409."
    ),
)
async def classify_mail(
    email_id: uuid.UUID,
    body: ClassifyMail,
    user: User = Depends(require_permission("mail.triage.classify")),
    db: AsyncSession = Depends(get_db),
) -> ClassifyResult:
    return await triage_queue.classify(db, user, email_id, body)


@router.post(
    "/{email_id}/reroute",
    response_model=RerouteResult,
    summary="Send a mail to the other pipeline (payment <-> dispute)",
    description=(
        "Requires mail.triage.classify. For a mail that was handed to the wrong pipeline: a payment that was really "
        "a complaint, or a complaint that was really a payment advice. Its jobs are withdrawn and what the first "
        "pipeline made is taken back (a payment under review is rejected; a complaint is withdrawn without any "
        "e-mail to the customer). Work that is already real is NOT undone: money already recorded (reverse the "
        "payment first), a complaint that was replied to, escalated or resolved: 409 with the reason, nothing changes."
    ),
)
async def reroute_mail(
    email_id: uuid.UUID,
    body: RerouteMail,
    user: User = Depends(require_permission("mail.triage.classify")),
    db: AsyncSession = Depends(get_db),
) -> RerouteResult:
    return await reroute.reroute(db, user, email_id, body)
