"""payments, payment_allocations, notes (v7), doc_kind += payment

New tables: payments, payment_allocations, notes.
New enums: payment_state, matched_layer, tolerance, allocation_status, note_status.
New enum value: doc_kind.payment (a remittance advice is read by its own pipeline).
New sequence: note_number_seq (credit-note numbers CN-<year>-<6 digits>).

Left out until their tables exist: payments.held_for_dispute_id (disputes). The downgrade drops
the new tables and enums but cannot remove doc_kind.payment (Postgres cannot drop an enum
value); nothing uses it once the tables are gone.

Revision ID: e5b8c13a7d94
Revises: d2e9a4b17c63
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b8c13a7d94"
down_revision: str | None = "d2e9a4b17c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_ZERO = sa.text("0")
_MONEY = sa.Numeric(18, 2)

_ENUMS = {
    "payment_state": (
        "Received", "Reading", "Matching", "Applied", "PartiallyApplied",
        "Unapplied", "NeedsReview", "Rejected", "Held", "Failed",
    ),
    "matched_layer": ("L1", "L2", "L3", "L4"),
    "tolerance": ("T1", "T2", "T3", "T4"),
    "allocation_status": ("Active", "Reversed"),
    "note_status": ("Open", "PartiallyUsed", "Closed", "Voided"),
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
    op.execute("ALTER TYPE doc_kind ADD VALUE IF NOT EXISTS 'payment'")
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("state", _enum("payment_state"), server_default="Received", nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True,
                  comment="resolved identity, set the moment the waterfall resolves it"),
        sa.Column("payment_currency", sa.CHAR(3), nullable=True),
        sa.Column("amount_received_foreign", _MONEY, nullable=True,
                  comment="as received, in payment_currency; only for a non-INR payment"),
        sa.Column("amount_received_inr", _MONEY, nullable=True,
                  comment="AUTHORITATIVE identity basis; a foreign payment gets it when matched "
                  "(received x the paid invoices' frozen rate)"),
        sa.Column("unapplied_amount", _MONEY, server_default=_ZERO, nullable=False,
                  comment="unclaimed residual only"),
        sa.Column("held_amount", _MONEY, server_default=_ZERO, nullable=False,
                  comment="dispute-held portion (the dispute module is not built yet: always 0)"),
        sa.Column("credited_amount", _MONEY, server_default=_ZERO, nullable=False,
                  comment="sum of credit notes born from this payment"),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("reference_number", sa.String(), nullable=True, comment="junk refs never dedup"),
        sa.Column("payee_name", sa.String(), nullable=True, comment="who paid, as written"),
        sa.Column("payee_bank", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=True),
        sa.Column("from_email", sa.String(), nullable=True, comment="L2 sender key"),
        sa.Column("extracted_invoice_numbers", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_po", sa.String(), nullable=True, comment="display only"),
        sa.Column("extracted_credit_note_ref", sa.String(), nullable=True,
                  comment="a note_number the remittance quotes: the SPECIFIC spend instruction"),
        sa.Column("credit_note_requested", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False, comment="GENERIC 'use our credit' instruction"),
        sa.Column("confidence_score", sa.Integer(), nullable=True,
                  comment="stored clamped at 100; ties are decided on raw scores"),
        sa.Column("matched_layer", _enum("matched_layer"), nullable=True),
        sa.Column("match_reason", sa.Text(), nullable=True),
        sa.Column("review_reasons", postgresql.JSONB(), nullable=True,
                  comment="plain-English reasons while the payment waits for a person"),
        sa.Column("rematch_count", sa.Integer(), server_default=_ZERO, nullable=False,
                  comment="contention requeues; capped, then review"),
        sa.Column("duplicate_of", sa.UUID(), nullable=True),
        sa.Column("ingested_email_id", sa.UUID(), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["duplicate_of"], ["payments.id"]),
        sa.ForeignKeyConstraint(["ingested_email_id"], ["ingested_emails.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id", name="uq_payments_source_document"),
    )
    op.create_index("ix_payments_reference_customer", "payments", ["reference_number", "customer_id"])
    op.create_index("ix_payments_state", "payments", ["state"])
    op.create_index(
        "idx_payments_unapplied", "payments", ["customer_id"],
        postgresql_where=sa.text("unapplied_amount > 0"),
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("note_number", sa.String(), nullable=False, comment="quotable by a remittance"),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.CHAR(3), server_default="INR", nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("remaining_amount", _MONEY, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("linked_invoice_id", sa.UUID(), nullable=True),
        sa.Column("linked_payment_id", sa.UUID(), nullable=True),
        sa.Column("status", _enum("note_status"), server_default="Open", nullable=False),
        sa.Column("generated_document_id", sa.UUID(), nullable=True,
                  comment="customer-facing PDF; rendering comes with the template phase"),
        *_audit(),
        sa.CheckConstraint("currency = 'INR'", name="ck_notes_inr"),
        sa.CheckConstraint("remaining_amount >= 0 AND remaining_amount <= amount",
                           name="ck_notes_remaining"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["linked_invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["linked_payment_id"], ["payments.id"]),
        sa.ForeignKeyConstraint(["generated_document_id"], ["source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_number"),
    )
    op.create_index("ix_notes_customer_status", "notes", ["customer_id", "status"])

    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=True),
        sa.Column("note_id", sa.UUID(), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("allocated_amount", _MONEY, nullable=False),
        sa.Column("matched_layer", _enum("matched_layer"), nullable=True),
        sa.Column("tolerance_applied", _enum("tolerance"), nullable=True),
        sa.Column("fx_rate_used", sa.Numeric(18, 8), nullable=True,
                  comment="the invoice's frozen rate used for this allocation"),
        sa.Column("tolerance_residual", _MONEY, nullable=True,
                  comment="SIGNED: received - allocated (T1 rounding and T2 band absorption)"),
        sa.Column("tds_deducted", _MONEY, nullable=True),
        sa.Column("allocated_by", sa.UUID(), nullable=True, comment="NULL = the system"),
        sa.Column("status", _enum("allocation_status"), server_default="Active", nullable=False),
        *_audit(),
        sa.CheckConstraint("(payment_id IS NULL) <> (note_id IS NULL)",
                           name="ck_allocations_one_funder"),
        sa.CheckConstraint(
            "note_id IS NULL OR tolerance_residual IS NULL OR tolerance_residual = 0",
            name="ck_allocations_note_exact",
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["allocated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_allocations_payment", "payment_allocations", ["payment_id"])
    op.create_index("ix_payment_allocations_note", "payment_allocations", ["note_id"])
    op.create_index("ix_payment_allocations_invoice", "payment_allocations", ["invoice_id"])
    op.execute("CREATE SEQUENCE IF NOT EXISTS note_number_seq")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS note_number_seq")
    op.drop_table("payment_allocations")
    op.drop_table("notes")
    op.drop_table("payments")
    for name in _ENUMS:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
    # doc_kind.payment stays: Postgres cannot drop an enum value
