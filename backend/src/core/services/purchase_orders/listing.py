"""Read side for created purchase orders: per-line ordered / invoiced / remaining, linked invoices."""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from data.models.postgres import PurchaseOrder, PurchaseOrderLineItem
from data.repositories.customers import customers_repo
from data.repositories.invoices import documents_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.purchase_orders.listing import (
    Fulfillment,
    POLineOut,
    POLinkedInvoice,
    PODetail,
    POListItem,
)

ZERO = Decimal("0")


def fulfillment_of(
    lines: list[PurchaseOrderLineItem], invoiced: dict[uuid.UUID, Decimal]
) -> Fulfillment:
    """From the ledger, line by line. Lines with no ordered quantity cannot be judged and are skipped."""
    judged = [(line.quantity, invoiced.get(line.id, ZERO)) for line in lines if line.quantity is not None]
    if not any(done for _, done in judged):
        return "Not invoiced"
    if any(done > ordered for ordered, done in judged):
        return "Over-invoiced"
    if all(done >= ordered for ordered, done in judged):
        return "Fully invoiced"
    return "Partly invoiced"


def _item(
    po: PurchaseOrder,
    customer_name: str,
    lines: list[PurchaseOrderLineItem],
    invoiced: dict[uuid.UUID, Decimal],
    counts: tuple[int, int],
) -> dict:
    return dict(
        purchase_order_id=po.id,
        po_number=po.po_number,
        customer_id=po.customer_id,
        customer_name=customer_name,
        po_date=po.po_date,
        currency=(po.currency or "").strip() or None,
        total_amount=po.total_amount,
        line_count=len(lines),
        fulfillment=fulfillment_of(lines, invoiced),
        linked_invoices=counts[0],
        invoices_on_hold=counts[1],
        source_document_id=po.source_document_id,
        created_at=po.created_at,
    )


async def list_purchase_orders(
    db: AsyncSession, *, customer_id: uuid.UUID | None, search: str | None, limit: int, offset: int
) -> list[POListItem]:
    rows = await po_repo.list_purchase_orders(
        db, customer_id=customer_id, search=search, limit=limit, offset=offset
    )
    ids = [po.id for po, _ in rows]
    if not ids:
        return []
    lines = await po_repo.lines_for_orders(db, ids)
    invoiced = await po_repo.invoiced_by_line(db, ids)
    counts = await po_repo.invoice_counts(db, ids)
    return [
        POListItem(**_item(po, name, lines[po.id], invoiced, counts.get(po.id, (0, 0))))
        for po, name in rows
    ]


async def get_purchase_order(db: AsyncSession, po_id: uuid.UUID) -> PODetail:
    po = await po_repo.get_purchase_order(db, po_id)
    if po is None:
        raise NotFoundError("Purchase order not found")
    customer = await customers_repo.get_by_id(db, po.customer_id)
    lines = (await po_repo.lines_for_orders(db, [po.id]))[po.id]
    invoiced = await po_repo.invoiced_by_line(db, [po.id])
    linked = await po_repo.linked_invoices(db, po.id)
    consumed = await po_repo.consumed_by_invoice(db, po.id)
    held = sum(1 for invoice, _ in linked if invoice.po_hold)
    document = await documents_repo.get_document(db, po.source_document_id) if po.source_document_id else None
    return PODetail(
        **_item(po, customer.customer_name if customer else "", lines, invoiced, (len(linked), held)),
        source_file_name=document.file_name if document else None,
        lines=[
            POLineOut(
                line_id=line.id,
                description=line.description,
                quantity_ordered=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total,
                quantity_invoiced=invoiced.get(line.id, ZERO),
                quantity_remaining=(
                    line.quantity - invoiced.get(line.id, ZERO) if line.quantity is not None else None
                ),
            )
            for line in lines
        ],
        invoices=[
            POLinkedInvoice(
                invoice_id=invoice.id,
                invoice_number=invoice.invoice_number,
                invoice_status=invoice.status.value,
                invoice_date=invoice.invoice_date,
                amount_inr=invoice.amount_inr,
                po_hold=invoice.po_hold,
                match_direction=match.match_direction,
                quantity_counted=consumed.get(invoice.id, ZERO),
            )
            for invoice, match in linked
        ],
    )
