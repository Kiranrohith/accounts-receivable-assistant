"""What people do with a dispute: enter one, classify it, reassign it, note it, resolve it.

Every action locks the dispute row first, so of two people acting at once one wins and the other
gets 409 (never two classifications, never a double resolve).

Who may act:
  enter / classify   disputes.work.normal / disputes.classify
  note               the work permission of the dispute's state: disputes.work.escalated once it is
                     Escalated, disputes.work.normal before
  resolve            the same permission, and the dispute must be theirs (or they can reassign: admin)
  reassign           disputes.reassign (admin); the new owner must hold that work permission
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import DISPUTE_DEFAULT_CONFIDENCE
from core.exceptions.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailure
from core.services.disputes import assignment, create
from core.services.users.users import permission_codes_for
from data.models.postgres import Dispute, User
from data.models.postgres.enums import DisputeCategory, DisputeMessageKind, DisputeStatus
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from data.repositories.users import users_repo
from schemas.disputes.disputes import (
    ActionResult,
    AddNote,
    ClassifyDispute,
    CreateDispute,
    CreateResult,
    NoteResult,
    ReassignDispute,
    ResolveDispute,
)

WORK_NORMAL = "disputes.work.normal"
WORK_ESCALATED = "disputes.work.escalated"
REASSIGN = "disputes.reassign"


def work_permission(status: DisputeStatus) -> str:
    return WORK_ESCALATED if status is DisputeStatus.ESCALATED else WORK_NORMAL


def _result(dispute: Dispute) -> ActionResult:
    return ActionResult(dispute_id=dispute.id, status=dispute.status.value, assigned_to=dispute.assigned_to)


async def _lock(db: AsyncSession, dispute_id: uuid.UUID) -> Dispute:
    """Lock order is invoice, then dispute: the same order create_dispute takes them in."""
    peek = await disputes_repo.get_dispute(db, dispute_id)
    if peek is not None and peek.invoice_id is not None:
        await invoices_repo.get_invoice_for_update(db, peek.invoice_id)
    dispute = await disputes_repo.lock_dispute(db, dispute_id)
    if dispute is None:
        raise NotFoundError("Dispute not found")
    return dispute


async def _authorize(db: AsyncSession, user: User, dispute: Dispute, *, must_own: bool) -> None:
    codes = await permission_codes_for(db, user)
    needed = work_permission(dispute.status)
    if needed not in codes:
        what = "escalated disputes" if needed == WORK_ESCALATED else "disputes"
        raise PermissionDeniedError(f"Your role does not work {what} (missing {needed})")
    if must_own and dispute.assigned_to not in (None, user.id) and REASSIGN not in codes:
        raise PermissionDeniedError("This dispute is assigned to someone else")


# ---- enter --------------------------------------------------------------------------------------
async def create_manual(db: AsyncSession, user: User, body: CreateDispute) -> CreateResult:
    category = DisputeCategory(body.category) if body.category else None
    created = await create.create_dispute(
        db,
        customer_id=body.customer_id,
        invoice_id=body.invoice_id,
        summary=body.summary.strip(),
        from_email=body.from_email.strip().lower() if body.from_email else None,
        received_at=body.received_at,
        gmail_thread_id=body.gmail_thread_id.strip() if body.gmail_thread_id else None,
        category=category,
        payment_related=body.payment_related,
        confidence=DISPUTE_DEFAULT_CONFIDENCE if category else None,
        actor=user.id,
        classified_by_person=category is not None,
    )
    dispute = created.dispute
    result = CreateResult(
        dispute_id=dispute.id,
        appended_to_existing=created.appended,
        status=dispute.status.value,
        assigned_to=dispute.assigned_to,
        linked_dispute_id=dispute.linked_dispute_id,
    )
    await unit_of_work.commit(db)
    return result


# ---- classify -----------------------------------------------------------------------------------
async def _attach(db: AsyncSession, dispute: Dispute, body: ClassifyDispute) -> None:
    """Fill in the customer / invoice a dispute arrived without."""
    if body.invoice_id is not None:
        if dispute.invoice_id is not None:
            raise ValidationFailure("This dispute already has an invoice")
        invoice = await invoices_repo.get_invoice_for_update(db, body.invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")
        if dispute.customer_id not in (None, invoice.customer_id):
            raise ValidationFailure("That invoice belongs to a different customer")
        dispute.invoice_id = invoice.id
        dispute.customer_id = invoice.customer_id
        invoice.is_disputed = True
    elif body.customer_id is not None:
        if dispute.customer_id is not None:
            raise ValidationFailure("This dispute already has a customer")
        if await customers_repo.get_by_id(db, body.customer_id) is None:
            raise NotFoundError("Customer not found")
        dispute.customer_id = body.customer_id


async def classify(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, body: ClassifyDispute
) -> ActionResult:
    dispute = await _lock(db, dispute_id)
    if dispute.status is not DisputeStatus.RAISED:
        raise ConflictError(f"This dispute is {dispute.status.value}; it was already classified")
    await _attach(db, dispute, body)
    dispute.category = DisputeCategory(body.category)
    dispute.payment_related = body.payment_related
    dispute.classified_by = user.id
    dispute.classified_at = datetime.now(UTC)
    dispute.updated_by = user.id
    await create.categorise_and_assign(
        db, dispute, actor=user.id, reason=f"Classified as {dispute.category.value} by {user.username}"
    )
    result = _result(dispute)
    await unit_of_work.commit(db)
    return result


# ---- reassign -----------------------------------------------------------------------------------
async def reassign(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, body: ReassignDispute
) -> ActionResult:
    dispute = await _lock(db, dispute_id)
    if dispute.status in (DisputeStatus.RESOLVED, DisputeStatus.RAISED):
        raise ConflictError(
            "A resolved dispute is closed" if dispute.status is DisputeStatus.RESOLVED
            else "Classify the dispute first; it has no owner yet"
        )
    target = await users_repo.get_user_by_id(db, body.assigned_to)
    if target is None or not target.is_active:
        raise ValidationFailure("That user does not exist or is deactivated")
    needed = work_permission(dispute.status)
    if needed not in await permission_codes_for(db, target):
        raise ValidationFailure(f"{target.username} cannot work this dispute (their role lacks {needed})")
    if dispute.assigned_to == target.id:
        raise ValidationFailure(f"It is already assigned to {target.username}")
    dispute.assigned_to = target.id
    dispute.updated_by = user.id
    reason = f"Reassigned to {target.username}" + (f": {body.reason.strip()}" if body.reason else "")
    assignment.log_state(db, dispute, dispute.status, reason, user_id=user.id)
    await assignment.tell_owner(db, dispute, target, await assignment.label(db, dispute), actor=user.id)
    result = _result(dispute)
    await unit_of_work.commit(db)
    return result


# ---- note ---------------------------------------------------------------------------------------
async def add_note(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, body: AddNote
) -> NoteResult:
    dispute = await _lock(db, dispute_id)
    if dispute.status is DisputeStatus.RESOLVED:
        raise ConflictError("This dispute is resolved; notes are closed")
    await _authorize(db, user, dispute, must_own=False)
    message = await disputes_repo.insert_message(
        db,
        dispute_id=dispute.id,
        kind=DisputeMessageKind.INTERNAL_NOTE,
        body=body.body.strip(),
        created_by=user.id,
    )
    result = NoteResult(dispute_id=dispute.id, message_id=message.id)
    await unit_of_work.commit(db)
    return result


# ---- resolve ------------------------------------------------------------------------------------
async def resolve(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, body: ResolveDispute
) -> ActionResult:
    dispute = await _lock(db, dispute_id)
    if dispute.status is DisputeStatus.RESOLVED:
        raise ConflictError("This dispute is already resolved")
    if dispute.status is DisputeStatus.RAISED:
        raise ConflictError("Classify the dispute before resolving it")
    await _authorize(db, user, dispute, must_own=True)
    before = dispute.status
    now = datetime.now(UTC)
    dispute.status = DisputeStatus.RESOLVED
    dispute.resolved_at = now
    dispute.resolved_by = user.id
    dispute.resolution_narrative = body.narrative.strip()
    dispute.updated_by = user.id
    if dispute.invoice_id is not None:  # the invoice stays flagged while another dispute on it is open
        await unit_of_work.flush(db)  # sessions do not autoflush: the count must see this one resolved
        invoice = await invoices_repo.get_invoice_for_update(db, dispute.invoice_id)
        if invoice is not None and await disputes_repo.open_count_on_invoice(db, dispute.invoice_id) == 0:
            invoice.is_disputed = False
    assignment.log_state(db, dispute, before, f"Resolved by {user.username}", user_id=user.id)
    result = _result(dispute)
    await unit_of_work.commit(db)
    return result
