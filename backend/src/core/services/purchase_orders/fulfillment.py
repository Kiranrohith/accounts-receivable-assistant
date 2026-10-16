"""Linking invoices to purchase orders and checking what they billed against what was ordered.

The check, per invoice line: the normalised description must exist among the PO's lines AND
quantity + everything already accepted for that PO line must stay within the ordered
quantity. Cumulative across every invoice linked to the PO (10 + 10 + 10 against an order of
10 must fail on the second invoice).

PASS: append the consumed quantities to po_fulfillments; the invoice stays as it is.
FAIL: the invoice gets po_hold and a pending_invoice_changes row (reason po_mismatch) holding
      the comparison; nothing is written to the ledger.

Everything runs under a row lock on the PO, so two invoices cannot both pass against the
same remaining quantity. Callers commit.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.purchase_orders.normalize import normalize_description
from data.models.postgres import Invoice, PurchaseOrder
from data.models.postgres.enums import PendingChangeReason
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo

NO_LINES_REASON = "The invoice has no line items, so it cannot be checked against the order"


@dataclass(frozen=True)
class OrderedLine:
    id: uuid.UUID
    normalized: str
    ordered: Decimal | None


@dataclass(frozen=True)
class BilledLine:
    id: uuid.UUID
    description: str
    quantity: Decimal | None


@dataclass(frozen=True)
class LineCheck:
    invoice_line_item_id: uuid.UUID
    description: str
    po_line_item_id: uuid.UUID | None
    ordered: Decimal | None
    already_invoiced: Decimal
    this_invoice: Decimal | None
    remaining_after: Decimal | None
    ok: bool
    reason: str | None = None


def evaluate_lines(
    po_lines: list[OrderedLine],
    invoiced_so_far: dict[uuid.UUID, Decimal],
    billed: list[BilledLine],
) -> list[LineCheck]:
    """Pure: compare invoice lines with the PO. Lines sharing a description add up."""
    by_key: dict[str, OrderedLine] = {}
    for line in po_lines:
        by_key.setdefault(line.normalized, line)  # first PO line wins on duplicate wording
    running: dict[uuid.UUID, Decimal] = {}
    checks: list[LineCheck] = []

    for item in billed:
        target = by_key.get(normalize_description(item.description))
        if target is None:
            checks.append(LineCheck(item.id, item.description, None, None, Decimal(0),
                                    item.quantity, None, False,
                                    "This item is not on the purchase order"))
            continue
        before = invoiced_so_far.get(target.id, Decimal(0)) + running.get(target.id, Decimal(0))
        if item.quantity is None or item.quantity <= 0:
            checks.append(LineCheck(item.id, item.description, target.id, target.ordered, before,
                                    item.quantity, None, False,
                                    "The invoice line has no positive quantity"))
            continue
        if target.ordered is None:
            checks.append(LineCheck(item.id, item.description, target.id, None, before,
                                    item.quantity, None, False,
                                    "The purchase order has no ordered quantity for this item"))
            continue
        running[target.id] = running.get(target.id, Decimal(0)) + item.quantity
        remaining = target.ordered - before - item.quantity
        ok = remaining >= 0
        checks.append(LineCheck(
            item.id, item.description, target.id, target.ordered, before, item.quantity,
            remaining, ok,
            None if ok else (
                f"Ordered {target.ordered}, already invoiced {before}, this invoice "
                f"{item.quantity}: {-remaining} over the ordered quantity"
            ),
        ))
    return checks


@dataclass
class LinkResult:
    passed: bool
    checks: list[LineCheck] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    pending_change_id: uuid.UUID | None = None


def build_snapshot(po: PurchaseOrder, checks: list[LineCheck], reasons: list[str]) -> dict:
    return {
        "po_id": str(po.id),
        "po_number": po.po_number,
        "reasons": reasons,
        "lines": [
            {k: (str(v) if isinstance(v, (uuid.UUID, Decimal)) else v) for k, v in asdict(c).items()}
            for c in checks
        ],
    }


async def check_against_po(db: AsyncSession, po: PurchaseOrder, invoice: Invoice):
    """Run the check with the PO already locked; returns (checks, reasons)."""
    lines = await po_repo.po_lines(db, po.id)
    billed = await po_repo.invoice_lines(db, invoice.id, invoice.line_items_version)
    if not billed:
        return [], [NO_LINES_REASON]
    checks = evaluate_lines(
        [OrderedLine(l.id, l.normalized_description or "", l.quantity) for l in lines],
        await po_repo.invoiced_totals(db, po.id),
        [BilledLine(b.id, b.description, b.quantity) for b in billed],
    )
    return checks, [f"'{c.description}': {c.reason}" for c in checks if not c.ok]


def write_ledger(db: AsyncSession, invoice: Invoice, checks: list[LineCheck], actor: uuid.UUID | None) -> None:
    po_repo.add_fulfillments(
        db,
        [
            {
                "po_line_item_id": c.po_line_item_id,
                "invoice_id": invoice.id,
                "invoice_line_item_id": c.invoice_line_item_id,
                "quantity_invoiced": c.this_invoice,
                "created_by": actor,
            }
            for c in checks
            if c.po_line_item_id is not None and c.this_invoice is not None and c.this_invoice > 0
        ],
    )


async def link_and_check(
    db: AsyncSession,
    po: PurchaseOrder,
    invoice: Invoice,
    direction: str,
    actor: uuid.UUID | None,
) -> LinkResult | None:
    """Link the invoice to the PO (once) and run the check. None if it was already linked."""
    await po_repo.lock_purchase_order(db, po.id)
    if await po_repo.get_match(db, invoice.id) is not None:
        return None
    await po_repo.insert_match(
        db, po_id=po.id, invoice_id=invoice.id, match_direction=direction, created_by=actor
    )
    now = datetime.now(UTC)
    logs_repo.add_state_log(
        db, entity_type="purchase_order", entity_id=po.id, from_state=None, to_state="InvoiceLinked",
        reason=f"Invoice {invoice.invoice_number} linked ({direction})", occurred_at=now,
    )

    checks, reasons = await check_against_po(db, po, invoice)
    if not reasons:
        write_ledger(db, invoice, checks, actor)
        return LinkResult(True, checks)

    po_repo.set_po_hold(invoice, True)
    change = await invoices_repo.insert_pending_change(
        db,
        invoice_id=invoice.id,
        source_document_id=invoice.source_document_id,
        reason=PendingChangeReason.PO_MISMATCH,
        proposed_snapshot=build_snapshot(po, checks, reasons),
        created_by=actor,
    )
    logs_repo.add_state_log(
        db, entity_type="invoice", entity_id=invoice.id, from_state=invoice.status.value,
        to_state=invoice.status.value,
        reason=f"On PO hold: does not match purchase order {po.po_number}", occurred_at=now,
    )
    return LinkResult(False, checks, reasons, change.id)


async def retro_link(
    db: AsyncSession, po: PurchaseOrder, actor: uuid.UUID | None
) -> list[tuple[Invoice, LinkResult]]:
    """A new PO arrives: link every unlinked invoice of that customer quoting its number."""
    out: list[tuple[Invoice, LinkResult]] = []
    for invoice in await po_repo.unlinked_invoices(db, po.customer_id, po.po_number):
        result = await link_and_check(db, po, invoice, "invoice_first", actor)
        if result is not None:
            out.append((invoice, result))
    return out


async def link_new_invoice(
    db: AsyncSession, invoice: Invoice, actor: uuid.UUID | None
) -> LinkResult | None:
    """PO-first direction: a new invoice quoting a PO number the customer already has an order for.

    None when there is nothing to link (no po_number, or no such PO for this customer yet; the
    link is made later if the PO arrives).
    """
    if not invoice.po_number:
        return None
    po = await po_repo.get_by_customer_number(db, invoice.customer_id, invoice.po_number)
    if po is None:
        return None
    return await link_and_check(db, po, invoice, "po_first", actor)
