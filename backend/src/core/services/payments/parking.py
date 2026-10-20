"""Payments parked on a purchase-order hold, and queuing them again when the hold is released.

A payment whose target invoice is on PO hold waits in state Matching (see engine.PARKED_PREFIX).
When the hold is released (an override or a passing re-check, or a corrected new version), a
payment_rematch job is queued for each waiting payment of that customer. Kept free of other
payment modules so the invoice and PO services can import it without a cycle.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.jobs import queue
from data.repositories.payments import payments_repo


async def queue_parked_payments(
    db: AsyncSession, customer_id: uuid.UUID, actor: uuid.UUID | None
) -> int:
    """Queue a re-match for every payment of this customer parked on a hold. The caller commits
    together with the release that triggered it. Returns how many were queued."""
    queued = 0
    for payment in await payments_repo.parked_payments_for_customer(db, customer_id):
        if payment.source_document_id is None:
            continue
        await queue.enqueue(
            db,
            job_type=queue.JOB_PAYMENT_REMATCH,
            entity_id=payment.source_document_id,
            created_by=actor,
        )
        queued += 1
    return queued
