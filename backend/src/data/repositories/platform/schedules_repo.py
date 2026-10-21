from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import ScheduledRun

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def seed(db: AsyncSession, definitions: dict[str, str]) -> None:
    """Make sure every schedule has a row (name -> kind). New rows start at the epoch, so each
    fires on first boot; existing rows are left exactly as they are."""
    for name, kind in definitions.items():
        await db.execute(
            insert(ScheduledRun)
            .values(schedule_name=name, kind=kind, last_run_at=EPOCH)
            .on_conflict_do_nothing(index_elements=["schedule_name"])
        )
    await db.commit()


async def lock_row(db: AsyncSession, name: str) -> ScheduledRun | None:
    """The schedule's row, locked; None when another worker holds it (SKIP LOCKED) or it is missing."""
    return await db.scalar(
        select(ScheduledRun)
        .where(ScheduledRun.schedule_name == name)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


async def get_row(db: AsyncSession, name: str) -> ScheduledRun | None:
    return await db.get(ScheduledRun, name)
