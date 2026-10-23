"""What is waiting for a person, as ONE shape.

Every queue contributes a SELECT with the same columns (see `_row`), so the permitted queues can be combined with
UNION ALL and counted, searched, sorted and paged by the database, never in Python. The conditions mirror the ones the
queue's own list endpoint applies; tests/queues compares the two so they cannot drift apart.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import DateTime, Numeric, String, Uuid, case, cast, func, literal, null, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from data.models.postgres import (
    Customer, Dispute, IngestedEmail, Invoice, PendingInvoiceChange, Payment, Reminder, SourceDocument, Task,
)
from data.repositories.mail import emails_repo
from data.models.postgres.enums import (
    DisputeStatus, EmailStatus, PaymentState, PendingChangeReason, PendingChangeStatus, ReminderState, TaskStatus,
)

# queue key -> function(user_id) -> SELECT of the common columns
Selector = Callable[[uuid.UUID], Select]

_NUMBER = r"^-?[0-9]+(\.[0-9]+)?$"


def _number(text_value: Any) -> Any:
    """A JSON text that is a plain number becomes numeric; anything else (a stray "1,234") becomes NULL, never an error."""
    return case((text_value.op("~")(_NUMBER), cast(text_value, Numeric)), else_=null())


def _row(queue: str, item_id, subject, customer, amount, currency, reason, created_at, due_at=None) -> Select:
    """The common shape. Every column is typed, so a NULL still unions with a real value."""
    def typed(value, type_):
        return cast(null(), type_) if value is None else cast(value, type_)

    return select(
        literal(queue, String).label("queue"),
        cast(item_id, Uuid).label("item_id"),
        typed(subject, String).label("subject"),
        typed(customer, String).label("customer_name"),
        typed(amount, Numeric).label("amount"),
        typed(currency, String).label("currency"),
        typed(reason, String).label("reason"),
        cast(created_at, DateTime(timezone=True)).label("created_at"),
        typed(due_at, DateTime(timezone=True)).label("due_at"),
    )


def _first_reason(snapshot: Any, fallback: str) -> Any:
    return func.coalesce(snapshot["reasons"][0].astext, fallback)


# ------------------------------------------------------------ document reviews (the uploader's own)
def _pending(user_id: uuid.UUID, reason: PendingChangeReason, kind: str | None, *, own: bool = True):
    P = PendingInvoiceChange
    conditions = [P.reason == reason, P.status == PendingChangeStatus.PENDING]
    if own:
        conditions.append(P.created_by == user_id)
    snapshot_kind = P.proposed_snapshot["kind"].astext
    if kind == "invoice":  # an invoice's extraction review is whatever is neither a PO nor a document-type review
        conditions += [snapshot_kind.is_distinct_from("purchase_order"), snapshot_kind.is_distinct_from("doc_type")]
    elif kind is not None:
        conditions.append(snapshot_kind == kind)
    return conditions


def doc_type_review(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    return (
        _row("doc_type_review", P.id, SourceDocument.file_name, None, None, None,
             _first_reason(P.proposed_snapshot, "Could not tell what kind of document this is"), P.created_at)
        .select_from(P).outerjoin(SourceDocument, SourceDocument.id == P.source_document_id)
        .where(*_pending(user_id, PendingChangeReason.EXTRACTION_REVIEW, "doc_type"))
    )


def invoice_review(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    extraction = P.proposed_snapshot["extraction"]
    return (
        _row("invoice_review", P.id, func.coalesce(extraction["invoice_number"].astext, SourceDocument.file_name),
             extraction["customer_name"].astext, _number(extraction["total_amount"].astext), extraction["currency"].astext,
             _first_reason(P.proposed_snapshot, "Needs a check before it is saved"), P.created_at)
        .select_from(P).outerjoin(SourceDocument, SourceDocument.id == P.source_document_id)
        .where(*_pending(user_id, PendingChangeReason.EXTRACTION_REVIEW, "invoice"))
    )


def po_review(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    extraction = P.proposed_snapshot["extraction"]
    return (
        _row("po_review", P.id, func.coalesce(extraction["po_number"].astext, SourceDocument.file_name),
             extraction["customer_name"].astext, _number(extraction["total_amount"].astext), extraction["currency"].astext,
             _first_reason(P.proposed_snapshot, "Needs a check before it is saved"), P.created_at)
        .select_from(P).outerjoin(SourceDocument, SourceDocument.id == P.source_document_id)
        .where(*_pending(user_id, PendingChangeReason.EXTRACTION_REVIEW, "purchase_order"))
    )


def version_review(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    return (
        _row("version_review", P.id, Invoice.invoice_number, Customer.customer_name, func.coalesce(Invoice.amount_foreign, Invoice.amount_inr),
             Invoice.invoice_currency, _first_reason(P.proposed_snapshot, "A new version of this invoice needs a decision"),
             P.created_at)
        .select_from(P).outerjoin(Invoice, Invoice.id == P.invoice_id).outerjoin(Customer, Customer.id == Invoice.customer_id)
        .where(*_pending(user_id, PendingChangeReason.VERSION_UPLOAD, None))
    )


def tds_review(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    return (
        _row("tds_review", P.id, Invoice.invoice_number, Customer.customer_name, func.coalesce(Invoice.amount_foreign, Invoice.amount_inr),
             Invoice.invoice_currency, _first_reason(P.proposed_snapshot, "A change of TDS needs your decision"), P.created_at)
        .select_from(P).outerjoin(Invoice, Invoice.id == P.invoice_id).outerjoin(Customer, Customer.id == Invoice.customer_id)
        .where(*_pending(user_id, PendingChangeReason.TDS_RECOMPUTE, None))
    )


def po_mismatch(user_id: uuid.UUID) -> Select:
    P = PendingInvoiceChange
    return (
        _row("po_mismatch", P.id, Invoice.invoice_number, Customer.customer_name, func.coalesce(Invoice.amount_foreign, Invoice.amount_inr),
             Invoice.invoice_currency, _first_reason(P.proposed_snapshot, "po_mismatch"), P.created_at)
        .select_from(P).join(Invoice, Invoice.id == P.invoice_id).outerjoin(Customer, Customer.id == Invoice.customer_id)
        .where(*_pending(user_id, PendingChangeReason.PO_MISMATCH, None, own=False))  # shared: not tied to the uploader
    )


# ------------------------------------------------------------ payments
def _payment(queue: str, state: PaymentState, fallback_reason: str) -> Selector:
    def select_(_: uuid.UUID) -> Select:
        return (
            _row(queue, Payment.id, func.coalesce(Payment.reference_number, Payment.payee_name),
                 func.coalesce(Customer.customer_name, Payment.payee_name),
                 func.coalesce(Payment.amount_received_foreign, Payment.amount_received_inr),
                 func.coalesce(Payment.payment_currency, "INR"),
                 func.coalesce(Payment.review_reasons[0].astext, fallback_reason), Payment.created_at)
            .select_from(Payment).outerjoin(Customer, Customer.id == Payment.customer_id)
            .where(Payment.state == state)
        )
    return select_


payment_review = _payment("payment_review", PaymentState.NEEDS_REVIEW, "Could not be settled automatically")
unapplied_cash = _payment("unapplied_cash", PaymentState.UNAPPLIED, "Money received that is not yet against an invoice")


# ------------------------------------------------------------ mail, reminders
def mail_triage(_: uuid.UUID) -> Select:
    E = IngestedEmail
    return (
        _row("mail_triage", E.id, E.subject, E.sender, None, None,
             case((E.classification.is_(None), "Not classified yet: please choose where it belongs"),
                  else_=func.concat("Reader suggests ", cast(E.classification, String), " (",
                                    func.coalesce(cast(E.classification_confidence, String), "?"), "%)")),
             func.coalesce(E.received_at, E.created_at))
        .where(emails_repo.waiting_for_person_condition())
    )


def reminder_draft(_: uuid.UUID) -> Select:
    return (
        _row("reminder_draft", Reminder.id, Invoice.invoice_number, Customer.customer_name, Invoice.collectible_balance,
             "INR", func.concat("Tier ", cast(Reminder.reminder_tier, String), " reminder to approve"),
             Reminder.created_at)
        .select_from(Reminder).join(Invoice, Invoice.id == Reminder.invoice_id).join(Customer, Customer.id == Invoice.customer_id)
        .where(Reminder.state == ReminderState.DRAFTED)
    )


# ------------------------------------------------------------ disputes and tasks
def _dispute_reference(): return func.concat("DSP-", func.upper(func.substr(cast(Dispute.id, String), 1, 8)))


def _dispute_base(queue: str, reason: str) -> Select:
    due = case((Dispute.first_response_at.is_(None), Dispute.first_response_due), else_=Dispute.resolution_due)
    return (
        _row(queue, Dispute.id, func.coalesce(Invoice.invoice_number, _dispute_reference()),
             func.coalesce(Customer.customer_name, "Unknown customer"), None, None, reason, Dispute.received_at, due)
        .select_from(Dispute).outerjoin(Invoice, Invoice.id == Dispute.invoice_id).outerjoin(Customer, Customer.id == Dispute.customer_id)
    )


def dispute_classify(_: uuid.UUID) -> Select:
    return _dispute_base("dispute_classify", "Needs a category and an owner").where(Dispute.status == DisputeStatus.RAISED)


def my_dispute(user_id: uuid.UUID) -> Select:
    return _dispute_base("my_dispute", "Assigned to you").where(
        Dispute.assigned_to == user_id,
        Dispute.status.in_([DisputeStatus.CATEGORISED, DisputeStatus.CLARIFICATION_SENT, DisputeStatus.ASSIGNED, DisputeStatus.ESCALATED]),
    )


def collection_task(user_id: uuid.UUID) -> Select:
    return (
        _row("collection_task", Task.id, Invoice.invoice_number, Customer.customer_name, Invoice.collectible_balance,
             "INR", "60 days overdue: collect it", Task.created_at)
        .select_from(Task).outerjoin(Invoice, Invoice.id == Task.invoice_id).outerjoin(Customer, Customer.id == Task.customer_id)
        .where(Task.status == TaskStatus.OPEN, Task.assigned_to == user_id)
    )


SELECTORS: dict[str, Selector] = {
    "doc_type_review": doc_type_review,
    "invoice_review": invoice_review,
    "po_review": po_review,
    "version_review": version_review,
    "tds_review": tds_review,
    "po_mismatch": po_mismatch,
    "payment_review": payment_review,
    "unapplied_cash": unapplied_cash,
    "mail_triage": mail_triage,
    "reminder_draft": reminder_draft,
    "dispute_classify": dispute_classify,
    "my_dispute": my_dispute,
    "collection_task": collection_task,
}

_SORTS = {"created_at": "created_at", "amount": "amount", "due_at": "due_at"}


def _combined(queues: list[str], user_id: uuid.UUID):
    selects = [SELECTORS[key](user_id) for key in queues]
    return (selects[0] if len(selects) == 1 else union_all(*selects)).subquery("items")


async def counts(db: AsyncSession, queues: list[str], user_id: uuid.UUID) -> dict[str, int]:
    """One query: how many items each of these queues holds."""
    if not queues:
        return {}
    items = _combined(queues, user_id)
    rows = await db.execute(select(items.c.queue, func.count()).group_by(items.c.queue))
    found = {queue: count for queue, count in rows}
    return {key: found.get(key, 0) for key in queues}


async def page(
    db: AsyncSession, queues: list[str], user_id: uuid.UUID, *, search: str | None, sort: str, descending: bool,
    limit: int, offset: int,
) -> tuple[list[Any], int]:
    """(rows, total): the queues' items together, searched, sorted and cut to one page by the database."""
    if not queues:
        return [], 0
    items = _combined(queues, user_id)
    where = []
    if search and search.strip():
        # What the person typed is text, not a pattern: a "%" or "_" in it must match itself.
        escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        where.append(or_(items.c.subject.ilike(like, escape="\\"), items.c.customer_name.ilike(like, escape="\\")))

    column = items.c[_SORTS.get(sort, "created_at")]
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()  # no value sorts last either way
    total = await db.scalar(select(func.count()).select_from(items).where(*where))
    rows = await db.execute(select(items).where(*where).order_by(ordered, items.c.created_at.desc(), items.c.item_id).limit(limit).offset(offset))
    return list(rows), total or 0
