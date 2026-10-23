"""The receivables figures the dashboard shows: what is owed, what is late, how old it is, who owes most."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.disputes.calendar import today_ist
from data.repositories.reports import receivables_repo
from schemas.reports.receivables import AgingBucket, OverdueCustomer, ReceivablesSummary

TOP_OVERDUE = 5


async def summary(db: AsyncSession) -> ReceivablesSummary:
    today = today_ist()
    totals = await receivables_repo.totals(db, today)
    cash = await receivables_repo.cash(db, today.replace(day=1))
    return ReceivablesSummary(
        as_of=today,
        **totals,
        received_this_month_count=cash["received_count"],
        received_this_month_amount=cash["received_amount"],
        unapplied_amount=cash["unapplied_amount"],
        aging=[AgingBucket(**b) for b in await receivables_repo.aging(db, today)],
        top_overdue=[OverdueCustomer(**c) for c in await receivables_repo.top_overdue(db, today, TOP_OVERDUE)],
    )
