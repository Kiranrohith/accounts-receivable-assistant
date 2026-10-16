import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
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
    lines = [PurchaseOrderLineItem(**row) for row in rows]
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
