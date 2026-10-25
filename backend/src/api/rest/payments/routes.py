import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.payments import listing, note_actions, reversal, review
from data.clients.db import get_db
from data.models.postgres import User
from data.models.postgres.enums import PaymentState
from schemas.common import Page
from schemas.payments.listing import PaymentDetail, PaymentListItem
from schemas.payments.notes import ConvertResult, ConvertUnapplied
from schemas.payments.reversal import ReversalResult, ReversePayment
from schemas.payments.review import (
    AliasResult,
    EnterAmount,
    LeaveUnapplied,
    ManualMatch,
    RejectPayment,
    ReviewResult,
    SaveAlias,
)

# /reviews is declared before /{payment_id} so the dynamic path does not shadow it.
router = APIRouter(prefix="/payments", tags=["payments"])

_STATES = Literal[
    "Received", "Reading", "Matching", "Applied", "PartiallyApplied", "Unapplied",
    "NeedsReview", "Rejected", "Held", "Failed",
]
_DECIDE_ERRORS = {
    404: {"description": "No such payment / invoice / customer"},
    409: {"description": "Already decided by someone else, or an invoice changed meanwhile"},
    422: {"description": "The decision does not make sense (amounts, customer, currency)"},
}


@router.get(
    "",
    response_model=list[PaymentListItem],
    summary="List payments",
    description=(
        "Requires payments.view. Newest first. Filter by `state`, `customer_id`, or `search` "
        "(contains, case-insensitive) a payment reference, the payer's name or the customer's "
        "name. `matched_layer` says how the payer was identified: L1 invoice number, L2 sender "
        "e-mail, L3 payer name, L4 a person."
    ),
)
async def list_payments(
    state: _STATES | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> list[PaymentListItem]:
    return await listing.list_payments(
        db,
        state=PaymentState(state) if state else None,
        customer_id=customer_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/reviews",
    response_model=list[PaymentListItem],
    summary="Payments waiting for a person",
    description=(
        "Requires payments.view. Payments in NeedsReview, oldest first, each with the plain-English "
        "`reasons` it could not be settled automatically. Open one with GET /payments/{id} to see "
        "the invoices it could be applied to."
    ),
)
async def reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> list[PaymentListItem]:
    return await listing.list_payments(
        db, state=PaymentState.NEEDS_REVIEW, customer_id=None, search=None, limit=limit,
        offset=offset, oldest_first=True,
    )


@router.get(
    "/search",
    response_model=Page[PaymentListItem],
    summary="Payments, a page at a time",
    description=(
        "Requires payments.view. The same rows as GET /payments, searched, filtered, sorted and cut to one page by the "
        "server, with the `total` of everything that matches. `q` matches the payment reference, the payer's name or the "
        "customer's name as plain text. `sort` is one of created (default, newest first), reference, payer, customer, "
        "date, amount, state; `dir` is asc or desc."
    ),
)
async def search_payments(
    q: str | None = Query(None, max_length=100),
    state: _STATES | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    sort: Literal["created", "reference", "payer", "customer", "date", "amount", "state"] = Query("created"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> Page[PaymentListItem]:
    return await listing.search_payments(
        db,
        state=PaymentState(state) if state else None,
        customer_id=customer_id,
        search=q,
        sort=sort,
        descending=(dir == "desc") if dir else sort == "created",
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentDetail,
    summary="One payment, with everything done to it",
    description=(
        "Requires payments.view. The extracted advice, how the payer was identified, the "
        "allocations (invoice, amount, tolerance class, signed residual, rate used), credit notes "
        "born from it, the state timeline, and whether the money adds up. While a person can "
        "still decide it (NeedsReview, Unapplied) it also lists the customer's open invoices."
    ),
    responses={404: {"description": "No such payment"}},
)
async def payment_detail(
    payment_id: uuid.UUID,
    _: User = Depends(require_permission("payments.view")),
    db: AsyncSession = Depends(get_db),
) -> PaymentDetail:
    return await listing.get_payment(db, payment_id)


@router.post(
    "/{payment_id}/match",
    response_model=ReviewResult,
    summary="Say which invoices a payment pays",
    description=(
        "Requires payments.review.match. For a NeedsReview or Unapplied payment. Give each "
        "invoice and how much of the payment goes to it (in the payment's own currency). Applied "
        "through the same locked, re-checked step as the automatic matcher. An amount over what "
        "an invoice owes becomes a credit note; a line on an invoice that is already Paid needs "
        "`confirm_repayment` and then becomes a credit note; for an INR payment, money left over "
        "stays unapplied (a foreign payment's lines must add up to what was paid). "
        "`save_alias` also remembers the payer name / sender e-mail (needs customers.alias.confirm)."
    ),
    responses=_DECIDE_ERRORS,
)
async def match(
    payment_id: uuid.UUID,
    body: ManualMatch,
    user: User = Depends(require_permission("payments.review.match")),
    db: AsyncSession = Depends(get_db),
) -> ReviewResult:
    return await review.manual_match(db, user, payment_id, body)


@router.post(
    "/{payment_id}/unapplied",
    response_model=ReviewResult,
    summary="Keep the money on the customer's account, to apply later",
    description=(
        "Requires payments.review.match. For a NeedsReview payment whose amount is known. The "
        "payment becomes Unapplied: the whole amount is held as unapplied cash for the customer. A "
        "foreign payment is valued provisionally at the frozen rate of the customer's open invoices "
        "in that currency when they share one, otherwise at the ECB rate for its date; the chosen "
        "invoice's frozen rate replaces that value when it is applied. Apply it later with "
        "POST /payments/{id}/match."
    ),
    responses=_DECIDE_ERRORS,
)
async def leave_unapplied(
    payment_id: uuid.UUID,
    body: LeaveUnapplied,
    user: User = Depends(require_permission("payments.review.match")),
    db: AsyncSession = Depends(get_db),
) -> ReviewResult:
    return await review.leave_unapplied(db, user, payment_id, body)


@router.post(
    "/{payment_id}/reject",
    response_model=ReviewResult,
    summary="Reject a payment",
    description=(
        "Requires payments.review.match. For a NeedsReview payment: it is not valid or not ours. "
        "A reason is required. The payment becomes Rejected with every money bucket at zero."
    ),
    responses=_DECIDE_ERRORS,
)
async def reject_payment(
    payment_id: uuid.UUID,
    body: RejectPayment,
    user: User = Depends(require_permission("payments.review.match")),
    db: AsyncSession = Depends(get_db),
) -> ReviewResult:
    return await review.reject(db, user, payment_id, body)


@router.post(
    "/{payment_id}/amount",
    response_model=ReviewResult,
    summary="Enter the amount the advice did not state",
    description=(
        "Requires payments.review.match. For a NeedsReview payment that has no amount (the "
        "advice named an invoice or reference but no amount). The amount is logged as entered "
        "by you and the payment is matched again straight away: the result says whether it was "
        "applied or still needs a person. When the advice has no transaction reference (or only a "
        "bank-rail word like NEFT) the reference must be given too (422 otherwise); a reference "
        "this customer already sent is a 409."
    ),
    responses=_DECIDE_ERRORS,
)
async def enter_amount(
    payment_id: uuid.UUID,
    body: EnterAmount,
    user: User = Depends(require_permission("payments.review.match")),
    db: AsyncSession = Depends(get_db),
) -> ReviewResult:
    return await review.enter_amount(db, user, payment_id, body)


@router.post(
    "/{payment_id}/convert-unapplied",
    response_model=ConvertResult,
    summary="Turn unapplied money into a credit note",
    description=(
        "Requires notes.create (FTM, ADM). For a payment with money still unapplied (Unapplied, or "
        "PartiallyApplied with an unapplied part). `amount` defaults to all of it. A credit note "
        "is created for the customer, the payment's unapplied amount falls and its credited amount "
        "rises in the same transaction; when nothing is left unapplied the payment becomes Applied."
    ),
    responses=_DECIDE_ERRORS,
)
async def convert_unapplied(
    payment_id: uuid.UUID,
    body: ConvertUnapplied,
    user: User = Depends(require_permission("notes.create")),
    db: AsyncSession = Depends(get_db),
) -> ConvertResult:
    return await note_actions.convert_unapplied(db, user, payment_id, body)


@router.post(
    "/{payment_id}/reverse",
    response_model=ReversalResult,
    summary="Reverse a payment's application",
    description=(
        "Requires payments.correction.rerun (FTM, ADM). For an Applied, PartiallyApplied or "
        "Unapplied payment; a reason is required. In one transaction: every allocation the payment "
        "made is reversed and its invoices get the money back (a Paid invoice can reopen); credit "
        "spends its own 'use our credit' instruction caused are undone (the note is given the "
        "amount back); a credit note born from it (an overpayment, or converted unapplied money) is "
        "Voided; the payment's buckets are cleared. `after` says what happens next: `rematch` runs "
        "the matcher again straight away, `review` leaves it for a person. If a credit note born "
        "from the payment has already been spent, the reversal is refused with 409 and nothing is "
        "changed."
    ),
    responses={
        404: {"description": "No such payment"},
        409: {"description": "Not reversible in this state, or its credit was already consumed"},
    },
)
async def reverse_payment(
    payment_id: uuid.UUID,
    body: ReversePayment,
    user: User = Depends(require_permission("payments.correction.rerun")),
    db: AsyncSession = Depends(get_db),
) -> ReversalResult:
    return await reversal.reverse_payment(db, user, payment_id, body)


@router.post(
    "/{payment_id}/save-alias",
    response_model=AliasResult,
    status_code=201,
    summary="Remember the payer's name or e-mail as a customer's alias",
    description=(
        "Requires customers.alias.confirm. Saves the advice's payer name (or sender e-mail) as an "
        "alias of the payment's customer (or `customer_id`), so the next advice from that name "
        "or address is recognised. 409 if it is already an alias of that customer."
    ),
    responses=_DECIDE_ERRORS,
)
async def save_alias(
    payment_id: uuid.UUID,
    body: SaveAlias,
    user: User = Depends(require_permission("customers.alias.confirm")),
    db: AsyncSession = Depends(get_db),
) -> AliasResult:
    return await review.save_alias(db, user, payment_id, body)
