"""disputes, dispute_messages, notifications (v7), payments.held_for_dispute_id, doc_kind += dispute

New tables: disputes, dispute_messages, notifications.
New enums: dispute_category, dispute_status, dispute_message_kind, notification_type.
New enum value: doc_kind.dispute (a customer's complaint is read by its own pipeline).
New column: payments.held_for_dispute_id (the column P1 left out until disputes existed).

My additions to the DBML: disputes.source_document_id, summary, from_email, classified_by,
classified_at, resolved_at, resolved_by. An invoice may have several disputes at once (decision 62):
a reply in the same mail thread joins its dispute (E2) or follows a resolved one (E3); a complaint
in a new thread is its own dispute, with the others on the invoice shown as "possibly related".

The downgrade drops the tables and enums but cannot remove doc_kind.dispute (Postgres cannot drop
an enum value); nothing uses it once the tables are gone.

Revision ID: a8d3e5f0b216
Revises: f3c9a82d5e17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8d3e5f0b216"
down_revision: str | None = "f3c9a82d5e17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")

_ENUMS = {
    "dispute_category": (
        "Pricing", "Quality", "Delivery", "AlreadyPaid", "WrongDuplicateInvoice",
        "TaxDiscrepancy", "POMismatch", "CreditNotePending", "Other",
    ),
    "dispute_status": ("Raised", "Categorised", "ClarificationSent", "Assigned", "Escalated", "Resolved"),
    "dispute_message_kind": ("inbound_email", "draft", "sent", "internal_note"),
    "notification_type": (
        "Escalation", "Assignment", "ShortPayment", "TDSFlip", "PaymentDuringDispute",
        "SendFailure", "SuggestionTask", "DeadlineWarning", "DisputeFollowUp",
    ),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _audit() -> list[sa.Column]:
    return [
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    ]


def upgrade() -> None:
    op.execute("ALTER TYPE doc_kind ADD VALUE IF NOT EXISTS 'dispute'")
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "disputes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=True, comment="NULL = a customer-level dispute (E1)"),
        sa.Column("linked_dispute_id", sa.UUID(), nullable=True,
                  comment="a reply after resolution opens a new dispute linked to the old one (E3)"),
        sa.Column("ingested_email_id", sa.UUID(), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True,
                  comment="the uploaded complaint this dispute was read from"),
        sa.Column("category", _enum("dispute_category"), nullable=True),
        sa.Column("status", _enum("dispute_status"), server_default="Raised", nullable=False),
        sa.Column("payment_related", sa.Boolean(), server_default=sa.text("false"), nullable=False,
                  comment="the customer withheld or short-paid on purpose: this one can hold a payment"),
        sa.Column("classification_confidence", sa.Integer(), nullable=True),
        sa.Column("clarification_confidence", sa.Integer(), nullable=True,
                  comment="gates the FIRST clarification draft only; later drafts are human-sent"),
        sa.Column("summary", sa.Text(), nullable=True, comment="what the customer complains about"),
        sa.Column("from_email", sa.String(), nullable=True),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("classified_by", sa.UUID(), nullable=True, comment="NULL = the reader classified it"),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_response_due", sa.Date(), nullable=True),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_due", sa.Date(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("gmail_thread_id", sa.String(), nullable=True),
        sa.Column("resolution_narrative", sa.Text(), nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["linked_dispute_id"], ["disputes.id"]),
        sa.ForeignKeyConstraint(["ingested_email_id"], ["ingested_emails.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["classified_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disputes_invoice", "disputes", ["invoice_id"])
    op.create_index("ix_disputes_thread", "disputes", ["gmail_thread_id"])
    op.create_index("ix_disputes_status", "disputes", ["status"])
    op.create_index("ix_disputes_customer", "disputes", ["customer_id"])
    op.create_index("ix_disputes_assigned", "disputes", ["assigned_to", "status"])

    op.create_table(
        "dispute_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("dispute_id", sa.UUID(), nullable=False),
        sa.Column("kind", _enum("dispute_message_kind"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, comment="drafted clarifications persist here"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_message_id", sa.String(), nullable=True),
        sa.Column("ingested_email_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"]),
        sa.ForeignKeyConstraint(["ingested_email_id"], ["ingested_emails.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_dispute_messages_thread_evidence", "dispute_messages", ["dispute_id", "gmail_message_id"],
        unique=True, postgresql_where=sa.text("gmail_message_id IS NOT NULL"),
    )
    op.create_index("ix_dispute_messages_dispute", "dispute_messages", ["dispute_id", "created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipient_user_id", sa.UUID(), nullable=False),
        sa.Column("type", _enum("notification_type"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.String(), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_audit(),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_recipient", "notifications", ["recipient_user_id", "is_read", "created_at"]
    )

    op.add_column(
        "payments",
        sa.Column("held_for_dispute_id", sa.UUID(), nullable=True,
                  comment="non-null = a hold is active: that portion sits in held_amount"),
    )
    op.create_foreign_key(
        "fk_payments_held_for_dispute", "payments", "disputes", ["held_for_dispute_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_payments_held_for_dispute", "payments", type_="foreignkey")
    op.drop_column("payments", "held_for_dispute_id")
    op.drop_table("notifications")
    op.drop_table("dispute_messages")
    op.drop_table("disputes")
    for name in _ENUMS:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
    # doc_kind.dispute stays: Postgres cannot drop an enum value
