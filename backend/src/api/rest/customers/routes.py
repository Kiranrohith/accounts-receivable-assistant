import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.customers import tds
from core.services.customers.customers import (
    add_alias,
    create_customer,
    get_customer_detail,
    list_customers_with_stats,
    update_customer,
)
from data.clients.db import get_db
from data.models.postgres import User
from schemas.customers.customers import (
    AliasCreate,
    CustomerCreate,
    CustomerDetail,
    CustomerListItem,
    CustomerOut,
    CustomerUpdate,
    TdsDecide,
    TdsRecomputeResult,
    TdsReviewDecision,
    TdsReviewDetail,
)

router = APIRouter(prefix="/customers", tags=["customers"])

_NOT_FOUND = {404: {"description": "No such customer"}}


@router.post(
    "",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a customer",
    description="Requires customers.manage. GSTIN, when given, must be unique.",
)
async def create(
    body: CustomerCreate,
    actor: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> CustomerOut:
    return CustomerOut.model_validate(await create_customer(db, actor, body))


@router.get(
    "",
    response_model=list[CustomerListItem],
    summary="List customers",
    description=(
        "Requires customers.view. Active customers, alphabetical, each with the figures the list shows: invoices still "
        "to collect (`open_invoices`), how many are past due (`overdue_invoices`), what they add up to (`outstanding`, INR) "
        "and money received that is not yet against an invoice (`unapplied_cash`, INR)."
    ),
)
async def list_all(
    _: User = Depends(require_permission("customers.view")),
    db: AsyncSession = Depends(get_db),
) -> list[CustomerListItem]:
    return await list_customers_with_stats(db)


# The TDS review paths are declared before /{customer_id} so the dynamic path does not shadow them.
@router.get(
    "/tds-reviews/{review_id}",
    response_model=TdsReviewDetail,
    summary="One TDS recompute decision: the old and new figures",
    description="Requires customers.manage. Only the person who asked for the recompute can see and decide on it.",
)
async def tds_review(
    review_id: uuid.UUID,
    user: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> TdsReviewDetail:
    return await tds.get_review(db, user, review_id)


@router.post(
    "/tds-reviews/{review_id}/approve",
    response_model=TdsReviewDecision,
    summary="Apply the new TDS to the invoice",
    description=(
        "Requires customers.manage and a reason. The invoice's old state is kept as a superseded version. Refused (409) when "
        "the invoice or the customer's TDS setting changed since it was proposed, or the invoice is now paid; 422 when more "
        "was already paid than the new amount to collect."
    ),
    responses={409: {"description": "Already decided, or things changed since"}, 422: {"description": "Cannot be applied"}},
)
async def approve_tds(
    review_id: uuid.UUID,
    body: TdsDecide,
    user: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> TdsReviewDecision:
    return await tds.approve(db, user, review_id, body)


@router.post(
    "/tds-reviews/{review_id}/reject",
    response_model=TdsReviewDecision,
    summary="Leave the invoice as it is",
    description="Requires customers.manage and a reason. Nothing changes on the invoice.",
    responses={409: {"description": "Already decided"}},
)
async def reject_tds(
    review_id: uuid.UUID,
    body: TdsDecide,
    user: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> TdsReviewDecision:
    return await tds.reject(db, user, review_id, body)


@router.get(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="One customer: figures, confirmed aliases, and how many invoices still carry the old TDS",
    description=(
        "Requires customers.view. `aliases` lists the names and e-mail addresses a person confirmed for this customer, "
        "each with who confirmed it. `tds_out_of_date` counts invoices still to collect whose frozen TDS does not match "
        "the customer's setting: what the recompute action would change."
    ),
    responses=_NOT_FOUND,
)
async def detail(
    customer_id: uuid.UUID,
    _: User = Depends(require_permission("customers.view")),
    db: AsyncSession = Depends(get_db),
) -> CustomerDetail:
    return await get_customer_detail(db, customer_id)


@router.patch(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="Edit a customer",
    description=(
        "Requires customers.manage. Send only the fields to change. GSTIN, name and e-mail must stay unique (409 "
        "otherwise). Changing `deducts_tds` is prospective: it applies to invoices created from now on; use "
        "POST /customers/{id}/recompute-tds for the ones already saved."
    ),
    responses={**_NOT_FOUND, 409: {"description": "Another customer has the same GSTIN, name or e-mail"}},
)
async def edit(
    customer_id: uuid.UUID,
    body: CustomerUpdate,
    actor: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> CustomerDetail:
    return await update_customer(db, actor, customer_id, body)


@router.post(
    "/{customer_id}/aliases",
    response_model=CustomerDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm an alias: another name or e-mail address this customer uses",
    description=(
        "Requires customers.alias.confirm. A payer name or sender address that is not the customer's own, so later "
        "payment advices and mails from it are recognised. 409 when it is already an alias of this customer, is the "
        "customer's own name or address, or belongs to another customer."
    ),
    responses={**_NOT_FOUND, 409: {"description": "Not usable as an alias"}},
)
async def add_customer_alias(
    customer_id: uuid.UUID,
    body: AliasCreate,
    actor: User = Depends(require_permission("customers.alias.confirm")),
    db: AsyncSession = Depends(get_db),
) -> CustomerDetail:
    return await add_alias(db, actor, customer_id, body)


@router.post(
    "/{customer_id}/recompute-tds",
    response_model=TdsRecomputeResult,
    summary="Bring the customer's open invoices in line with its TDS setting",
    description=(
        "Requires customers.manage. For every invoice still to collect whose TDS differs from the customer's setting: an "
        "Open invoice is updated at once as a new version; a part-paid or disputed one gets a decision for you in Take "
        "Action (queue tds_review); a Paid invoice and debit notes are left alone. An invoice that already has a decision "
        "waiting, or that was paid more than the new amount to collect, is skipped and listed with the reason. Safe to repeat."
    ),
    responses=_NOT_FOUND,
)
async def recompute(
    customer_id: uuid.UUID,
    user: User = Depends(require_permission("customers.manage")),
    db: AsyncSession = Depends(get_db),
) -> TdsRecomputeResult:
    return await tds.recompute(db, user, customer_id)
