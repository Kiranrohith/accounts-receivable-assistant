"""Read side for payments: the list, one payment's full story, and an invoice's payments."""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from core.services.invoices.amounts import money
from core.services.payments.identities import payment_violations
from data.models.postgres import Payment
from data.models.postgres.enums import AllocationStatus, PaymentState
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.invoices.numbers import normalize_invoice_no
from data.repositories.payments import payments_repo
from data.repositories.payments.payments_repo import PaymentMoney
from data.repositories.platform import logs_repo
from schemas.payments.listing import (
    AllocationOut,
    InvoicePayment,
    NoteRef,
    OpenInvoiceOut,
    PaymentDetail,
    PaymentListItem,
    QuotedInvoice,
    TimelineEntry,
)

# a person can still decide a payment in these states
DECIDABLE = (PaymentState.NEEDS_REVIEW, PaymentState.UNAPPLIED)
_ZERO = Decimal("0.00")


def _item(payment: Payment, customer_name: str | None, file_name: str | None) -> PaymentListItem:
    foreign = payment.amount_received_foreign
    return PaymentListItem(
        payment_id=payment.id,
        state=payment.state.value,
        customer_id=payment.customer_id,
        customer_name=customer_name,
        payer_name=payment.payee_name,
        currency=payment.payment_currency,
        amount=foreign if foreign is not None else payment.amount_received_inr,
        amount_received_inr=payment.amount_received_inr,
        reference_number=payment.reference_number,
        payment_date=payment.payment_date,
        matched_layer=payment.matched_layer.value if payment.matched_layer else None,
        confidence_score=payment.confidence_score,
        unapplied_amount=payment.unapplied_amount,
        held_amount=payment.held_amount,
        credited_amount=payment.credited_amount,
        reasons=list(payment.review_reasons or []),
        source_document_id=payment.source_document_id,
        file_name=file_name,
        created_at=payment.created_at,
    )


async def list_payments(
    db: AsyncSession,
    *,
    state: PaymentState | None,
    customer_id: uuid.UUID | None,
    search: str | None,
    limit: int,
    offset: int,
    oldest_first: bool = False,
) -> list[PaymentListItem]:
    rows = await payments_repo.list_payments(
        db, state=state, customer_id=customer_id, search=search, limit=limit, offset=offset,
        oldest_first=oldest_first,
    )
    return [_item(p, name, file_name) for p, name, file_name in rows]


def _identity_ok(payment: Payment, allocations: list) -> bool:
    active = [a for a, _ in allocations if a.status is AllocationStatus.ACTIVE]
    row = PaymentMoney(
        payment.id,
        payment.state,
        payment.amount_received_inr,
        payment.unapplied_amount,
        payment.held_amount,
        payment.credited_amount,
        sum((a.allocated_amount + (a.tolerance_residual or _ZERO) for a in active), _ZERO),
        len(active),
    )
    return not payment_violations(row)


async def get_payment(db: AsyncSession, payment_id: uuid.UUID) -> PaymentDetail:
    found = await payments_repo.get_payment_with_names(db, payment_id)
    if found is None:
        raise NotFoundError("Payment not found")
    payment, customer_name, file_name = found
    allocations = await payments_repo.allocations_for_payment(db, payment.id)
    notes = await payments_repo.notes_for_payment(db, payment.id)
    log = await logs_repo.list_state_log(db, "payment", payment.id)

    quoted_rows = [r for r in (payment.extracted_invoice_numbers or []) if r.get("invoice_number")]
    known = {
        normalize_invoice_no(i.invoice_number): i
        for i in await invoices_repo.get_by_numbers(db, [r["invoice_number"].strip() for r in quoted_rows])
        if i.customer_id == payment.customer_id
    }
    quoted = []
    for row in quoted_rows:
        number = row["invoice_number"].strip()
        amount = row.get("amount")
        quoted.append(
            QuotedInvoice(
                invoice_number=number,
                stated_amount=Decimal(str(amount)) if amount not in (None, "") else None,
                known=normalize_invoice_no(number) in known,
                invoice_status=known[normalize_invoice_no(number)].status.value if normalize_invoice_no(number) in known else None,
            )
        )

    open_invoices: list[OpenInvoiceOut] = []
    if payment.state in DECIDABLE and payment.customer_id is not None:
        open_invoices = [
            OpenInvoiceOut(
                invoice_id=i.id,
                invoice_number=i.invoice_number,
                due_date=i.due_date,
                invoice_currency=i.invoice_currency,
                fx_rate=i.fx_rate_invoice,
                collectible_balance=money(i.collectible_balance),
                status=i.status.value,
                po_hold=i.po_hold,
                is_disputed=i.is_disputed,
            )
            for i in await invoices_repo.open_invoices_for_customer(db, payment.customer_id)
        ]

    document = (
        await documents_repo.get_document(db, payment.source_document_id)
        if payment.source_document_id
        else None
    )
    raw = dict(document.raw_extraction_json or {}) if document else {}
    raw.pop("classification", None)

    return PaymentDetail(
        **_item(payment, customer_name, file_name).model_dump(),
        payer_bank=payment.payee_bank,
        purpose=payment.purpose,
        from_email=payment.from_email,
        extracted_po=payment.extracted_po,
        credit_note_reference=payment.extracted_credit_note_ref,
        credit_note_requested=payment.credit_note_requested,
        quoted_invoices=quoted,
        match_reason=payment.match_reason,
        rematch_count=payment.rematch_count,
        duplicate_of=payment.duplicate_of,
        allocations=[
            AllocationOut(
                allocation_id=a.id,
                invoice_id=a.invoice_id,
                invoice_number=number,
                allocated_amount=a.allocated_amount,
                status=a.status.value,
                tolerance=a.tolerance_applied.value if a.tolerance_applied else None,
                tolerance_residual=a.tolerance_residual,
                fx_rate_used=a.fx_rate_used,
                matched_layer=a.matched_layer.value if a.matched_layer else None,
                created_at=a.created_at,
            )
            for a, number in allocations
        ],
        notes=[
            NoteRef(
                note_id=n.id,
                note_number=n.note_number,
                amount=n.amount,
                remaining_amount=n.remaining_amount,
                status=n.status.value,
            )
            for n in notes
        ],
        open_invoices=open_invoices,
        timeline=[
            TimelineEntry(at=e.occurred_at, from_state=e.from_state, to_state=e.to_state, reason=e.reason)
            for e in log
        ],
        money_identity_ok=_identity_ok(payment, allocations),
        extraction=raw or None,
    )


async def invoice_payments(db: AsyncSession, invoice_id: uuid.UUID) -> list[InvoicePayment]:
    rows = await payments_repo.allocations_for_invoice(db, invoice_id)
    return [
        InvoicePayment(
            allocation_id=a.id,
            funded_by="credit_note" if a.note_id else "payment",
            payment_id=a.payment_id,
            payment_reference=reference,
            note_number=note_number,
            allocated_amount=a.allocated_amount,
            status=a.status.value,
            tolerance=a.tolerance_applied.value if a.tolerance_applied else None,
            tolerance_residual=a.tolerance_residual,
            created_at=a.created_at,
        )
        for a, reference, note_number in rows
    ]
