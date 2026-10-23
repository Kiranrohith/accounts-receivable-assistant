import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import (
    Customer,
    Invoice,
    InvoiceLineItem,
    PoFulfillment,
    PoInvoiceMatch,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from data.repositories import unit_of_work
from data.repositories.invoices import invoices_repo


async def get_purchase_order(db: AsyncSession, po_id: uuid.UUID) -> PurchaseOrder | None:
    return await db.get(PurchaseOrder, po_id)


async def get_by_customer_number(
    db: AsyncSession, customer_id: uuid.UUID, po_number: str
) -> PurchaseOrder | None:
    return await db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.customer_id == customer_id, PurchaseOrder.po_number == po_number
        )
    )


async def owner_of_number_elsewhere(
    db: AsyncSession, customer_id: uuid.UUID, po_number: str
) -> str | None:
    """Name of another customer that owns a PO with this number, if any."""
    return await db.scalar(
        select(Customer.customer_name)
        .join(PurchaseOrder, PurchaseOrder.customer_id == Customer.id)
        .where(PurchaseOrder.po_number == po_number, PurchaseOrder.customer_id != customer_id)
        .limit(1)
    )


async def insert_purchase_order(db: AsyncSession, **fields: Any) -> PurchaseOrder:
    po = PurchaseOrder(**fields)
    db.add(po)
    await unit_of_work.flush(db)
    return po


async def insert_po_lines(db: AsyncSession, rows: list[dict[str, Any]]) -> list[PurchaseOrderLineItem]:
    lines = [PurchaseOrderLineItem(**row) for row in invoices_repo.stamp_in_order(rows)]
    db.add_all(lines)
    await unit_of_work.flush(db)
    return lines


async def lock_purchase_order(db: AsyncSession, po_id: uuid.UUID) -> PurchaseOrder:
    """Row-lock the PO: linking and the cumulative-quantity check run one at a time per PO."""
    po = await db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update()
    )
    assert po is not None
    return po


async def po_lines(db: AsyncSession, po_id: uuid.UUID) -> list[PurchaseOrderLineItem]:
    result = await db.scalars(
        select(PurchaseOrderLineItem)
        .where(PurchaseOrderLineItem.po_id == po_id)
        .order_by(PurchaseOrderLineItem.created_at, PurchaseOrderLineItem.id)
    )
    return list(result)


