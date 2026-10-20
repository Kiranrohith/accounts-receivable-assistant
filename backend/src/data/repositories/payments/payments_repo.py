import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, Invoice, Note, Payment, PaymentAllocation, SourceDocument
from data.models.postgres.enums import AllocationStatus, NoteStatus, PaymentState
from data.repositories import unit_of_work

_ZERO = Decimal("0")
_ACTIVE = PaymentAllocation.status == AllocationStatus.ACTIVE


async def insert_payment(db: AsyncSession, **fields: Any) -> Payment:
    payment = Payment(**fields)
    db.add(payment)
    await unit_of_work.flush(db)
    return payment


async def get_payment(db: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    return await db.get(Payment, payment_id)


async def lock_payment(db: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    """Row-lock the payment (and re-read it): only one worker applies a payment at a time."""
    return await db.scalar(
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def insert_allocations(
    db: AsyncSession, rows: list[dict[str, Any]]
) -> list[PaymentAllocation]:
    allocations = [PaymentAllocation(**row) for row in rows]
    db.add_all(allocations)
    await unit_of_work.flush(db)
    return allocations


async def next_note_number_value(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.nextval("note_number_seq"))))


async def insert_note(db: AsyncSession, **fields: Any) -> Note:
    note = Note(**fields)
    db.add(note)
    await unit_of_work.flush(db)
    return note


async def parked_payments_for_customer(db: AsyncSession, customer_id: uuid.UUID) -> list[Payment]:
    """Payments waiting in Matching because a target invoice was on hold (oldest first)."""
    result = await db.scalars(
        select(Payment)
        .where(
            Payment.customer_id == customer_id,
            Payment.state == PaymentState.MATCHING,
            Payment.match_reason.like("Parked:%"),
        )
        .order_by(Payment.created_at)
    )
    return list(result)


async def payments_by_document(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Payment]:
    result = await db.scalars(select(Payment).where(Payment.source_document_id.in_(document_ids)))
    return {p.source_document_id: p for p in result}


async def list_payments(
    db: AsyncSession,
    *,
    state: PaymentState | None,
    customer_id: uuid.UUID | None,
    search: str | None,
    limit: int,
    offset: int,
    oldest_first: bool = False,
) -> list[tuple[Payment, str | None, str | None]]:
    """Newest first (or oldest first): (payment, customer name, source file name)."""
    query = (
        select(Payment, Customer.customer_name, SourceDocument.file_name)
        .outerjoin(Customer, Customer.id == Payment.customer_id)
        .outerjoin(SourceDocument, SourceDocument.id == Payment.source_document_id)
    )
    if state is not None:
        query = query.where(Payment.state == state)
    if customer_id is not None:
        query = query.where(Payment.customer_id == customer_id)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.where(
            or_(
                Payment.reference_number.ilike(like),
                Payment.payee_name.ilike(like),
                Customer.customer_name.ilike(like),
            )
        )
    order = Payment.created_at.asc() if oldest_first else Payment.created_at.desc()
    rows = await db.execute(query.order_by(order, Payment.id).limit(limit).offset(offset))
    return [(p, name, file_name) for p, name, file_name in rows]


async def get_payment_with_names(
    db: AsyncSession, payment_id: uuid.UUID
) -> tuple[Payment, str | None, str | None] | None:
    row = (
        await db.execute(
            select(Payment, Customer.customer_name, SourceDocument.file_name)
            .outerjoin(Customer, Customer.id == Payment.customer_id)
            .outerjoin(SourceDocument, SourceDocument.id == Payment.source_document_id)
            .where(Payment.id == payment_id)
        )
    ).first()
    return None if row is None else (row[0], row[1], row[2])


async def allocations_for_payment(
    db: AsyncSession, payment_id: uuid.UUID
) -> list[tuple[PaymentAllocation, str]]:
    rows = await db.execute(
        select(PaymentAllocation, Invoice.invoice_number)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(PaymentAllocation.payment_id == payment_id)
        .order_by(PaymentAllocation.created_at, Invoice.invoice_number)
    )
    return [(a, number) for a, number in rows]


async def allocations_for_invoice(
    db: AsyncSession, invoice_id: uuid.UUID
) -> list[tuple[PaymentAllocation, str | None, str | None]]:
    """(allocation, the funding payment's reference, the funding note's number), oldest first."""
    rows = await db.execute(
        select(PaymentAllocation, Payment.reference_number, Note.note_number)
        .outerjoin(Payment, Payment.id == PaymentAllocation.payment_id)
        .outerjoin(Note, Note.id == PaymentAllocation.note_id)
        .where(PaymentAllocation.invoice_id == invoice_id)
        .order_by(PaymentAllocation.created_at)
    )
    return [(a, ref, number) for a, ref, number in rows]


async def eligible_notes_for_customer(db: AsyncSession, customer_id: uuid.UUID) -> list[Note]:
    """Notes that can still be spent (Open / PartiallyUsed with something left), oldest first."""
    result = await db.scalars(
        select(Note)
        .where(
            Note.customer_id == customer_id,
            Note.status.in_([NoteStatus.OPEN, NoteStatus.PARTIALLY_USED]),
            Note.remaining_amount > 0,
        )
        .order_by(Note.created_at, Note.note_number)
    )
    return list(result)


async def get_note(db: AsyncSession, note_id: uuid.UUID) -> Note | None:
    return await db.get(Note, note_id)


async def get_note_by_number(db: AsyncSession, number: str) -> Note | None:
    return await db.scalar(select(Note).where(func.upper(Note.note_number) == number.strip().upper()))


async def lock_notes(db: AsyncSession, note_ids: list[uuid.UUID]) -> list[Note]:
    """Row-lock these notes (id order), after the invoices: the same order everywhere."""
    if not note_ids:
        return []
    result = await db.scalars(
        select(Note)
        .where(Note.id.in_(note_ids))
        .order_by(Note.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result)


async def list_notes(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    status: NoteStatus | None,
    search: str | None,
    limit: int,
    offset: int,
) -> list[tuple[Note, str]]:
    query = select(Note, Customer.customer_name).join(Customer, Customer.id == Note.customer_id)
    if customer_id is not None:
        query = query.where(Note.customer_id == customer_id)
    if status is not None:
        query = query.where(Note.status == status)
    if search and search.strip():
        query = query.where(Note.note_number.ilike(f"%{search.strip()}%"))
    rows = await db.execute(
        query.order_by(Note.created_at.desc(), Note.id).limit(limit).offset(offset)
    )
    return [(n, name) for n, name in rows]


async def spends_for_note(
    db: AsyncSession, note_id: uuid.UUID
) -> list[tuple[PaymentAllocation, str]]:
    rows = await db.execute(
        select(PaymentAllocation, Invoice.invoice_number)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(PaymentAllocation.note_id == note_id)
        .order_by(PaymentAllocation.created_at)
    )
    return [(a, number) for a, number in rows]


async def active_allocations_of(db: AsyncSession, payment_id: uuid.UUID) -> list[PaymentAllocation]:
    """Everything a reversal of this payment must undo: the allocations it funded, and the credit
    spends its own instruction caused. Manual spends are not here."""
    result = await db.scalars(
        select(PaymentAllocation)
        .where(
            or_(
                PaymentAllocation.payment_id == payment_id,
                PaymentAllocation.spent_for_payment_id == payment_id,
            ),
            PaymentAllocation.status == AllocationStatus.ACTIVE,
        )
        .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result)


async def notes_for_payment(db: AsyncSession, payment_id: uuid.UUID) -> list[Note]:
    result = await db.scalars(
        select(Note).where(Note.linked_payment_id == payment_id).order_by(Note.created_at)
    )
    return list(result)


async def find_duplicate(
    db: AsyncSession, customer_id: uuid.UUID, reference_key: str
) -> Payment | None:
    """The earliest still-standing payment of this customer with this reference (D4).

    `reference_key` is the normalised reference (see core.services.payments.routing.reference_key).
    A payment that was itself rejected or failed does not count: it never took the money.
    """
    return await db.scalar(
        select(Payment)
        .where(
            Payment.customer_id == customer_id,
            func.upper(func.btrim(Payment.reference_number)) == reference_key,
            Payment.state.not_in([PaymentState.REJECTED, PaymentState.FAILED]),
        )
        .order_by(Payment.created_at)
        .limit(1)
    )


@dataclass(frozen=True)
class InvoiceMoney:
    invoice_id: uuid.UUID
    invoice_number: str
    status: str
    collectible_total: Decimal
    amount_paid: Decimal
    allocated: Decimal  # sum of Active allocations, payment- and note-funded


@dataclass(frozen=True)
class PaymentMoney:
    payment_id: uuid.UUID
    state: PaymentState
    received_inr: Decimal | None
    unapplied: Decimal
    held: Decimal
    credited: Decimal
    allocated_plus_residual: Decimal  # sum of Active (allocated_amount + tolerance_residual)
    active_allocations: int


@dataclass(frozen=True)
class NoteMoney:
    note_id: uuid.UUID
    note_number: str
    status: NoteStatus
    amount: Decimal
    remaining: Decimal
    spent: Decimal  # sum of Active note-funded allocations


async def invoice_money(db: AsyncSession) -> list[InvoiceMoney]:
    allocated = func.coalesce(
        func.sum(PaymentAllocation.allocated_amount).filter(_ACTIVE), _ZERO
    )
    rows = await db.execute(
        select(Invoice, allocated)
        .outerjoin(PaymentAllocation, PaymentAllocation.invoice_id == Invoice.id)
        .group_by(Invoice.id)
    )
    return [
        InvoiceMoney(
            i.id, i.invoice_number, i.status.value, i.collectible_total, i.amount_paid, total
        )
        for i, total in rows
    ]


async def payment_money(db: AsyncSession) -> list[PaymentMoney]:
    own = PaymentAllocation.payment_id == Payment.id
    rows = await db.execute(
        select(
            Payment,
            func.coalesce(
                func.sum(
                    PaymentAllocation.allocated_amount
                    + func.coalesce(PaymentAllocation.tolerance_residual, _ZERO)
                ).filter(_ACTIVE),
                _ZERO,
            ),
            func.count(PaymentAllocation.id).filter(_ACTIVE),
        )
        .outerjoin(PaymentAllocation, own)
        .group_by(Payment.id)
    )
    return [
        PaymentMoney(
            p.id, p.state, p.amount_received_inr, p.unapplied_amount, p.held_amount,
            p.credited_amount, total, count,
        )
        for p, total, count in rows
    ]


async def note_money(db: AsyncSession) -> list[NoteMoney]:
    spent = func.coalesce(func.sum(PaymentAllocation.allocated_amount).filter(_ACTIVE), _ZERO)
    rows = await db.execute(
        select(Note, spent)
        .outerjoin(PaymentAllocation, PaymentAllocation.note_id == Note.id)
        .group_by(Note.id)
    )
    return [
        NoteMoney(n.id, n.note_number, n.status, n.amount, n.remaining_amount, total)
        for n, total in rows
    ]
