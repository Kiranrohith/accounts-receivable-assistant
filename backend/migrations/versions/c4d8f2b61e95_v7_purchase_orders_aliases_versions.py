"""v7: purchase orders, customer aliases, invoice versions, doc_kind, po_hold

New tables: customer_aliases, purchase_orders, purchase_order_line_items,
po_invoice_matches, po_fulfillments, invoice_versions.
New columns: source_documents.doc_kind, invoices.po_hold, invoices.generated_document_id.
New enum values: pending_change_reason.po_mismatch, uploaded_via.generated.

Postgres cannot drop an enum value, so downgrade leaves the two added values in place
(harmless: nothing uses them once the new columns and tables are gone).

Revision ID: c4d8f2b61e95
Revises: a7c3e91d4b20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8f2b61e95"
down_revision: str | None = "a7c3e91d4b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def _audit() -> list[sa.Column]:
    return [
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    ]


def _created() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    ]


def upgrade() -> None:
    op.execute("ALTER TYPE pending_change_reason ADD VALUE IF NOT EXISTS 'po_mismatch'")
    op.execute("ALTER TYPE uploaded_via ADD VALUE IF NOT EXISTS 'generated'")

    # --- doc_kind on source_documents (existing rows are invoices) ---
    postgresql.ENUM("invoice", "po", name="doc_kind").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "source_documents",
        sa.Column(
            "doc_kind",
            postgresql.ENUM("invoice", "po", name="doc_kind", create_type=False),
            server_default="invoice",
            nullable=False,
            comment="which pipeline reads this document: invoice or purchase order",
        ),
    )

    # --- invoices ---
    op.add_column(
        "invoices",
        sa.Column("po_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False,
                  comment="true while a po_mismatch review is pending (mirrors is_disputed)"),
    )
    op.add_column(
        "invoices",
        sa.Column("generated_document_id", sa.UUID(), nullable=True,
                  comment="DEBIT_NOTE only: system-rendered PDF for its reminder emails"),
    )
    op.create_foreign_key(
        "fk_invoices_generated_document", "invoices", "source_documents",
        ["generated_document_id"], ["id"],
    )
    op.alter_column(
        "invoices", "po_number",
        existing_type=sa.String(), existing_nullable=True,
        comment="matching key for the purchase-order link; invoices without a PO are valid",
    )

    # --- customer_aliases ---
    op.create_table(
        "customer_aliases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("alias_type", sa.Enum("name", "email", name="alias_type"), nullable=False),
        sa.Column("alias_value", sa.String(), nullable=False),
        sa.Column("source", sa.Enum("human_review", "seed", name="alias_source"), nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "customer_id", "alias_type", "alias_value", name="uq_customer_aliases_per_customer"
        ),
    )

    # --- purchase orders ---
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("po_number", sa.String(), nullable=False,
                  comment="the customer's numbering: unique per customer"),
        sa.Column("po_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "po_number", name="uq_purchase_orders_customer_number"),
    )
    op.create_table(
        "purchase_order_line_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("po_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("normalized_description", sa.String(), nullable=True,
                  comment="casefolded, punctuation stripped: the fulfilment-check comparison key"),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=18, scale=2), nullable=True),
        *_created(),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_order_line_items_po", "purchase_order_line_items", ["po_id"], unique=False
    )
    op.create_table(
        "po_invoice_matches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("po_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("match_direction", sa.String(), nullable=True,
                  comment="po_first / invoice_first / manual"),
        *_created(),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id"),
    )
    op.create_index("ix_po_invoice_matches_po", "po_invoice_matches", ["po_id"], unique=False)
    op.create_table(
        "po_fulfillments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("po_line_item_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("invoice_line_item_id", sa.UUID(), nullable=True),
        sa.Column("quantity_invoiced", sa.Numeric(precision=18, scale=4), nullable=False),
        *_created(),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["invoice_line_item_id"], ["invoice_line_items.id"]),
        sa.ForeignKeyConstraint(["po_line_item_id"], ["purchase_order_line_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_po_fulfillments_po_line", "po_fulfillments", ["po_line_item_id"], unique=False
    )

    # --- invoice_versions ---
    op.create_table(
        "invoice_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True,
                  comment="NULL for TDS-recompute versions"),
        *_created(),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "version_no", name="uq_invoice_versions_no"),
    )


def downgrade() -> None:
    op.drop_table("invoice_versions")
    op.drop_index("ix_po_fulfillments_po_line", table_name="po_fulfillments")
    op.drop_table("po_fulfillments")
    op.drop_index("ix_po_invoice_matches_po", table_name="po_invoice_matches")
    op.drop_table("po_invoice_matches")
    op.drop_index("ix_purchase_order_line_items_po", table_name="purchase_order_line_items")
    op.drop_table("purchase_order_line_items")
    op.drop_table("purchase_orders")
    op.drop_table("customer_aliases")
    op.alter_column(
        "invoices", "po_number", existing_type=sa.String(), existing_nullable=True, comment=None
    )
    op.drop_constraint("fk_invoices_generated_document", "invoices", type_="foreignkey")
    op.drop_column("invoices", "generated_document_id")
    op.drop_column("invoices", "po_hold")
    op.drop_column("source_documents", "doc_kind")
    for enum_name in ("alias_source", "alias_type", "doc_kind"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
