"""Match a parked payment again, or settle a payment whose money is held for a dispute.

A payment whose target invoice was on purchase-order hold waits in state Matching; the release of
the hold queues a payment_rematch job (see parking.py) and the worker runs the matcher again.

The same job type serves a payment with a slice held for a dispute: when a dispute on that invoice
is classified or resolved the job re-checks the hold (release.py).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invoices.processing import finish_job
from core.services.payments import release
from core.services.payments.processing import match_payment_step
from data.models.postgres import Job, SourceDocument
from data.models.postgres.enums import JobState, PaymentState
from data.repositories.payments import payments_repo


async def handle(db: AsyncSession, job: Job, document: SourceDocument) -> None:
    """Job handler for payment_rematch."""
    payment = (await payments_repo.payments_by_document(db, [document.id])).get(document.id)
    if release.is_held(payment):
        await release.handle_held(db, job, document, payment.id)
        return
    if payment is None or payment.state is not PaymentState.MATCHING:
        await finish_job(db, job, JobState.SUCCEEDED)  # decided meanwhile: nothing to do
        return
    await match_payment_step(db, job, document, payment.id)
