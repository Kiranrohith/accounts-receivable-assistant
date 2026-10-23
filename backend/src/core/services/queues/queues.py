"""The decision queues the signed-in person can act on: what they are, how many items each holds, and the items."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import NotFoundError, PermissionDeniedError
from core.services.users.users import permission_codes_for
from data.models.postgres import User
from data.repositories.queues import queues_repo
from schemas.common import Page
from schemas.queues.queues import QueueItem, QueueSummary


@dataclass(frozen=True)
class QueueDef:
    key: str
    label: str
    description: str
    any_of: tuple[str, ...]  # holding ANY of these permissions shows the queue


# Design order (frontend design, section 4). A queue the person has no permission for is not shown at all.
QUEUES: tuple[QueueDef, ...] = (
    QueueDef("doc_type_review", "Document type", "Uploads the reader could not tell were an invoice or a purchase order.", ("invoices.review",)),
    QueueDef("invoice_review", "Invoice check", "Extracted invoices that need your check before they are saved.", ("invoices.review",)),
    QueueDef("po_review", "Purchase order check", "Extracted purchase orders that need your check before they are saved.", ("invoices.review",)),
    QueueDef("version_review", "Invoice changes", "A new version of a saved invoice: approve or reject the change.", ("invoices.review",)),
    QueueDef("tds_review", "TDS changes", "Part-paid or disputed invoices whose TDS you asked to recompute: approve or reject.", ("customers.manage",)),
    QueueDef("po_mismatch", "PO mismatch", "Invoices that do not fit their purchase order.", ("invoices.review",)),
    QueueDef("payment_review", "Match review", "Payments the system could not settle on its own.", ("payments.review.match",)),
    QueueDef("unapplied_cash", "Unapplied cash", "Money received that is not yet against an invoice.", ("payments.review.match",)),
    QueueDef("mail_triage", "Mail triage", "Mail the reader could not classify.", ("mail.triage.classify",)),
    QueueDef("reminder_draft", "Reminder drafts", "Payment reminders waiting for your approval to send.", ("reminders.manage",)),
    QueueDef("dispute_classify", "Dispute classification", "Complaints that need a category and an owner.", ("disputes.classify",)),
    QueueDef("my_dispute", "My disputes", "Disputes assigned to you.", ("disputes.work.normal", "disputes.work.escalated")),
    QueueDef("collection_task", "Collection tasks", "Invoices 60 days overdue, handed to you to collect.", ("tasks.collection.work",)),
)
_BY_KEY = {q.key: q for q in QUEUES}
assert set(_BY_KEY) == set(queues_repo.SELECTORS), "every queue needs a definition and a selector"

# The Take Action "Sort by" control: (column, largest or newest first?)
SORTS = {"newest": ("created_at", True), "oldest": ("created_at", False), "amount": ("amount", True), "deadline": ("due_at", False)}


async def _visible(db: AsyncSession, user: User) -> list[QueueDef]:
    held = await permission_codes_for(db, user)
    return [q for q in QUEUES if any(code in held for code in q.any_of)]


async def catalog(db: AsyncSession, user: User) -> list[QueueSummary]:
    visible = await _visible(db, user)
    counts = await queues_repo.counts(db, [q.key for q in visible], user.id)
    return [QueueSummary(key=q.key, label=q.label, description=q.description, count=counts[q.key]) for q in visible]


async def items(
    db: AsyncSession, user: User, *, queue: str | None, search: str | None, sort: str, limit: int, offset: int
) -> Page[QueueItem]:
    """One queue, or (queue=None) every queue the person can act on, merged."""
    visible = [q.key for q in await _visible(db, user)]
    if queue is not None:
        if queue not in _BY_KEY:
            raise NotFoundError("No such queue")
        if queue not in visible:
            raise PermissionDeniedError("Your role does not work this queue")
        visible = [queue]
    column, descending = SORTS[sort]
    rows, total = await queues_repo.page(
        db, visible, user.id, search=search, sort=column, descending=descending, limit=limit, offset=offset
    )
    return Page[QueueItem](items=[QueueItem(**dict(row._mapping)) for row in rows], total=total)
