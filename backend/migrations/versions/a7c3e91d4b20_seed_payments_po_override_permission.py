"""seed payments.po_override permission (v7: 27 permission codes)

Adds the one new permission from design v7 and links it to FTM and ADM (FA never gets it):
FA 15 / FTM 19 / ADM 27 = 61 role-permission links. Same uuid5 scheme and ON CONFLICT DO
NOTHING as the original seed, so re-running is harmless.

Revision ID: a7c3e91d4b20
Revises: fa8e7ce50ac9
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e91d4b20"
down_revision: str | None = "fa8e7ce50ac9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ar-assistant.genworx")

CODE = "payments.po_override"
NAME = "Override a PO fulfilment mismatch (mandatory reason)"
MODULE = "payments"
ROLES_WITH_IT = ("FTM", "ADM")


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"{kind}:{code}")


_permissions = sa.table(
    "permissions",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("module", sa.String),
)
_role_permissions = sa.table(
    "role_permissions",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("role_id", postgresql.UUID(as_uuid=True)),
    sa.column("permission_id", postgresql.UUID(as_uuid=True)),
)


def upgrade() -> None:
    op.execute(
        postgresql.insert(_permissions)
        .values([{"id": _id("permission", CODE), "code": CODE, "name": NAME, "module": MODULE}])
        .on_conflict_do_nothing()
    )
    op.execute(
        postgresql.insert(_role_permissions)
        .values(
            [
                {
                    "id": _id("role_permission", f"{role}:{CODE}"),
                    "role_id": _id("role", role),
                    "permission_id": _id("permission", CODE),
                }
                for role in ROLES_WITH_IT
            ]
        )
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    op.execute(
        _role_permissions.delete().where(_role_permissions.c.permission_id == _id("permission", CODE))
    )
    op.execute(_permissions.delete().where(_permissions.c.id == _id("permission", CODE)))