async def invoiced_totals(db: AsyncSession, po_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Quantity already consumed per PO line, summed over the append-only ledger."""
    rows = await db.execute(
        select(PoFulfillment.po_line_item_id, func.sum(PoFulfillment.quantity_invoiced))
        .join(PurchaseOrderLineItem, PurchaseOrderLineItem.id == PoFulfillment.po_line_item_id)
        .where(PurchaseOrderLineItem.po_id == po_id)
        .group_by(PoFulfillment.po_line_item_id)
    )
    return {line_id: total for line_id, total in rows}


async def get_match(db: AsyncSession, invoice_id: uuid.UUID) -> PoInvoiceMatch | None:
    return await db.scalar(select(PoInvoiceMatch).where(PoInvoiceMatch.invoice_id == invoice_id))


async def insert_match(db: AsyncSession, **fields: Any) -> PoInvoiceMatch:
    match = PoInvoiceMatch(**fields)
    db.add(match)
    await unit_of_work.flush(db)
    return match


def add_fulfillments(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    db.add_all(PoFulfillment(**row) for row in rows)


async def unlinked_invoices(
    db: AsyncSession, customer_id: uuid.UUID, po_number: str
) -> list[Invoice]:
    """Invoices of this customer quoting this PO number that are not linked to any PO yet."""
    linked = select(PoInvoiceMatch.invoice_id)
    result = await db.scalars(
        select(Invoice)
        .where(
            Invoice.customer_id == customer_id,
            Invoice.po_number == po_number,
            Invoice.id.not_in(linked),
        )
        .order_by(Invoice.created_at, Invoice.id)
    )
    return list(result)


async def invoice_lines(
    db: AsyncSession, invoice_id: uuid.UUID, version_no: int
) -> list[InvoiceLineItem]:
    result = await db.scalars(
        select(InvoiceLineItem)
        .where(InvoiceLineItem.invoice_id == invoice_id, InvoiceLineItem.version_no == version_no)
        .order_by(InvoiceLineItem.created_at, InvoiceLineItem.id)
    )
    return list(result)


def set_po_hold(invoice: Invoice, hold: bool) -> None:
    invoice.po_hold = hold


async def purchase_orders_by_document(
    db: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PurchaseOrder]:
    result = await db.scalars(
        select(PurchaseOrder).where(PurchaseOrder.source_document_id.in_(document_ids))
    )
    return {po.source_document_id: po for po in result}


# ---------------------------------------------------------------- read side (lists)
async def list_purchase_orders(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    search: str | None,
    limit: int,
    offset: int,
) -> list[tuple[PurchaseOrder, str]]:
    query = select(PurchaseOrder, Customer.customer_name).join(
        Customer, Customer.id == PurchaseOrder.customer_id
    )
    if customer_id is not None:
        query = query.where(PurchaseOrder.customer_id == customer_id)
    if search:
        escaped = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        query = query.where(PurchaseOrder.po_number.ilike(f"%{escaped}%", escape="!"))
    rows = await db.execute(
        query.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id).limit(limit).offset(offset)
    )
    return [(po, name) for po, name in rows]


_SORTS = {
    "created": PurchaseOrder.created_at,
    "po_number": PurchaseOrder.po_number,
    "customer": Customer.customer_name,
    "po_date": PurchaseOrder.po_date,
    "amount": PurchaseOrder.total_amount,
}


def _fulfillment_case():
    """The same judgement as `fulfillment_of`, in SQL: per line what was ordered against what the ledger consumed."""
    done = (
        select(
            PurchaseOrderLineItem.po_id.label("po_id"),
            PurchaseOrderLineItem.quantity.label("ordered"),
            func.coalesce(func.sum(PoFulfillment.quantity_invoiced), 0).label("done"),
        )
        .outerjoin(PoFulfillment, PoFulfillment.po_line_item_id == PurchaseOrderLineItem.id)
        .where(PurchaseOrderLineItem.quantity.is_not(None))
        .group_by(PurchaseOrderLineItem.id, PurchaseOrderLineItem.po_id, PurchaseOrderLineItem.quantity)
        .subquery()
    )
    per_po = (
        select(
            done.c.po_id,
            func.bool_or(done.c.done > 0).label("any_done"),
            func.bool_or(done.c.done > done.c.ordered).label("any_over"),
            func.bool_and(done.c.done >= done.c.ordered).label("all_full"),
        )
        .group_by(done.c.po_id)
        .subquery()
    )
    verdict = case(
        (func.coalesce(per_po.c.any_done, False).is_(False), "Not invoiced"),
        (per_po.c.any_over.is_(True), "Over-invoiced"),
        (per_po.c.all_full.is_(True), "Fully invoiced"),
        else_="Partly invoiced",
    )
    return per_po, verdict


async def search_purchase_orders(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    fulfillment: str | None,
    on_hold: bool | None,
    search: str | None,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> tuple[list[tuple[PurchaseOrder, str]], int]:
    """(rows, total) for the purchase orders page: filtered, searched, sorted and cut to one page by the database."""
    where = []
    if customer_id is not None:
        where.append(PurchaseOrder.customer_id == customer_id)
    if search and search.strip():
        # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
        escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        where.append(
            or_(
                PurchaseOrder.po_number.ilike(like, escape="\\"),
                Customer.customer_name.ilike(like, escape="\\"),
            )
        )
    if on_hold is not None:
        held = exists().where(
            PoInvoiceMatch.po_id == PurchaseOrder.id,
            PoInvoiceMatch.invoice_id == Invoice.id,
            Invoice.po_hold.is_(True),
        )
        where.append(held if on_hold else ~held)

    base = select(PurchaseOrder, Customer.customer_name).join(Customer, Customer.id == PurchaseOrder.customer_id)
    if fulfillment is not None:
        per_po, verdict = _fulfillment_case()
        base = base.outerjoin(per_po, per_po.c.po_id == PurchaseOrder.id)
        where.append(verdict == fulfillment)
    base = base.where(*where)

    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    column = _SORTS.get(sort, PurchaseOrder.created_at)
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    rows = await db.execute(base.order_by(ordered, PurchaseOrder.id).limit(limit).offset(offset))
    return [(po, name) for po, name in rows], total or 0


async def lines_for_orders(
    db: AsyncSession, po_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PurchaseOrderLineItem]]:
    result = await db.scalars(
        select(PurchaseOrderLineItem)
        .where(PurchaseOrderLineItem.po_id.in_(po_ids))
        .order_by(PurchaseOrderLineItem.created_at, PurchaseOrderLineItem.id)
    )
    grouped: dict[uuid.UUID, list[PurchaseOrderLineItem]] = {i: [] for i in po_ids}
    for line in result:
        grouped[line.po_id].append(line)
    return grouped


async def invoiced_by_line(db: AsyncSession, po_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """Quantity consumed per PO line (ledger sum) for several POs at once."""
    rows = await db.execute(
        select(PoFulfillment.po_line_item_id, func.sum(PoFulfillment.quantity_invoiced))
        .join(PurchaseOrderLineItem, PurchaseOrderLineItem.id == PoFulfillment.po_line_item_id)
        .where(PurchaseOrderLineItem.po_id.in_(po_ids))
        .group_by(PoFulfillment.po_line_item_id)
    )
    return {line_id: total for line_id, total in rows}


async def invoice_counts(
    db: AsyncSession, po_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """(linked invoices, of which on PO hold) per PO."""
    rows = await db.execute(
        select(
            PoInvoiceMatch.po_id,
            func.count(),
            func.count().filter(Invoice.po_hold.is_(True)),
        )
        .join(Invoice, Invoice.id == PoInvoiceMatch.invoice_id)
        .where(PoInvoiceMatch.po_id.in_(po_ids))
        .group_by(PoInvoiceMatch.po_id)
    )
    return {po_id: (linked, held) for po_id, linked, held in rows}


async def linked_invoices(
    db: AsyncSession, po_id: uuid.UUID
) -> list[tuple[Invoice, PoInvoiceMatch]]:
    rows = await db.execute(
        select(Invoice, PoInvoiceMatch)
        .join(PoInvoiceMatch, PoInvoiceMatch.invoice_id == Invoice.id)
        .where(PoInvoiceMatch.po_id == po_id)
        .order_by(PoInvoiceMatch.created_at, Invoice.id)
    )
    return [(invoice, match) for invoice, match in rows]


async def consumed_by_invoice(db: AsyncSession, po_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Quantity each linked invoice has consumed on this PO (0 or absent while on hold)."""
    rows = await db.execute(
        select(PoFulfillment.invoice_id, func.sum(PoFulfillment.quantity_invoiced))
        .join(PurchaseOrderLineItem, PurchaseOrderLineItem.id == PoFulfillment.po_line_item_id)
        .where(PurchaseOrderLineItem.po_id == po_id)
        .group_by(PoFulfillment.invoice_id)
    )
    return {invoice_id: total for invoice_id, total in rows}


async def consumed_by_line_for_invoice(
    db: AsyncSession, invoice_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """Net quantity this invoice has consumed on each PO line (ledger sum)."""
    rows = await db.execute(
        select(PoFulfillment.po_line_item_id, func.sum(PoFulfillment.quantity_invoiced))
        .where(PoFulfillment.invoice_id == invoice_id)
        .group_by(PoFulfillment.po_line_item_id)
    )
    return {line_id: total for line_id, total in rows}
