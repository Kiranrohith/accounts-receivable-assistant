"""seed roles permissions role_permissions

Reference data only (no users). IDs are uuid5 of the code, so they are the same on
every database and safe to assert in tests. Inserts are ON CONFLICT DO NOTHING, so
re-running is harmless (F12: seed twice is idempotent).

Revision ID: cfc261a50dc8
Revises: af185ae3a119
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cfc261a50dc8"
down_revision: str | None = "af185ae3a119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ar-assistant.genworx")


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"{kind}:{code}")


ROLES = [
    ("FA", "Finance Associate",
     "Day-to-day processing: uploads, payment review, reminders, mail triage, normal disputes"),
    ("FTM", "Finance Team Member",
     "Escalated work: escalated disputes, 60-day collections, customer master, corrections, credit notes"),
    ("ADM", "Finance Admin",
     "Full access plus user management, dispute reassignment, timeline and step-log screens"),
]

PERMISSIONS = [
    ("invoices.upload", "Upload invoice PDFs", "invoices"),
    ("invoices.view", "View invoice list and detail", "invoices"),
    ("invoices.review", "Work extraction/version review screen", "invoices"),
    ("invoices.rejected.rerun", "View rejected list and re-run items", "invoices"),
    ("customers.view", "View customer master", "customers"),
    ("customers.manage", "Add/edit customers incl. TDS flag and recompute", "customers"),
    ("customers.alias.confirm", "Confirm and save customer aliases", "customers"),
    ("payments.view", "View payment list and detail", "payments"),
    ("payments.review.match",
     "Work payment review queue (manual match, unapplied, reject, reroute)", "payments"),
    ("payments.correction.rerun", "Reverse applications and re-run matching", "payments"),
    ("notes.create", "Create and apply credit notes", "payments"),
    ("mail.triage.classify", "Work human mail-triage queue", "mail"),
    ("reminders.manage", "Edit, approve, send, reject reminder drafts", "reminders"),
    ("reminders.view_sent", "View sent reminder status", "reminders"),
    ("tasks.collection.work", "Work 60-day collection tasks", "tasks"),
    ("tasks.retromatch.review", "Accept/dismiss retro-match suggestions", "tasks"),
    ("disputes.dashboard.view", "View dispute dashboard", "disputes"),
    ("disputes.classify", "Work manual classification queue", "disputes"),
    ("disputes.work.normal", "Work normal disputes (clarify, reply, resolve)", "disputes"),
    ("disputes.work.escalated", "Work escalated disputes", "disputes"),
    ("disputes.reassign", "Reassign disputes between users", "disputes"),
    ("reports.view", "View and download reports", "reports"),
    ("failed_items.rerun", "View failed items and re-run jobs", "platform"),
    ("users.manage", "Create/deactivate users, assign roles", "users"),
    ("logs.timeline.view", "View item state-log timelines", "logs"),
    ("logs.steplog.view", "View step-log detail", "logs"),
]

ROLE_PERMISSIONS = {
    "FA": [
        "invoices.upload", "invoices.view", "invoices.review",
        "invoices.rejected.rerun", "customers.view", "payments.view",
        "payments.review.match", "mail.triage.classify",
        "reminders.manage", "reminders.view_sent",
        "disputes.dashboard.view", "disputes.classify",
        "disputes.work.normal", "reports.view", "failed_items.rerun",
    ],
    "FTM": [
        "invoices.upload", "invoices.view", "invoices.review",
        "invoices.rejected.rerun", "customers.view", "customers.manage",
        "customers.alias.confirm", "payments.view", "payments.review.match",
        "payments.correction.rerun", "notes.create", "reminders.view_sent",
        "tasks.collection.work", "tasks.retromatch.review",
        "disputes.dashboard.view", "disputes.work.escalated",
        "reports.view", "failed_items.rerun",
    ],
    "ADM": [code for code, _, _ in PERMISSIONS],
}

_roles = sa.table(
    "roles",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("is_active", sa.Boolean),
)
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


def _insert(table: sa.TableClause, rows: list[dict]) -> None:
    op.execute(postgresql.insert(table).values(rows).on_conflict_do_nothing())


def upgrade() -> None:
    _insert(
        _roles,
        [
            {"id": _id("role", c), "code": c, "name": n, "description": d, "is_active": True}
            for c, n, d in ROLES
        ],
    )
    _insert(
        _permissions,
        [
            {"id": _id("permission", c), "code": c, "name": n, "module": m}
            for c, n, m in PERMISSIONS
        ],
    )
    _insert(
        _role_permissions,
        [
            {
                "id": _id("role_permission", f"{role}:{perm}"),
                "role_id": _id("role", role),
                "permission_id": _id("permission", perm),
            }
            for role, perms in ROLE_PERMISSIONS.items()
            for perm in perms
        ],
    )


def downgrade() -> None:
    role_ids = [_id("role", c) for c, _, _ in ROLES]
    perm_ids = [_id("permission", c) for c, _, _ in PERMISSIONS]
    op.execute(_role_permissions.delete().where(_role_permissions.c.role_id.in_(role_ids)))
    op.execute(_permissions.delete().where(_permissions.c.id.in_(perm_ids)))
    op.execute(_roles.delete().where(_roles.c.id.in_(role_ids)))
