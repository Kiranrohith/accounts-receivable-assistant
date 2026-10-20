import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_current_user, require_permission
from core.services.disputes import dashboard as dashboard_service
from core.services.disputes import queries, workflow
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import DisputeCategory, DisputeStatus
from schemas.disputes.disputes import (
    ActionResult,
    AddNote,
    CategoryName,
    ClassifyDispute,
    CreateDispute,
    CreateResult,
    DisputeDashboard,
    DisputeDetail,
    DisputeListItem,
    NoteResult,
    ReassignDispute,
    ResolveDispute,
    StatusName,
)

# /dashboard and /classify-queue are declared before /{dispute_id} so the dynamic path does not shadow them.
router = APIRouter(prefix="/disputes", tags=["disputes"])

_ACT_ERRORS = {
    403: {"description": "Your role may not do this, or the dispute belongs to someone else"},
    404: {"description": "No such dispute"},
    409: {"description": "Someone else got there first, or the dispute is not in a state that allows it"},
}


@router.post(
    "",
    response_model=CreateResult,
    status_code=201,
    summary="Enter a complaint by hand",
    description=(
        "Requires disputes.work.normal. Give the invoice (the customer follows from it) or only "
        "the customer for a complaint about the account. With a `category` it is classified by you "
        "and handed to the least-loaded finance associate; without one it waits in the classify "
        "queue. Only the mail thread joins disputes: with a `gmail_thread_id` that belongs to an OPEN "
        "dispute your text is added to it as a message (`appended_to_existing` is true) and its owner "
        "is notified; in the thread of a RESOLVED dispute a new dispute is opened, linked to it. "
        "Otherwise it is its own dispute, even if the invoice already has others (those are listed "
        "as `possibly_related`; their deadlines are separate). Flagging `payment_related` says the "
        "customer withheld or short-paid on purpose and gave this as the reason."
    ),
    responses={
        404: {"description": "No such invoice / customer"},
        422: {"description": "The invoice belongs to a different customer, or nothing to attach to"},
    },
)
async def create_dispute(
    body: CreateDispute,
    user: User = Depends(require_permission("disputes.work.normal")),
    db: AsyncSession = Depends(get_db),
) -> CreateResult:
    return await workflow.create_manual(db, user, body)


@router.get(
    "",
    response_model=list[DisputeListItem],
    summary="List disputes",
    description=(
        "Requires disputes.dashboard.view. Filter by `status` (repeat it for several), "
        "`open_only`, `category`, `customer_id`, `invoice_id`, `assigned_to`, `unassigned`, "
        "`overdue` (a first response or the resolution still outstanding past its date), and the "
        "`received_from` / `received_to` window. Sort by `received_at` (default), "
        "`first_response_due`, `resolution_due` or `status`. `age_days` counts calendar days (IST) "
        "since it was received, up to resolution."
    ),
)
async def list_disputes(
    status: list[StatusName] | None = Query(None),
    open_only: bool = Query(False),
    category: CategoryName | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    invoice_id: uuid.UUID | None = Query(None),
    assigned_to: uuid.UUID | None = Query(None),
    unassigned: bool = Query(False),
    overdue: bool = Query(False),
    received_from: datetime | None = Query(None),
    received_to: datetime | None = Query(None),
    sort: Literal["received_at", "first_response_due", "resolution_due", "status"] = Query("received_at"),
    order: Literal["desc", "asc"] = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("disputes.dashboard.view")),
    db: AsyncSession = Depends(get_db),
) -> list[DisputeListItem]:
    return await queries.list_disputes(
        db,
        statuses=[DisputeStatus(s) for s in status] if status else None,
        open_only=open_only,
        category=DisputeCategory(category) if category else None,
        customer_id=customer_id,
        invoice_id=invoice_id,
        assigned_to=assigned_to,
        unassigned=unassigned,
        overdue=overdue,
        received_from=received_from,
        received_to=received_to,
        sort=sort,
        descending=order == "desc",
        limit=limit,
        offset=offset,
    )


@router.get(
    "/classify-queue",
    response_model=list[DisputeListItem],
    summary="Disputes waiting to be classified",
    description=(
        "Requires disputes.classify. Raised disputes, oldest first: the reader was not sure of the "
        "category (it shows its suggestion, if any, in `category`) or could not find the customer or "
        "invoice. Decide one with POST /disputes/{id}/classify."
    ),
)
async def classify_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("disputes.classify")),
    db: AsyncSession = Depends(get_db),
) -> list[DisputeListItem]:
    return await queries.classify_queue(db, limit=limit, offset=offset)


