"""The dispute dashboard: volume, reasons, age, deadlines and who is carrying what."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.disputes import calendar
from core.services.disputes.queries import age_days, first_response_overdue, resolution_overdue
from data.models.postgres.enums import DisputeStatus
from data.repositories.disputes import disputes_repo
from schemas.disputes.disputes import CountBy, DayVolume, DisputeDashboard, OwnerLoad

_STATUS_ORDER = [s.value for s in DisputeStatus]
_AGE_BUCKETS = (("0-2 days", 0, 2), ("3-5 days", 3, 5), ("6-10 days", 6, 10), ("over 10 days", 11, 10**6))
UNCATEGORISED = "Uncategorised"


async def dashboard(db: AsyncSession, *, days: int) -> DisputeDashboard:
    today = calendar.today_ist()
    by_status = dict(await disputes_repo.counts_by_status(db))
    open_rows = await disputes_repo.open_rows(db)

    categories = {
        (key or UNCATEGORISED): (open_n, total)
        for key, open_n, total in await disputes_repo.counts_by_category(db)
    }

    ages = Counter()
    for d in open_rows:
        a = age_days(d, today)
        for name, low, high in _AGE_BUCKETS:
            if low <= a <= high:
                ages[name] += 1
                break

    carrying: dict = {}
    overdue_by_owner: Counter = Counter()
    for d in open_rows:
        if d.assigned_to is not None:
            carrying[d.assigned_to] = carrying.get(d.assigned_to, 0) + 1
            if first_response_overdue(d, today) or resolution_overdue(d, today):
                overdue_by_owner[d.assigned_to] += 1
    names = await disputes_repo.user_names(db, list(carrying))

    # the window is whole IST days ending today
    first_day = today - timedelta(days=days - 1)
    since = datetime.combine(first_day, datetime.min.time(), tzinfo=calendar.IST).astimezone(UTC)
    raised, resolved = await disputes_repo.daily_volume(db, since)
    volume = [
        DayVolume(day=day, raised=raised.get(day, 0), resolved=resolved.get(day, 0))
        for day in (first_day + timedelta(days=n) for n in range(days))
    ]

    average = await disputes_repo.average_days_to_resolve(db)
    return DisputeDashboard(
        open=len(open_rows),
        resolved=by_status.get(DisputeStatus.RESOLVED.value, 0),
        unclassified=by_status.get(DisputeStatus.RAISED.value, 0),
        escalated=by_status.get(DisputeStatus.ESCALATED.value, 0),
        first_response_overdue=sum(first_response_overdue(d, today) for d in open_rows),
        resolution_overdue=sum(resolution_overdue(d, today) for d in open_rows),
        average_days_to_resolve=None if average is None else round(average, 1),
        by_status=[CountBy(key=s, open=0 if s == "Resolved" else by_status.get(s, 0), total=by_status.get(s, 0))
                   for s in _STATUS_ORDER],
        by_category=[
            CountBy(key=k, open=o, total=t) for k, (o, t) in sorted(categories.items(), key=lambda kv: (-kv[1][1], kv[0]))
        ],
        open_by_age=[CountBy(key=name, open=ages[name], total=ages[name]) for name, _, _ in _AGE_BUCKETS],
        volume=volume,
        owners=sorted(
            (
                OwnerLoad(user_id=uid, name=names.get(uid, "?"), open=n, overdue=overdue_by_owner[uid])
                for uid, n in carrying.items()
            ),
            key=lambda o: (-o.open, o.name),
        ),
    )
