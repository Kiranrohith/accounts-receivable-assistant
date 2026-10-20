"""Match a parked payment again.

A payment whose target invoice was on purchase-order hold waits in state Matching; the release of
the hold queues a payment_rematch job (see parking.py) and the worker runs the matcher again.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.processing import finish_job
from core.services.payments.processing import match_payment_step
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import JobState, PaymentState
from data.repositories.payments import payments_repo


async def handle(db: AsyncSession, job: Job, document: SourceDocument) -> None:
    """Job handler for payment_rematch."""
    payment = (await payments_repo.payments_by_document(db, [document.id])).get(document.id)
    if payment is None or payment.state is not PaymentState.MATCHING:
        await finish_job(db, job, JobState.SUCCEEDED)  # decided meanwhile: nothing to do
        return
    await match_payment_step(db, job, document, payment.id)
