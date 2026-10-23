"""Changing a customer's TDS setting, and what that does to invoices already saved.

The setting itself is prospective: it only shapes invoices created from now on. The recompute action brings the
invoices still to collect in line with it, like this:
- Open (nothing paid, not disputed): updated at once as a new version (the old state is kept as a superseded version).
- Part paid, or under dispute: a decision waits for the person who asked (reason tds_recompute), who sees the old and
  new figures and approves or rejects it. One Pending decision per invoice, like every other review.
- Paid, and debit notes (which never carry TDS): untouched.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailure
from core.services.invoices import versioning
from core.services.invoices.amounts import FrozenAmounts, format_money, freeze_amounts
from core.services.invoices.review import record_decision
from data.models.postgres import Customer, Invoice, PendingInvoiceChange, User
from data.models.postgres.enums import InvoiceStatus, InvoiceType, PendingChangeReason, PendingChangeStatus
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from schemas.customers.customers import (
    TdsDecide,
    TdsRecomputeResult,
    TdsReviewDecision,
    TdsReviewDetail,
    TdsSkip,
)

KIND = "tds_recompute"
_ZERO = Decimal("0.00")


def new_amounts(invoice: Invoice, deducts_tds: bool) -> FrozenAmounts:
    """The invoice's frozen amounts as they would be under this TDS setting (currency, rate and total do not move)."""
    foreign = invoice.amount_foreign is not None
    total = invoice.amount_foreign if foreign else invoice.amount_inr
    return freeze_amounts(
        total,
        rate=invoice.fx_rate_invoice or Decimal("1"),
        foreign=foreign,
        deducts_tds=deducts_tds,
        is_debit_note=invoice.type is InvoiceType.DEBIT_NOTE,
    )


async def _apply(db: AsyncSession, invoice: Invoice, frozen: FrozenAmounts, actor: uuid.UUID, deducts_tds: bool) -> int:
    """Save the current state as a superseded version, then take the new TDS. Returns the new version number."""
    lines = await invoices_repo.current_line_items(db, invoice)
    old_version = invoice.version
    old_tds, old_collectible = invoice.tds_amount or _ZERO, invoice.collectible_total
    await invoices_repo.insert_invoice_version(
        db,
        invoice_id=invoice.id,
        version_no=old_version,
        snapshot=versioning.snapshot_of(invoice, lines),
        change_summary={
            "changes": [
                {"field": "tds_amount", "label": "TDS", "old": str(old_tds), "new": str(frozen.tds_amount)},
                {"field": "collectible_total", "label": "Amount to collect", "old": str(old_collectible), "new": str(frozen.collectible_total)},
            ],
            "new_version": old_version + 1,
            "reason": KIND,
            "new_source_document_id": str(invoice.source_document_id) if invoice.source_document_id else None,
        },
        source_document_id=invoice.source_document_id,
        created_by=actor,
    )
    invoice.tds_amount = frozen.tds_amount
    invoice.collectible_total = frozen.collectible_total
    invoice.version = old_version + 1
    invoice.updated_by = actor
    logs_repo.add_state_log(
        db,
        entity_type="invoice",
        entity_id=invoice.id,
        from_state=invoice.status.value,
        to_state=invoice.status.value,
        reason=f"Version {invoice.version}: TDS {'is now deducted' if deducts_tds else 'is no longer deducted'} "
        f"(TDS {old_tds} to {frozen.tds_amount})",
        triggered_by_id=actor,
        occurred_at=datetime.now(UTC),
    )
    await db.flush()
    return invoice.version


@dataclass
class _Tally:
    applied: int = 0
    review: int = 0
    unchanged: int = 0

    def __post_init__(self) -> None:
        self.skipped: list[TdsSkip] = []


