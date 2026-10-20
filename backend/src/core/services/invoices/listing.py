"""Read side for invoices and the rejected list."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError
from core.services.payments.listing import invoice_payments
from data.models.postgres import Invoice, RejectedInvoice, SourceDocument
from data.models.postgres.enums import InvoiceStatus, RejectionResolution
from data.repositories.customers import customers_repo
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.invoices.listing import (
    InvoiceDetail,
    InvoiceLineOut,
    InvoiceListItem,
    LinkedPO,
    RejectedItem,
)


def _item(invoice: Invoice, customer_name: str) -> dict:
    return dict(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        type=invoice.type.value,
        status=invoice.status.value,
        customer_id=invoice.customer_id,
        customer_name=customer_name,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        invoice_currency=(invoice.invoice_currency or "").strip(),
        amount_foreign=invoice.amount_foreign,
        fx_rate=invoice.fx_rate_invoice,
        amount_inr=invoice.amount_inr,
        tds_amount=invoice.tds_amount,
        collectible_total=invoice.collectible_total,
        amount_paid=invoice.amount_paid,
        collectible_balance=invoice.collectible_balance,
        is_disputed=invoice.is_disputed,
        po_hold=invoice.po_hold,
        po_number=invoice.po_number,
        version=invoice.version,
        source_document_id=invoice.source_document_id,
        created_at=invoice.created_at,
    )


async def list_invoices(
    db: AsyncSession,
    *,
    status: InvoiceStatus | None,
    customer_id: uuid.UUID | None,
    po_hold: bool | None,
    limit: int,
    offset: int,
) -> list[InvoiceListItem]:
    rows = await invoices_repo.list_invoices(
        db, status=status, customer_id=customer_id, po_hold=po_hold, limit=limit, offset=offset
    )
    return [InvoiceListItem(**_item(invoice, name)) for invoice, name in rows]


async def get_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> InvoiceDetail:
    invoice = await invoices_repo.get_invoice(db, invoice_id)
    if invoice is None:
        raise NotFoundError("Invoice not found")
    customer = await customers_repo.get_by_id(db, invoice.customer_id)
    lines = await invoices_repo.current_line_items(db, invoice)
    document = (
        await documents_repo.get_document(db, invoice.source_document_id)
        if invoice.source_document_id
        else None
    )
    match = await po_repo.get_match(db, invoice.id)
    linked = None
    if match is not None:
        order = await po_repo.get_purchase_order(db, match.po_id)
        linked = LinkedPO(
            purchase_order_id=match.po_id, po_number=order.po_number, match_direction=match.match_direction
        )
    hold = await invoices_repo.open_po_mismatch(db, invoice.id) if invoice.po_hold else None
    return InvoiceDetail(
        **_item(invoice, customer.customer_name if customer else ""),
        subtotal=invoice.subtotal,
        tax_total=invoice.tax_total,
        tax_breakdown=invoice.tax_breakdown,
        line_items=[
            InvoiceLineOut(
                description=l.description,
                quantity=l.quantity,
                unit_price=l.unit_price,
                line_total=l.line_total,
                tax_rate=l.tax_rate,
            )
            for l in lines
        ],
        source_file_name=document.file_name if document else None,
        linked_po=linked,
        po_mismatch_review_id=hold.id if hold else None,
        payments=await invoice_payments(db, invoice.id),
    )


def _rejected_item(rejected: RejectedInvoice, document: SourceDocument | None) -> RejectedItem:
    reasons = rejected.rejection_reasons or []
    codes = [r.get("code", "") for r in reasons]
    snapshot = rejected.extracted_snapshot or {}
    kind = snapshot["kind"] if snapshot.get("kind") in ("purchase_order", "other", "duplicate") else "invoice"
    file_kept = bool(document and document.file_path)
    return RejectedItem(
        rejected_id=rejected.id,
        source_document_id=rejected.source_document_id,
        duplicate_of_source_document_id=rejected.duplicate_of_source_document_id,
        kind=kind,
        file_name=(document.file_name if document else None) or rejected.attempted_file_name,
        rejected_at=rejected.created_at,
        reasons=[r.get("message", "") for r in reasons],
        reason_codes=codes,
        resolution=rejected.resolution.value,
        file_kept=file_kept,
        can_send_to_review=(
            kind == "other" and file_kept and rejected.resolution is RejectionResolution.NONE
        ),
        invoice_number=snapshot.get("invoice_number"),
        po_number=snapshot.get("po_number"),
        customer_name=snapshot.get("customer_name"),
        total_amount=snapshot.get("total_amount"),
    )


async def list_rejected(
    db: AsyncSession, *, kind: str, include_resolved: bool, limit: int, offset: int
) -> list[RejectedItem]:
    rows = await invoices_repo.list_rejected(
        db, kind=kind, include_resolved=include_resolved, limit=limit, offset=offset
    )
    return [_rejected_item(rejected, document) for rejected, document in rows]
