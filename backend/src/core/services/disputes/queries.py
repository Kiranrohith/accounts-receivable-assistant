"""Reading disputes: the list, the classify queue, and one dispute with its supporting facts."""

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from core.services.disputes import calendar, outbound
from core.services.payments.listing import invoice_payments
from data.models.postgres import Dispute
from data.models.postgres.enums import DisputeCategory, DisputeStatus
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.common import Page
from schemas.disputes.disputes import (
    DisputeDetail,
    DisputeInvoice,
    DisputeListItem,
    DisputeMessageOut,
    DisputePayment,
    HeldPayment,
    PossiblyRelated,
    RelatedDispute,
    TimelineEntry,
)


def is_open(dispute: Dispute) -> bool:
    return dispute.status is not DisputeStatus.RESOLVED


def first_response_overdue(dispute: Dispute, today: date) -> bool:
    return (
        is_open(dispute)
        and dispute.first_response_at is None
        and dispute.first_response_due is not None
        and today > dispute.first_response_due
    )


def resolution_overdue(dispute: Dispute, today: date) -> bool:
    return is_open(dispute) and dispute.resolution_due is not None and today > dispute.resolution_due


def age_days(dispute: Dispute, today: date) -> int:
    end = calendar.to_ist_date(dispute.resolved_at) if dispute.resolved_at else today
    return max((end - calendar.to_ist_date(dispute.received_at)).days, 0)


def _item(
    dispute: Dispute, customer: str | None, invoice: str | None, owner: str | None, today: date
) -> DisputeListItem:
    return DisputeListItem(
        dispute_id=dispute.id,
        status=dispute.status.value,
        category=dispute.category.value if dispute.category else None,
        payment_related=dispute.payment_related,
        classification_confidence=dispute.classification_confidence,
        summary=dispute.summary,
        customer_id=dispute.customer_id,
        customer_name=customer,
        invoice_id=dispute.invoice_id,
        invoice_number=invoice,
        assigned_to=dispute.assigned_to,
        assigned_to_name=owner,
        received_at=dispute.received_at,
        age_days=age_days(dispute, today),
        first_response_due=dispute.first_response_due,
        first_response_at=dispute.first_response_at,
        first_response_overdue=first_response_overdue(dispute, today),
        resolution_due=dispute.resolution_due,
        resolution_overdue=resolution_overdue(dispute, today),
        escalated_at=dispute.escalated_at,
        resolved_at=dispute.resolved_at,
        linked_dispute_id=dispute.linked_dispute_id,
    )


