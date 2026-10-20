import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import StateLog, StepLog


def add_state_log(db: AsyncSession, **fields: Any) -> None:
    db.add(StateLog(**fields))


def add_step_log(db: AsyncSession, **fields: Any) -> None:
    db.add(StepLog(**fields))


async def list_state_log(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[StateLog]:
    result = await db.scalars(
        select(StateLog)
        .where(StateLog.entity_type == entity_type, StateLog.entity_id == entity_id)
        .order_by(StateLog.occurred_at, StateLog.created_at)
    )
    return list(result)


async def latest_step_for_entities(
    db: AsyncSession, entity_type: str, step_name: str, entity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StepLog]:
    result = await db.scalars(
        select(StepLog)
        .where(
            StepLog.entity_type == entity_type,
            StepLog.step_name == step_name,
            StepLog.entity_id.in_(entity_ids),
        )
        .order_by(StepLog.occurred_at)
    )
    return {step.entity_id: step for step in result}  # later rows overwrite earlier ones


async def list_step_logs(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[StepLog]:
    result = await db.scalars(
        select(StepLog)
        .where(StepLog.entity_type == entity_type, StepLog.entity_id == entity_id)
        .order_by(StepLog.occurred_at)
    )
    return list(result)
