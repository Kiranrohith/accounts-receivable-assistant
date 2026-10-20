import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.payments import note_actions, note_queries
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import NoteStatus
from schemas.payments.notes import NewNote, NoteDetail, NoteListItem, SpendNote, SpendResult

router = APIRouter(prefix="/notes", tags=["credit notes"])

_ERRORS = {
    404: {"description": "No such note / invoice / customer"},
    409: {"description": "The note or invoice is not in a state that allows it"},
    422: {"description": "The amounts or the customer do not fit"},
}


@router.get(
    "",
    response_model=list[NoteListItem],
    summary="List credit notes",
    description=(
        "Requires payments.view. Newest first. Filter by `customer_id`, `status` (Open, "
        "PartiallyUsed, Closed, Voided) or `search` a note number. A note is always INR; "
        "`remaining_amount` is what can still be spent."
    ),
)
async def list_notes(
    customer_id: uuid.UUID | None = Query(None),
    status: Literal["Open", "PartiallyUsed", "Closed", "Voided"] | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> list[NoteListItem]:
    return await note_queries.list_notes(
        db,
        customer_id=customer_id,
        status=NoteStatus(status) if status else None,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{note_id}",
    response_model=NoteDetail,
    summary="One credit note, with what it has paid",
    description=(
        "Requires payments.view. Where the note came from (an overpayment, converted unapplied "
        "money, or a manual note), every invoice it has paid, its state timeline, and whether "
        "remaining = amount - spent."
    ),
    responses={404: {"description": "No such note"}},
)
async def note_detail(
    note_id: uuid.UUID,
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> NoteDetail:
    return await note_queries.get_note(db, note_id)


@router.post(
    "",
    response_model=NoteDetail,
    status_code=201,
    summary="Create a credit note",
    description=(
        "Requires notes.create (FTM, ADM). A credit for a customer, for a reason you give, "
        "optionally about one of their invoices. The note starts Open for the whole amount. A "
        "note is spent only on a remittance's instruction or with POST /notes/{id}/spend."
    ),
    responses=_ERRORS,
)
async def create_note(
    body: NewNote,
    user: User = Depends(require_permission("notes.create")),
    db: AsyncSession = Depends(get_db),
) -> NoteDetail:
    note_id = await note_actions.create_note(db, user, body)
    return await note_queries.get_note(db, note_id)


@router.post(
    "/{note_id}/spend",
    response_model=SpendResult,
    summary="Pay an invoice with a credit note",
    description=(
        "Requires notes.create (FTM, ADM). Moves credit from the note to one of the same "
        "customer's open invoices: the invoice's paid amount rises (Paid when it is fully paid) and "
        "the note's remaining amount falls (Open -> PartiallyUsed -> Closed). `amount` defaults to "
        "the most that fits. Refused for a closed or void note, a paid, held or disputed invoice, "
        "another customer's invoice, or more than the note holds or the invoice owes."
    ),
    responses=_ERRORS,
)
async def spend_note(
    note_id: uuid.UUID,
    body: SpendNote,
    user: User = Depends(require_permission("notes.create")),
    db: AsyncSession = Depends(get_db),
) -> SpendResult:
    return await note_actions.spend_note(db, user, note_id, body)
