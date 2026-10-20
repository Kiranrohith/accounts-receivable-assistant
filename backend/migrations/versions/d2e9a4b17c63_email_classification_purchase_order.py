"""email_classification += purchase_order (decision 60: POs also arrive by email)

The only schema change of that design round. Postgres cannot drop an enum value, so the
downgrade leaves it in place (harmless: nothing uses it once the mail module is gone).

Revision ID: d2e9a4b17c63
Revises: c4d8f2b61e95
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2e9a4b17c63"
down_revision: str | None = "c4d8f2b61e95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE email_classification ADD VALUE IF NOT EXISTS 'purchase_order'")


def downgrade() -> None:
    # an enum value cannot be removed in Postgres; see the module docstring
    pass
