"""customers all fields required, unique email and name

Revision ID: bf261c11dbd4
Revises: cfc261a50dc8

Fails (and rolls back) if existing rows have a NULL in one of these columns or share
an e-mail / normalised name: fix or remove those rows first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bf261c11dbd4"
down_revision: str | None = "cfc261a50dc8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED = {
    "contact": sa.String(),
    "email": sa.String(),
    "gstin": sa.String(),
    "billing_currency": sa.CHAR(3),
    "country": sa.String(),
}


def upgrade() -> None:
    for column, type_ in _REQUIRED.items():
        op.alter_column("customers", column, existing_type=type_, nullable=False)
    op.create_index(
        "uq_customers_email_lower", "customers", [sa.text("lower(email)")], unique=True
    )
    op.create_index(
        "uq_customers_name_normalized",
        "customers",
        [sa.text(r"lower(regexp_replace(btrim(customer_name), '\s+', ' ', 'g'))")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_customers_name_normalized", table_name="customers")
    op.drop_index("uq_customers_email_lower", table_name="customers")
    for column, type_ in _REQUIRED.items():
        op.alter_column("customers", column, existing_type=type_, nullable=True)
