"""payment_allocations.spent_for_payment_id: which payment's instruction caused a credit spend

A credit-note spend made because a remittance said "use our credit" is a note-funded allocation
(it has no payment_id). Reversing that payment must also undo what its instruction spent, or a
re-match would spend the credit a second time; this column is the trace. Manual spends leave it
NULL and are never touched by a reversal.

Revision ID: f3c9a82d5e17
Revises: e5b8c13a7d94
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c9a82d5e17"
down_revision: str | None = "e5b8c13a7d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_allocations",
        sa.Column(
            "spent_for_payment_id", sa.UUID(), nullable=True,
            comment="the payment whose 'use our credit' instruction caused this note-funded spend",
        ),
    )
    op.create_foreign_key(
        "fk_payment_allocations_spent_for_payment", "payment_allocations", "payments",
        ["spent_for_payment_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_allocations_spend_trace", "payment_allocations",
        "spent_for_payment_id IS NULL OR note_id IS NOT NULL",
    )
    op.create_index(
        "ix_payment_allocations_spent_for_payment", "payment_allocations", ["spent_for_payment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payment_allocations_spent_for_payment", table_name="payment_allocations")
    op.drop_constraint("ck_allocations_spend_trace", "payment_allocations", type_="check")
    op.drop_constraint("fk_payment_allocations_spent_for_payment", "payment_allocations", type_="foreignkey")
    op.drop_column("payment_allocations", "spent_for_payment_id")
