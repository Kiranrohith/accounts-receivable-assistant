"""Credit notes. A note is born from money we hold that belongs to the customer: the excess of an
overpayment (T3). Notes are always INR and are spent only on an explicit instruction (see the spend
functions that follow in this module's later phase). Callers commit.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import CREDIT_NOTE_PREFIX
from core.services.invoices.amounts import money
from data.models.postgres import Note
from data.models.postgres.enums import NoteStatus, TriggerType
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo


def format_note_number(sequence_value: int, year: int) -> str:
    return f"{CREDIT_NOTE_PREFIX}-{year}-{sequence_value:06d}"


async def create_credit_note(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID,
    amount: Decimal,
    reason: str,
    actor: uuid.UUID | None,
    linked_invoice_id: uuid.UUID | None = None,
    linked_payment_id: uuid.UUID | None = None,
) -> Note:
    """A new Open note for `amount` (INR). The whole amount is still to be spent."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("A credit note must be for more than zero")
    now = datetime.now(UTC)
    number = format_note_number(await payments_repo.next_note_number_value(db), now.year)
    note = await payments_repo.insert_note(
        db,
        note_number=number,
        customer_id=customer_id,
        amount=amount,
        remaining_amount=amount,
        reason=reason,
        linked_invoice_id=linked_invoice_id,
        linked_payment_id=linked_payment_id,
        status=NoteStatus.OPEN,
        created_by=actor,
        updated_by=actor,
    )
    logs_repo.add_state_log(
        db,
        entity_type="note",
        entity_id=note.id,
        from_state=None,
        to_state=NoteStatus.OPEN.value,
        triggered_by_type=TriggerType.USER if actor else TriggerType.SYSTEM,
        triggered_by_id=actor,
        reason=reason,
        occurred_at=now,
    )
    return note
