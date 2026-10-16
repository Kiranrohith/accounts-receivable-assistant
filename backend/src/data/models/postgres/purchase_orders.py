"""Purchase orders, their lines, the PO-to-invoice link and the fulfilment ledger."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CHAR, Date, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, CreatedMixin, uuid_pk

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 4)


class PurchaseOrder(Base, AuditMixin):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("customer_id", "po_number", name="uq_purchase_orders_customer_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(
        String, nullable=False, comment="the customer's numbering: unique per customer"
    )
    po_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )


class PurchaseOrderLineItem(Base, CreatedMixin):
    """Append-only."""

    __tablename__ = "purchase_order_line_items"
    __table_args__ = (Index("ix_purchase_order_line_items_po", "po_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    normalized_description: Mapped[str | None] = mapped_column(
        String, comment="casefolded, punctuation stripped: the fulfilment-check comparison key"
    )
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    line_total: Mapped[Decimal | None] = mapped_column(MONEY)


class PoInvoiceMatch(Base, CreatedMixin):
    """Append-only. An invoice links to at most one PO; a PO serves many invoices."""

    __tablename__ = "po_invoice_matches"
    __table_args__ = (Index("ix_po_invoice_matches_po", "po_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), unique=True, nullable=False
    )
    match_direction: Mapped[str | None] = mapped_column(
        String, comment="po_first / invoice_first / manual"
    )


class PoFulfillment(Base, CreatedMixin):
    """Append-only ledger of which PO lines each invoice consumed.

    Remaining per PO line = ordered - sum(quantity_invoiced); computed, never stored.
    A reversal writes a negative row.
    """

    __tablename__ = "po_fulfillments"
    __table_args__ = (Index("ix_po_fulfillments_po_line", "po_line_item_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    po_line_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_order_line_items.id"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    invoice_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_line_items.id")
    )
    quantity_invoiced: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
