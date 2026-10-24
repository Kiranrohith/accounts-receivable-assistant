"""po_number_norm: purchase-order numbers compared without case, spaces or punctuation

Generated, stored columns on purchase_orders and invoices holding the upper-case letters and digits of po_number
("PO-123", "PO123" and "po 123" are all "PO123"), so an invoice finds its purchase order (and the other way round)
whichever way either wrote the number. The customer's PO numbers are unique by this form, replacing the unique
constraint on the raw spelling, and an index serves the invoice side of the lookup. Same rule as invoice_number_norm.

The new constraint fails if one customer already has two POs that differ only by punctuation or case; the upgrade then
stops with the pair named, and nothing is changed.

Revision ID: c4a7e19d2b63
Revises: b30e0dd132ae
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7e19d2b63"
down_revision: str | None = "b30e0dd132ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPR = "upper(regexp_replace(po_number, '[^A-Za-z0-9]', '', 'g'))"
PO_COMMENT = "generated: upper-case letters and digits of po_number only (PO-123 = PO 123); unique per customer"
INVOICE_COMMENT = "generated: upper-case letters and digits of po_number only (PO-123 = PO 123); NULL without a PO"


def upgrade() -> None:
    clash = (
        op.get_bind()
        .execute(
            sa.text(
                f"select customer_id, array_agg(po_number order by po_number) as numbers "
                f"from purchase_orders group by customer_id, {EXPR} having count(*) > 1"
            )
        )
        .all()
    )
    if clash:
        detail = "; ".join(f"{row.numbers}" for row in clash)
        raise RuntimeError(
            "Cannot add the normalized PO number: these purchase orders of one customer differ only by case or "
            f"punctuation and must be merged or renumbered first: {detail}"
        )
    op.add_column(
        "purchase_orders",
        sa.Column("po_number_norm", sa.String(), sa.Computed(EXPR, persisted=True), nullable=False, comment=PO_COMMENT),
    )
    op.drop_constraint("uq_purchase_orders_customer_number", "purchase_orders", type_="unique")
    op.create_unique_constraint(
        "uq_purchase_orders_customer_number_norm", "purchase_orders", ["customer_id", "po_number_norm"]
    )
    op.add_column(
        "invoices",
        sa.Column("po_number_norm", sa.String(), sa.Computed(EXPR, persisted=True), nullable=True, comment=INVOICE_COMMENT),
    )
    op.create_index("ix_invoices_customer_po_norm", "invoices", ["customer_id", "po_number_norm"])


def downgrade() -> None:
    op.drop_index("ix_invoices_customer_po_norm", table_name="invoices")
    op.drop_column("invoices", "po_number_norm")
    op.drop_constraint("uq_purchase_orders_customer_number_norm", "purchase_orders", type_="unique")
    op.create_unique_constraint("uq_purchase_orders_customer_number", "purchase_orders", ["customer_id", "po_number"])
    op.drop_column("purchase_orders", "po_number_norm")
