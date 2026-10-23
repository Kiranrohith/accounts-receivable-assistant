"""Read side for credit notes."""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from core.services.payments import note_mail
from core.services.payments.identities import note_violations
from data.models.postgres import Note
from data.models.postgres.enums import AllocationStatus, NoteStatus
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.payments import payments_repo
from data.repositories.payments.payments_repo import NoteMoney
from data.repositories.platform import logs_repo
from schemas.payments.notes import NoteDetail, NoteEmail, NoteListItem, NoteSpendOut, NoteTimelineEntry

_ZERO = Decimal("0.00")


def _item(note: Note, customer_name: str) -> NoteListItem:
    return NoteListItem(
        note_id=note.id,
        note_number=note.note_number,
        customer_id=note.customer_id,
        customer_name=customer_name,
        amount=note.amount,
        remaining_amount=note.remaining_amount,
        status=note.status.value,
        reason=note.reason,
        linked_invoice_id=note.linked_invoice_id,
        linked_payment_id=note.linked_payment_id,
        created_at=note.created_at,
    )


async def list_notes(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    status: NoteStatus | None,
    search: str | None,
    limit: int,
    offset: int,
) -> list[NoteListItem]:
    rows = await payments_repo.list_notes(
        db, customer_id=customer_id, status=status, search=search, limit=limit, offset=offset
    )
    return [_item(n, name) for n, name in rows]


async def get_note(db: AsyncSession, note_id: uuid.UUID) -> NoteDetail:
    note = await payments_repo.get_note(db, note_id)
    if note is None:
        raise NotFoundError("Credit note not found")
    customer = await customers_repo.get_by_id(db, note.customer_id)
    spends = await payments_repo.spends_for_note(db, note.id)
    log = await logs_repo.list_state_log(db, "note", note.id)
    invoice = await invoices_repo.get_invoice(db, note.linked_invoice_id) if note.linked_invoice_id else None
    payment = await payments_repo.get_payment(db, note.linked_payment_id) if note.linked_payment_id else None
    spent = sum((a.allocated_amount for a, _ in spends if a.status is AllocationStatus.ACTIVE), _ZERO)
    ok = not note_violations(
        NoteMoney(note.id, note.note_number, note.status, note.amount, note.remaining_amount, spent)
    )
    return NoteDetail(
        **_item(note, customer.customer_name if customer else "").model_dump(),
        email=NoteEmail(**await note_mail.status(db, note)),
        linked_invoice_number=invoice.invoice_number if invoice else None,
        linked_payment_reference=payment.reference_number if payment else None,
        spends=[
            NoteSpendOut(
                allocation_id=a.id,
                invoice_id=a.invoice_id,
                invoice_number=number,
                amount=a.allocated_amount,
                status=a.status.value,
                created_at=a.created_at,
            )
            for a, number in spends
        ],
        timeline=[
            NoteTimelineEntry(at=e.occurred_at, from_state=e.from_state, to_state=e.to_state, reason=e.reason)
            for e in log
        ],
        money_identity_ok=ok,
    )
