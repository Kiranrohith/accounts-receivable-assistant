"""customers.gstin becomes tax_id, with tax_id_type

Decision 71: a customer's tax identity is not only a GSTIN. The column is renamed (its values, its not-null and its
unique key are untouched: every existing value is already upper case with no separators, which is the normalized form),
and tax_id_type says what kind of number it is. Every customer that has a value is a GSTIN.

Revision ID: d8b2f6a14c97
Revises: c4a7e19d2b63
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8b2f6a14c97"
down_revision: str | None = "c4a7e19d2b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPE_VALUES = ("GSTIN", "VAT", "TRN", "EIN", "OTHER")
COMMENT = (
    "the customer's registration number, stored normalized (upper case, no spaces, dots, hyphens or slashes): "
    "document -> customer deterministic key"
)


def upgrade() -> None:
    tax_id_type = postgresql.ENUM(*TYPE_VALUES, name="tax_id_type", create_type=False)
    tax_id_type.create(op.get_bind(), checkfirst=True)
    op.alter_column("customers", "gstin", new_column_name="tax_id", comment=COMMENT)
    op.execute("ALTER TABLE customers RENAME CONSTRAINT customers_gstin_key TO customers_tax_id_key")
    op.add_column(
        "customers",
        sa.Column("tax_id_type", tax_id_type, nullable=True, comment="what kind of number tax_id is (GSTIN, VAT, TRN, EIN, OTHER)"),
    )
    op.execute("UPDATE customers SET tax_id_type = 'GSTIN' WHERE tax_id IS NOT NULL AND btrim(tax_id) <> ''")


def downgrade() -> None:
    # a tax ID that is not a GSTIN has no place in the old column: refuse rather than lose or mislabel it
    foreign = op.get_bind().execute(
        sa.text("select customer_name from customers where tax_id_type is not null and tax_id_type <> 'GSTIN'")
    ).all()
    if foreign:
        raise RuntimeError(
            "Cannot go back to a GSTIN-only column: these customers hold another kind of tax ID: "
            + ", ".join(row.customer_name for row in foreign)
        )
    op.drop_column("customers", "tax_id_type")
    op.execute("ALTER TABLE customers RENAME CONSTRAINT customers_tax_id_key TO customers_gstin_key")
    op.alter_column(
        "customers", "tax_id", new_column_name="gstin", comment="invoice -> customer deterministic key"
    )
    postgresql.ENUM(name="tax_id_type").drop(op.get_bind(), checkfirst=True)
