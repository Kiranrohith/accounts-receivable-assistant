"""The scheduler: recurring work (the dispute deadline scan today; reminders and mail polling later).

One row per schedule in `scheduled_runs`, persisted, so a restart cannot make anything run twice and
nothing is lost while the process is down. Every worker calls tick() regularly; for each schedule it
tries to lock the row FOR UPDATE SKIP LOCKED (so N workers never take the same one), checks whether
it is due, runs it, and records the time, all in the one transaction that holds the lock. A run that
fails is rolled back and tried again on the next tick: the actions are idempotent.

Two kinds of schedule:
  interval   due when now >= last_run_at + the interval
  daily_ist  due when the IST date of last_run_at is before today's IST date AND the IST clock has
             reached the scan time; rows start at the epoch, so each fires once on its first day
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import DEADLINE_SCAN_TIME_IST
from core.services.disputes import calendar, deadlines
from data.clients.db import session_scope
from data.models.postgres.enums import StepResult
from data.repositories.platform import logs_repo, schedules_repo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schedule:
    kind: str  # "interval" | "daily_ist"
    seconds: float = 0  # interval
    at: time | None = None  # daily_ist: the IST clock time it becomes due


SCHEDULES: dict[str, Schedule] = {
    "deadline_scan": Schedule("daily_ist", at=DEADLINE_SCAN_TIME_IST),
}

Action = Callable[[datetime], Awaitable[dict[str, Any]]]
ACTIONS: dict[str, Action] = {"deadline_scan": deadlines.run_scheduled}


def is_due(schedule: Schedule, last_run_at: datetime, now: datetime) -> bool:
    if schedule.kind == "interval":
        return now >= last_run_at + timedelta(seconds=schedule.seconds)
    local = now.astimezone(calendar.IST)
    return calendar.to_ist_date(last_run_at) < local.date() and local.time() >= (schedule.at or time(0))


async def seed() -> None:
    async with session_scope() as db:
        await schedules_repo.seed(db, {name: s.kind for name, s in SCHEDULES.items()})


def _log(db: AsyncSession, name: str, result: StepResult, started: datetime, **fields) -> None:  # noqa: ANN003
    logs_repo.add_step_log(
        db,
        entity_type="schedule",
        step_name=name,
        result=result,
        occurred_at=datetime.now(UTC),
        duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        **fields,
    )


async def tick(
    now: datetime | None = None,
    *,
    schedules: dict[str, Schedule] | None = None,
    actions: dict[str, Action] | None = None,
) -> list[str]:
    """Run whatever is due and not already being run by another worker. Returns the names that ran."""
    now = now or datetime.now(UTC)
    ran: list[str] = []
    for name, schedule in (schedules or SCHEDULES).items():
        started = datetime.now(UTC)
        async with session_scope() as db:
            row = await schedules_repo.lock_row(db, name)
            if row is None or not is_due(schedule, row.last_run_at, now):
                await db.rollback()
                continue
            try:
                summary = await (actions or ACTIONS)[name](now)
            except Exception as exc:  # stays due: the next tick tries again
                logger.exception("schedule %s failed", name)
                await db.rollback()
                async with session_scope() as log_db:
                    _log(log_db, name, StepResult.ERROR, started, error_message=str(exc))
                    await log_db.commit()
                continue
            row.last_run_at = now
            _log(db, name, StepResult.SUCCESS, started, output_summary=summary)
            await db.commit()
            ran.append(name)
    return ran
