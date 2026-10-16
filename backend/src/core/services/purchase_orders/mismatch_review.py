"""Review of an invoice held for not matching its purchase order (reason po_mismatch).

Exit set (by design, deliberately small):
- override: approve as-is. Needs payments.po_override and a reason. The consumed quantities
  are written to the ledger even if that drives a PO line negative (shown, never hidden).
- re-check: the reviewer supplies corrected invoice lines (a new line-item version); a pass
  releases the hold, a fail leaves it Pending with the new arithmetic.
There is no reject: an invoice that already exists cannot be un-created by a review, and
"stay Open with the hold cleared" would just be an override without its gates.
po_hold therefore releases only through an override or a passing re-check.

Any user with invoices.review can see the list; the decisions run under a row lock on the
review item so two simultaneous decisions give one success and one 409.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError
from core.services.invoices.review import record_decision
from core.services.purchase_orders.fulfillment import (
    build_snapshot,
    check_against_po,
    write_ledger,
)
from data.models.postgres import Invoice, PendingInvoiceChange, User
from data.models.postgres.enums import PendingChangeReason, PendingChangeStatus, TriggerType
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.purchase_orders.review import (
    MismatchDecision,
    MismatchDetail,
    MismatchLine,
    MismatchSummary,
    OverrideBody,
    RecheckBody,
)


def _is_mismatch(item: PendingInvoiceChange | None) -> PendingInvoiceChange:
    if item is None or item.reason is not PendingChangeReason.PO_MISMATCH or item.invoice_id is None:
        raise NotFoundError("Review item not found")
    return item


def _summary(item: PendingInvoiceChange, invoice: Invoice | None) -> MismatchSummary:
    snapshot = item.proposed_snapshot or {}
    return MismatchSummary(
        review_id=item.id,
        invoice_id=item.invoice_id,
        invoice_number=invoice.invoice_number if invoice else None,
        customer_id=invoice.customer_id if invoice else None,
        po_number=snapshot.get("po_number"),
        status=item.status.value,
        reasons=snapshot.get("reasons", []),
        created_at=item.created_at,
    )


def _lines(snapshot: dict) -> list[MismatchLine]:
    return [MismatchLine.model_validate(line) for line in snapshot.get("lines", [])]


async def list_mismatch_reviews(
    db: AsyncSession, status: PendingChangeStatus = PendingChangeStatus.PENDING
) -> list[MismatchSummary]:
    items = await invoices_repo.list_pending_by_reason(db, PendingChangeReason.PO_MISMATCH, status)
    out = []
    for item in items:
        out.append(_summary(item, await invoices_repo.get_invoice(db, item.invoice_id)))
    return out


async def get_mismatch_review(db: AsyncSession, review_id: uuid.UUID) -> MismatchDetail:
    item = _is_mismatch(await invoices_repo.get_pending(db, review_id))
    invoice = await invoices_repo.get_invoice(db, item.invoice_id)
    snapshot = item.proposed_snapshot or {}
    return MismatchDetail(
        **_summary(item, invoice).model_dump(),
        lines=_lines(snapshot),
        po_hold=bool(invoice and invoice.po_hold),
        decision_reason=item.decision_reason,
        decided_at=item.decided_at,
    )


async def _lock_decidable(db: AsyncSession, review_id: uuid.UUID) -> PendingInvoiceChange:
    item = _is_mismatch(await invoices_repo.get_pending_for_update(db, review_id))
    if item.status is not PendingChangeStatus.PENDING:
        raise ConflictError(f"This review was already {item.status.value.lower()}")
    return item


async def _locked_po_and_invoice(db: AsyncSession, item: PendingInvoiceChange):
    invoice = await invoices_repo.get_invoice(db, item.invoice_id)
    match = await po_repo.get_match(db, invoice.id)
    if match is None:  # cannot happen: the hold is only ever raised on a linked invoice
        raise ConflictError("This invoice is not linked to a purchase order any more")
    po = await po_repo.lock_purchase_order(db, match.po_id)
    return po, invoice


def _release(db: AsyncSession, invoice: Invoice, po_id: uuid.UUID, user: User, how: str) -> None:
    po_repo.set_po_hold(invoice, False)
    now = datetime.now(UTC)
    logs_repo.add_state_log(
        db, entity_type="invoice", entity_id=invoice.id, from_state=invoice.status.value,
        to_state=invoice.status.value, reason=f"PO hold released: {how}",
        triggered_by_type=TriggerType.USER, triggered_by_id=user.id, occurred_at=now,
    )
    logs_repo.add_state_log(
        db, entity_type="purchase_order", entity_id=po_id, from_state=None,
        to_state="InvoiceAccepted", reason=f"Invoice {invoice.invoice_number}: {how}",
        triggered_by_type=TriggerType.USER, triggered_by_id=user.id, occurred_at=now,
    )


async def override(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: OverrideBody
) -> MismatchDecision:
    item = await _lock_decidable(db, review_id)
    po, invoice = await _locked_po_and_invoice(db, item)

    checks, reasons = await check_against_po(db, po, invoice)
    write_ledger(db, invoice, checks, user.id)  # even into negative remaining; never hidden
    snapshot = build_snapshot(po, checks, reasons)
    item.diff = {"decision": "override", "comparison": snapshot}
    _release(db, invoice, po.id, user, f"approved as-is by override ({body.reason})")
    record_decision(db, item, user, PendingChangeStatus.APPROVED, body.reason)
    await unit_of_work.commit(db)
    return MismatchDecision(
        review_id=item.id, status="Approved", invoice_id=invoice.id, po_hold=False,
        reasons=reasons, lines=_lines(snapshot),
    )


async def recheck(
    db: AsyncSession, user: User, review_id: uuid.UUID, body: RecheckBody
) -> MismatchDecision:
    item = await _lock_decidable(db, review_id)
    po, invoice = await _locked_po_and_invoice(db, item)

    version = invoice.line_items_version + 1  # corrected lines are a new version, old kept
    await invoices_repo.insert_line_items(
        db,
        [
            {
                "invoice_id": invoice.id,
                "version_no": version,
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "line_total": line.line_total,
                "tax_rate": line.tax_rate,
                "created_by": user.id,
            }
            for line in body.lines
        ],
    )
    invoice.line_items_version = version

    checks, reasons = await check_against_po(db, po, invoice)
    snapshot = build_snapshot(po, checks, reasons)
    now = datetime.now(UTC)
    logs_repo.add_state_log(
        db, entity_type="invoice", entity_id=invoice.id, from_state=invoice.status.value,
        to_state=invoice.status.value,
        reason=f"Invoice lines corrected (version {version}) and re-checked against {po.po_number}",
        triggered_by_id=user.id, occurred_at=now,
    )

    if reasons:  # still wrong: stays Pending with the new arithmetic
        item.proposed_snapshot = snapshot
        await unit_of_work.commit(db)
        return MismatchDecision(
            review_id=item.id, status="Pending", invoice_id=invoice.id, po_hold=True,
            reasons=reasons, lines=_lines(snapshot),
        )

    write_ledger(db, invoice, checks, user.id)
    item.diff = {"decision": "recheck", "line_items_version": version, "comparison": snapshot}
    note = body.note or "Corrected the invoice lines; they now match the purchase order"
    _release(db, invoice, po.id, user, f"corrected lines (version {version}) match the order")
    record_decision(db, item, user, PendingChangeStatus.APPROVED, note)
    await unit_of_work.commit(db)
    return MismatchDecision(
        review_id=item.id, status="Approved", invoice_id=invoice.id, po_hold=False,
        lines=_lines(snapshot),
    )