@router.get(
    "/dashboard",
    response_model=DisputeDashboard,
    summary="Dispute dashboard",
    description=(
        "Requires disputes.dashboard.view. Open and resolved counts, the classify queue and "
        "escalations, deadlines currently missed, average days to resolve, counts by status and by "
        "reason (category), open disputes by age, volume raised and resolved per IST day over the "
        "last `days`, and what each person is carrying."
    ),
)
async def dashboard(
    days: int = Query(30, ge=1, le=180),
    _: User = Depends(require_permission("disputes.dashboard.view")),
    db: AsyncSession = Depends(get_db),
) -> DisputeDashboard:
    return await dashboard_service.dashboard(db, days=days)


@router.get(
    "/{dispute_id}",
    response_model=DisputeDetail,
    summary="One dispute, with what is needed to answer it",
    description=(
        "Requires disputes.dashboard.view. The complaint, every message (follow-ups, drafts, "
        "replies, internal notes), the invoice with its purchase order link, what has been paid "
        "against it, payments held for this dispute, the earlier / later disputes it is linked to, "
        "`possibly_related` (other disputes on the same invoice, any status, each with a link: a "
        "hint for the person, never merged), and the timeline of every status change."
    ),
    responses={404: {"description": "No such dispute"}},
)
async def dispute_detail(
    dispute_id: uuid.UUID,
    _: User = Depends(require_permission("disputes.dashboard.view")),
    db: AsyncSession = Depends(get_db),
) -> DisputeDetail:
    return await queries.get_dispute(db, dispute_id)


@router.post(
    "/{dispute_id}/classify",
    response_model=ActionResult,
    summary="Classify a dispute from the queue",
    description=(
        "Requires disputes.classify. For a Raised dispute: give its category, and whether it is "
        "payment related. A dispute that arrived without a customer or invoice can be given one "
        "here. "
        "It is then handed to the least-loaded finance associate."
    ),
    responses={**_ACT_ERRORS, 422: {"description": "The invoice / customer does not fit"}},
)
async def classify(
    dispute_id: uuid.UUID,
    body: ClassifyDispute,
    user: User = Depends(require_permission("disputes.classify")),
    db: AsyncSession = Depends(get_db),
) -> ActionResult:
    return await workflow.classify(db, user, dispute_id, body)


@router.post(
    "/{dispute_id}/reassign",
    response_model=ActionResult,
    summary="Give a dispute to someone else",
    description=(
        "Requires disputes.reassign (admin). Not for a Raised or Resolved dispute. The new owner "
        "must be active and able to work it (an escalated dispute needs someone who works "
        "escalated disputes). The new owner is notified."
    ),
    responses={**_ACT_ERRORS, 422: {"description": "That user cannot take it"}},
)
async def reassign(
    dispute_id: uuid.UUID,
    body: ReassignDispute,
    user: User = Depends(require_permission("disputes.reassign")),
    db: AsyncSession = Depends(get_db),
) -> ActionResult:
    return await workflow.reassign(db, user, dispute_id, body)


@router.post(
    "/{dispute_id}/notes",
    response_model=NoteResult,
    status_code=201,
    summary="Add an internal note",
    description=(
        "Needs disputes.work.normal, or disputes.work.escalated once the dispute is Escalated. "
        "Visible to staff only. Not on a Resolved dispute."
    ),
    responses=_ACT_ERRORS,
)
async def add_note(
    dispute_id: uuid.UUID,
    body: AddNote,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NoteResult:
    return await workflow.add_note(db, user, dispute_id, body)


@router.post(
    "/{dispute_id}/resolve",
    response_model=ActionResult,
    summary="Resolve a dispute",
    description=(
        "Needs disputes.work.normal, or disputes.work.escalated once the dispute is Escalated, and "
        "the dispute must be assigned to you (an admin can resolve any). Give a narrative of how it "
        "was settled. The invoice stops being flagged as disputed. A dispute that is still in the "
        "classify queue must be classified first."
    ),
    responses=_ACT_ERRORS,
)
async def resolve(
    dispute_id: uuid.UUID,
    body: ResolveDispute,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionResult:
    return await workflow.resolve(db, user, dispute_id, body)
