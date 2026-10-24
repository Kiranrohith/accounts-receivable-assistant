"""mail.log.view and mail.ignored.route: the finance team manager may read the company mailbox

Two permissions, both for FTM (and ADM, who holds every permission):
  mail.log.view       read the Mail Log, Live Mail and one mail's text and history
  mail.ignored.route  send a mail that was set aside as general mail on to the pipeline it belongs to

Nothing else changes for FTM: the triage queue, rerouting a handed-on mail and everything else stay with
mail.triage.classify (FA, ADM). FA and ADM already pass every check these two open, through that permission.
Same uuid5 scheme and ON CONFLICT DO NOTHING as the other permission seeds, so re-running is harmless.

Revision ID: e1f4b7c92a58
Revises: d8b2f6a14c97
Create Date: 2026-10-25
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f4b7c92a58"
down_revision: str | None = "d8b2f6a14c97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ar-assistant.genworx")

PERMISSIONS = (
    ("mail.log.view", "View the mail log and live mail", "mail"),
    ("mail.ignored.route", "Send a mail set aside as general mail on to its pipeline", "mail"),
)
ROLES_WITH_THEM = ("FTM", "ADM")


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
        .values([{"id": _id("permission", code), "code": code, "name": name, "module": module} for code, name, module in PERMISSIONS])
        .on_conflict_do_nothing()
    )
    op.execute(
        postgresql.insert(_role_permissions)
        .values(
            [
                {
                    "id": _id("role_permission", f"{role}:{code}"),
                    "role_id": _id("role", role),
                    "permission_id": _id("permission", code),
                }
                for code, _, _ in PERMISSIONS
                for role in ROLES_WITH_THEM
            ]
        )
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    ids = [_id("permission", code) for code, _, _ in PERMISSIONS]
    op.execute(_role_permissions.delete().where(_role_permissions.c.permission_id.in_(ids)))
    op.execute(_permissions.delete().where(_permissions.c.id.in_(ids)))