async def recompute(db: AsyncSession, user: User, customer_id: uuid.UUID) -> TdsRecomputeResult:
    customer = await customers_repo.get_for_update(db, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    wanted = customer.deducts_tds
    candidates = await invoices_repo.open_invoices_for_customer(db, customer.id)
    invoices = await invoices_repo.lock_invoices(db, [i.id for i in candidates])
    tally = _Tally()

    for invoice in sorted(invoices, key=lambda i: i.invoice_number):
        if invoice.status is InvoiceStatus.PAID or invoice.type is InvoiceType.DEBIT_NOTE:
            continue
        frozen = new_amounts(invoice, wanted)
        old_tds = invoice.tds_amount or _ZERO
        if frozen.tds_amount == old_tds:
            tally.unchanged += 1
            continue
        if frozen.collectible_total < invoice.amount_paid:
            tally.skipped.append(TdsSkip(
                invoice_number=invoice.invoice_number,
                reason=f"{format_money(invoice.amount_paid)} was already paid, more than the new amount to collect "
                f"({format_money(frozen.collectible_total)}); this needs a credit note",
            ))
            continue
        if await invoices_repo.open_pending_for_invoice(db, invoice.id) is not None:
            tally.skipped.append(TdsSkip(invoice_number=invoice.invoice_number, reason="a decision is already waiting for this invoice"))
            continue

        reasons = []
        if invoice.status is InvoiceStatus.PARTLY_PAID:
            reasons.append(f"Payments of {format_money(invoice.amount_paid)} were already received against the current amount to collect")
        if invoice.is_disputed:
            reasons.append("The invoice is under dispute")
        if not reasons:
            await _apply(db, invoice, frozen, user.id, wanted)
            tally.applied += 1
            continue
        await invoices_repo.insert_pending_change(
            db,
            invoice_id=invoice.id,
            source_document_id=invoice.source_document_id,
            reason=PendingChangeReason.TDS_RECOMPUTE,
            proposed_snapshot={
                "kind": KIND,
                "reasons": reasons,
                "deducts_tds": wanted,
                "base_version": invoice.version,
                "currency": (invoice.invoice_currency or "INR").strip(),
                "old_tds": str(old_tds),
                "new_tds": str(frozen.tds_amount),
                "old_collectible": str(invoice.collectible_total),
                "new_collectible": str(frozen.collectible_total),
                "amount_paid": str(invoice.amount_paid),
            },
            created_by=user.id,
        )
        tally.review += 1

    await unit_of_work.commit(db)
    parts = []
    if tally.applied:
        parts.append(f"{tally.applied} updated")
    if tally.review:
        parts.append(f"{tally.review} waiting for your decision in Take Action")
    if tally.unchanged:
        parts.append(f"{tally.unchanged} already correct")
    if tally.skipped:
        parts.append(f"{len(tally.skipped)} left alone")
    return TdsRecomputeResult(
        applied=tally.applied, sent_to_review=tally.review, unchanged=tally.unchanged, skipped=tally.skipped,
        message=("; ".join(parts) or "There are no invoices to look at") + ".",
    )


# ---------------------------------------------------------------- the decision on a part-paid or disputed invoice
def _own(item: PendingInvoiceChange | None, user: User) -> PendingInvoiceChange:
    if item is None or item.reason is not PendingChangeReason.TDS_RECOMPUTE or item.invoice_id is None:
        raise NotFoundError("Review item not found")
    if item.created_by != user.id:
        raise PermissionDeniedError("Only the person who asked for the recompute can decide on it")
    return item


async def get_review(db: AsyncSession, user: User, review_id: uuid.UUID) -> TdsReviewDetail:
    item = _own(await invoices_repo.get_pending(db, review_id), user)
    invoice = await invoices_repo.get_invoice(db, item.invoice_id)
    customer = await customers_repo.get_by_id(db, invoice.customer_id) if invoice else None
    s = item.proposed_snapshot
    return TdsReviewDetail(
        review_id=item.id,
        invoice_id=item.invoice_id,
        invoice_number=invoice.invoice_number if invoice else None,
        customer_name=customer.customer_name if customer else None,
        status=item.status.value,
        deducts_tds=bool(s["deducts_tds"]),
        reasons=s.get("reasons", []),
        currency=s.get("currency", "INR"),
        old_tds=Decimal(s["old_tds"]),
        new_tds=Decimal(s["new_tds"]),
        old_collectible=Decimal(s["old_collectible"]),
        new_collectible=Decimal(s["new_collectible"]),
        amount_paid=Decimal(s["amount_paid"]),
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
    )


async def _lock_decidable(db: AsyncSession, user: User, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _own(await invoices_repo.get_pending_for_update(db, review_id), user)
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


async def approve(db: AsyncSession, user: User, review_id: uuid.UUID, body: TdsDecide) -> TdsReviewDecision:
    item = await _lock_decidable(db, user, review_id)
    invoice = await invoices_repo.get_invoice_for_update(db, item.invoice_id)
    customer: Customer | None = await customers_repo.get_by_id(db, invoice.customer_id)
    snapshot = item.proposed_snapshot
    wanted = bool(snapshot["deducts_tds"])
    if invoice.version != snapshot["base_version"]:
        raise ConflictError("The invoice changed after this was proposed; reject it and recompute again")
    if customer is None or customer.deducts_tds != wanted:
        raise ConflictError("The customer's TDS setting changed after this was proposed; reject it and recompute again")
    if invoice.status is InvoiceStatus.PAID:
        raise ConflictError(f"Invoice {invoice.invoice_number} is already paid; its TDS can no longer change")
    frozen = new_amounts(invoice, wanted)  # from what the invoice is now, not from what was shown earlier
    if frozen.collectible_total < invoice.amount_paid:
        raise ValidationFailure(
            f"More was paid ({format_money(invoice.amount_paid)}) than the new amount to collect "
            f"({format_money(frozen.collectible_total)}); this needs a credit note"
        )
    version = await _apply(db, invoice, frozen, user.id, wanted)
    record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
    await unit_of_work.commit(db)
    return TdsReviewDecision(review_id=item.id, status="Approved", invoice_id=invoice.id, version=version)


async def reject(db: AsyncSession, user: User, review_id: uuid.UUID, body: TdsDecide) -> TdsReviewDecision:
    item = await _lock_decidable(db, user, review_id)
    record_decision(db, item, user, PendingChangeStatus.REJECTED, body.reason)
    await unit_of_work.commit(db)
    return TdsReviewDecision(review_id=item.id, status="Rejected", invoice_id=item.invoice_id)
