"""invoices.invoice_number_norm: invoice numbers compared without case, spaces or punctuation

A generated, stored column holding the upper-case letters and digits of invoice_number
("INV-1176", "INV1176" and "inv 1176" are all "INV1176"), with a unique index so the same invoice
cannot exist twice under two spellings. The rule is the same one core code applies to the numbers an
advice or a complaint quotes (data/repositories/invoices/numbers.py).

The index fails if two saved invoices already differ only by punctuation or case; the upgrade then
stops with the pair named, and nothing is changed.

Revision ID: b7e4a1c93d52
Revises: a8d3e5f0b216
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4a1c93d52"
down_revision: str | None = "a8d3e5f0b216"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPR = "upper(regexp_replace(invoice_number, '[^A-Za-z0-9]', '', 'g'))"


def upgrade() -> None:
    clash = (
        op.get_bind()
        .execute(
            sa.text(
                f"select {EXPR} as n, array_agg(invoice_number order by invoice_number) as numbers "
                "from invoices group by 1 having count(*) > 1"
            )
        )
        .all()
    )
    if clash:
        detail = "; ".join(f"{row.numbers}" for row in clash)
        raise RuntimeError(
            "Cannot add the normalized invoice number: these saved invoices differ only by case or "
            f"punctuation and must be merged or renumbered first: {detail}"
        )
    op.add_column(
        "invoices",
        sa.Column(
            "invoice_number_norm",
            sa.String(),
            sa.Computed(EXPR, persisted=True),
            nullable=False,
            comment="generated: upper-case letters and digits of invoice_number only (INV-1176 = INV1176)",
        ),
    )
    op.create_unique_constraint("uq_invoices_invoice_number_norm", "invoices", ["invoice_number_norm"])


def downgrade() -> None:
    op.drop_constraint("uq_invoices_invoice_number_norm", "invoices", type_="unique")
    op.drop_column("invoices", "invoice_number_norm")