async def list_disputes(
    db: AsyncSession,
    *,
    statuses: list[DisputeStatus] | None,
    open_only: bool,
    category: DisputeCategory | None,
    customer_id: uuid.UUID | None,
    invoice_id: uuid.UUID | None,
    assigned_to: uuid.UUID | None,
    unassigned: bool,
    overdue: bool,
    received_from: datetime | None,
    received_to: datetime | None,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> list[DisputeListItem]:
    today = calendar.today_ist()
    rows = await disputes_repo.list_disputes(
        db,
        today=today,
        statuses=statuses,
        open_only=open_only,
        category=category,
        customer_id=customer_id,
        invoice_id=invoice_id,
        assigned_to=assigned_to,
        unassigned=unassigned,
        overdue=overdue,
        received_from=received_from,
        received_to=received_to,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )
    return [_item(d, c, i, o, today) for d, c, i, o in rows]


async def search_disputes(
    db: AsyncSession,
    *,
    statuses: list[DisputeStatus] | None,
    open_only: bool,
    category: DisputeCategory | None,
    customer_id: uuid.UUID | None,
    assigned_to: uuid.UUID | None,
    unassigned: bool,
    overdue: bool,
    search: str | None,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> Page[DisputeListItem]:
    today = calendar.today_ist()
    rows, total = await disputes_repo.search_disputes(
        db, today=today, statuses=statuses, open_only=open_only, category=category, customer_id=customer_id,
        assigned_to=assigned_to, unassigned=unassigned, overdue=overdue, search=search, sort=sort,
        descending=descending, limit=limit, offset=offset,
    )
    return Page(items=[_item(d, c, i, o, today) for d, c, i, o in rows], total=total)


async def classify_queue(db: AsyncSession, *, limit: int, offset: int) -> list[DisputeListItem]:
    today = calendar.today_ist()
    rows = await disputes_repo.classify_queue(db, limit=limit, offset=offset)
    return [_item(d, c, i, o, today) for d, c, i, o in rows]


def _related(dispute: Dispute) -> RelatedDispute:
    return RelatedDispute(
        dispute_id=dispute.id,
        status=dispute.status.value,
        category=dispute.category.value if dispute.category else None,
        received_at=dispute.received_at,
    )


async def possibly_related(db: AsyncSession, dispute: Dispute) -> list[PossiblyRelated]:
    """Other disputes on the same invoice (decision 62): computed on read, nothing stored, never
    merged. A dispute with no invoice has none."""
    if dispute.invoice_id is None:
        return []
    chain = {dispute.linked_dispute_id}
    rows = await disputes_repo.others_on_invoice(db, dispute.invoice_id, dispute.id)
    return [
        PossiblyRelated(
            dispute_id=d.id,
            link=f"/disputes/{d.id}",
            status=d.status.value,
            category=d.category.value if d.category else None,
            assigned_to=d.assigned_to,
            assigned_to_name=owner,
            received_at=d.received_at,
            resolved_at=d.resolved_at,
            in_same_chain=d.id in chain or d.linked_dispute_id == dispute.id,
        )
        for d, owner in rows
    ]


async def get_dispute(db: AsyncSession, dispute_id: uuid.UUID) -> DisputeDetail:
    row = await disputes_repo.get_with_names(db, dispute_id)
    if row is None:
        raise NotFoundError("Dispute not found")
    dispute, customer_name, invoice_number, owner_name = row
    today = calendar.today_ist()

    invoice_out: DisputeInvoice | None = None
    payments: list[DisputePayment] = []
    if dispute.invoice_id is not None:
        invoice = await invoices_repo.get_invoice(db, dispute.invoice_id)
        if invoice is not None:
            match = await po_repo.get_match(db, invoice.id)
            invoice_out = DisputeInvoice(
                invoice_id=invoice.id,
                invoice_number=invoice.invoice_number,
                status=invoice.status.value,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                invoice_currency=invoice.invoice_currency,
                amount_inr=invoice.amount_inr,
                collectible_total=invoice.collectible_total,
                amount_paid=invoice.amount_paid,
                collectible_balance=invoice.collectible_balance,
                po_number=invoice.po_number,
                purchase_order_id=match.po_id if match else None,
                po_hold=invoice.po_hold,
            )
            payments = [
                DisputePayment(
                    payment_id=p.payment_id,
                    payment_reference=p.payment_reference,
                    note_number=p.note_number,
                    funded_by=p.funded_by,
                    allocated_amount=p.allocated_amount,
                    status=p.status,
                    created_at=p.created_at,
                )
                for p in await invoice_payments(db, invoice.id)
            ]

    messages = [
        DisputeMessageOut(
            message_id=m.id,
            kind=m.kind.value,
            subject=outbound.split(m.body)[0] if m.kind.value in ("draft", "sent") else None,
            body=outbound.split(m.body)[1] if m.kind.value in ("draft", "sent") else m.body,
            sent_at=m.sent_at,
            author_id=m.created_by,
            author_name=name,
            created_at=m.created_at,
        )
        for m, name in await disputes_repo.messages_for(db, dispute.id)
    ]
    held = [
        HeldPayment(
            payment_id=p.id, payment_reference=p.reference_number, state=p.state.value, held_amount=p.held_amount
        )
        for p in await disputes_repo.held_payments(db, dispute.id)
    ]
    earlier = (
        await disputes_repo.get_dispute(db, dispute.linked_dispute_id) if dispute.linked_dispute_id else None
    )
    timeline = [
        TimelineEntry(
            from_state=e.from_state,
            to_state=e.to_state,
            triggered_by_type=e.triggered_by_type.value if e.triggered_by_type else None,
            triggered_by_id=e.triggered_by_id,
            reason=e.reason,
            occurred_at=e.occurred_at,
        )
        for e in await logs_repo.list_state_log(db, "dispute", dispute.id)
    ]
    return DisputeDetail(
        **_item(dispute, customer_name, invoice_number, owner_name, today).model_dump(),
        from_email=dispute.from_email,
        clarification_confidence=dispute.clarification_confidence,
        classified_by=dispute.classified_by,
        classified_at=dispute.classified_at,
        resolved_by=dispute.resolved_by,
        resolution_narrative=dispute.resolution_narrative,
        source_document_id=dispute.source_document_id,
        messages=messages,
        invoice=invoice_out,
        payments=payments,
        held_payments=held,
        earlier_dispute=_related(earlier) if earlier else None,
        later_disputes=[_related(d) for d in await disputes_repo.later_disputes(db, dispute.id)],
        possibly_related=await possibly_related(db, dispute),
        timeline=timeline,
    )

