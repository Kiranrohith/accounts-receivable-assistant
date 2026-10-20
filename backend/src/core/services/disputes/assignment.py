"""Who owns a dispute: the least-loaded active person of the right role, else an admin.

Load = the person's open (not Resolved) disputes. Normal disputes go to the FA pool, escalated ones
to the FTM pool; when a pool has nobody active the admin pool takes the work. Two disputes arriving
in the same instant can pick the same person; the balance is approximate, never wrong.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import DISPUTE_ESCALATION_ROLE, DISPUTE_FALLBACK_ROLE, DISPUTE_OWNER_ROLE
from core.services import notifications
from data.models.postgres import Dispute, User
from data.models.postgres.enums import DisputeStatus, NotificationType, TriggerType
from data.repositories.disputes import disputes_repo
from data.repositories.platform import logs_repo


async def pick_owner(db: AsyncSession, *, escalated: bool) -> User | None:
    first = DISPUTE_ESCALATION_ROLE if escalated else DISPUTE_OWNER_ROLE
    return await disputes_repo.least_loaded_user(db, first) or await disputes_repo.least_loaded_user(
        db, DISPUTE_FALLBACK_ROLE
    )


def log_state(
    db: AsyncSession,
    dispute: Dispute,
    before: DisputeStatus | None,
    reason: str,
    *,
    user_id: uuid.UUID | None = None,
    scheduler: bool = False,
) -> None:
    logs_repo.add_state_log(
        db,
        entity_type="dispute",
        entity_id=dispute.id,
        from_state=before.value if before else None,
        to_state=dispute.status.value,
        triggered_by_type=(
            TriggerType.USER if user_id else TriggerType.SCHEDULER if scheduler else TriggerType.SYSTEM
        ),
        triggered_by_id=user_id,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def tell_owner(
    db: AsyncSession, dispute: Dispute, owner: User, label: str, *, actor: uuid.UUID | None
) -> None:
    """The Assignment notice. A person who gave the work to themselves is not told about it."""
    if owner.id == actor:
        return
    await notifications.create(
        db,
        owner.id,
        NotificationType.ASSIGNMENT,
        f"Dispute {label} is assigned to you",
        entity_type="dispute",
        entity_id=dispute.id,
        actor=actor,
    )


async def label(db: AsyncSession, dispute: Dispute) -> str:
    """A short human name for notices: 'invoice INV-1 (Acme Ltd)', or the customer, or the id."""
    row = await disputes_repo.get_with_names(db, dispute.id)
    _, customer, invoice, _ = row if row else (dispute, None, None, None)
    if invoice and customer:
        return f"about invoice {invoice} ({customer})"
    if customer:
        return f"from {customer}"
    return f"{str(dispute.id)[:8]}"
