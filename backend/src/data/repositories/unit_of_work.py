"""Transaction boundaries. Repositories flush; services decide when the work commits."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class DuplicateRecordError(Exception):
    """A unique constraint rejected the write; the session has been rolled back."""


async def _guard(db: AsyncSession, write) -> None:  # noqa: ANN001
    try:
        await write()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateRecordError(str(exc.orig)) from exc


async def flush(db: AsyncSession) -> None:
    await _guard(db, db.flush)


async def commit(db: AsyncSession) -> None:
    await _guard(db, db.commit)


async def rollback(db: AsyncSession) -> None:
    await db.rollback()
