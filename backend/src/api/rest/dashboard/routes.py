from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.reports import receivables
from data.clients.db import get_db
from data.models.postgres import User
from schemas.reports.receivables import ReceivablesSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/receivables",
    response_model=ReceivablesSummary,
    summary="What is owed, what is late, and how old it is",
    description=(
        "Requires invoices.view. Everything is INR and counts only invoices still to collect (Open and PartlyPaid): the "
        "total, the overdue part (due date before today in IST), what falls due within 7 days, the disputed and PO-held "
        "ones, the aging buckets (not yet due, 1-30, 31-60, 61-90, over 90 days late; disputed invoices stay in their "
        "bucket and are also counted as disputed), the five customers owing the most past their due dates, money received "
        "so far this month (rejected and failed payments left out) and money received but not applied to an invoice."
    ),
)
async def receivables_summary(
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> ReceivablesSummary:
    return await receivables.summary(db)
