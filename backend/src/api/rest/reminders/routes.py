import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.reminders import workflow
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import ReminderState
from schemas.reminders.reminders import (
    EditReminder,
    RejectReminder,
    ReminderDetail,
    ReminderItem,
    ReminderResult,
    ReminderStateName,
    ScanResult,
)

# /scan is declared before /{reminder_id} so the dynamic path does not shadow it.
router = APIRouter(prefix="/reminders", tags=["reminders"])

_ERRORS = {
    404: {"description": "No such reminder"},
    409: {"description": "It is not a draft any more, or it is no longer needed (then it is cancelled)"},
}


@router.get(
    "",
    response_model=list[ReminderItem],
    summary="List reminders",
    description=(
        "Requires reminders.view_sent (finance associates, team members, admins): this is also the read-only answer to "
        "'was this customer notified?'. Newest first. Filter by `state` (repeat it), `invoice_id` or `customer_id`. A reminder "
        "is Drafted (waiting for a finance associate), Sent, Rejected, AutoCancelled (a payment arrived or a dispute opened "
        "first) or SkippedPastThreshold. While it waits, `days_overdue` and `balance` are live."
    ),
)
async def list_reminders(
    state: list[ReminderStateName] | None = Query(None),
    invoice_id: uuid.UUID | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("reminders.view_sent")),
    db: AsyncSession = Depends(get_db),
) -> list[ReminderItem]:
    return await workflow.list_reminders(
        db,
        states=[ReminderState(s) for s in state] if state else None,
        invoice_id=invoice_id,
        customer_id=customer_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/scan",
    response_model=ScanResult,
    summary="Run the reminder scan now",
    description=(
        "Requires users.manage (admin). The same scan the scheduler runs every morning at 09:00 IST: it drafts the reminder "
        "due today for every open or partly paid invoice (only the highest tier reached; lower ones it never wrote are "
        "recorded as skipped; a tier on a weekend waits for Monday), cancels drafts that are no longer needed, hands invoices "
        "60 days overdue to a finance team member, and closes collection tasks whose invoice was paid. Safe to repeat: "
        "a second run changes nothing. `as_of` runs it as if it were that moment; it still changes real data."
    ),
)
async def run_scan(
    as_of: datetime | None = Query(None),
    _: User = Depends(require_permission("users.manage")),
) -> ScanResult:
    return ScanResult(**await workflow.run_scan(as_of))


@router.get(
    "/{reminder_id}",
    response_model=ReminderDetail,
    summary="One reminder: the draft, a live preview, and its history",
    description=(
        "Requires reminders.view_sent. `subject` / `body` are the stored draft with the two changing figures as markers "
        "([[balance]], [[days_past_due]]); `preview_*` is exactly what would be sent right now. `can_send` is false (with "
        "`blocked_reason`) when the invoice is paid or in dispute, the customer holds unapplied cash, or there is no valid "
        "address. Once sent, `sent_subject` / `sent_body` are the text the customer received."
    ),
    responses={404: {"description": "No such reminder"}},
)
async def reminder_detail(
    reminder_id: uuid.UUID,
    _: User = Depends(require_permission("reminders.view_sent")),
    db: AsyncSession = Depends(get_db),
) -> ReminderDetail:
    return await workflow.get_reminder(db, reminder_id)


@router.patch(
    "/{reminder_id}",
    response_model=ReminderResult,
    summary="Edit a draft's wording",
    description=(
        "Requires reminders.manage (finance associate, admin). Subject and body only: the recipient is the customer's "
        "address and cannot be changed. Keep the [[balance]] and [[days_past_due]] markers where the figures belong: they "
        "are filled in at the moment of sending."
    ),
    responses=_ERRORS,
)
async def edit_reminder(
    reminder_id: uuid.UUID,
    body: EditReminder,
    user: User = Depends(require_permission("reminders.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReminderResult:
    return await workflow.edit_reminder(db, user, reminder_id, body)


@router.post(
    "/{reminder_id}/approve",
    response_model=ReminderResult,
    summary="Approve a draft: this sends it",
    description=(
        "Requires reminders.manage. Optionally carries last edits (`subject`, `body`). The figures are filled in with the "
        "true balance and day count now, the mail goes to the customer's address (with the debit note's PDF for a debit note), "
        "and the reminder is marked Sent. 409 if it is no longer needed (then it is cancelled), 422 without a valid address, "
        "502 if the mail server would not take it (it stays a draft; approve again once fixed)."
    ),
    responses={**_ERRORS, 422: {"description": "The customer has no valid e-mail address"}, 502: {"description": "The mail could not be sent"}},
)
async def approve_reminder(
    reminder_id: uuid.UUID,
    body: EditReminder | None = None,
    user: User = Depends(require_permission("reminders.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReminderResult:
    return await workflow.approve(db, user, reminder_id, body)


@router.post(
    "/{reminder_id}/reject",
    response_model=ReminderResult,
    summary="Reject a draft",
    description="Requires reminders.manage. Nothing is sent; the reason is kept. A later scan may draft the tier again.",
    responses=_ERRORS,
)
async def reject_reminder(
    reminder_id: uuid.UUID,
    body: RejectReminder,
    user: User = Depends(require_permission("reminders.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReminderResult:
    return await workflow.reject(db, user, reminder_id, body.reason)
