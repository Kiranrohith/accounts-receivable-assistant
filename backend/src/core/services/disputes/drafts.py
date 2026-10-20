"""What a person does with the clarification e-mail: ask for another draft, edit one, send it.

Later drafts are never sent by themselves: only the first clarification can go out automatically
(see clarify.py); everything here is a person's decision. Sending takes the dispute's row lock, so of
two people sending at once one wins and the other gets 409 (the mail goes out once).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, ExternalServiceError, NotFoundError
from core.services.disputes import clarification, outbound
from core.services.disputes.workflow import authorize
from data.models.postgres import Dispute, DisputeMessage, User
from data.models.postgres.enums import DisputeMessageKind, DisputeStatus
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from schemas.disputes.disputes import DraftRequest, DraftResult, EditDraft, SendResult


def _check_open(dispute: Dispute) -> None:
    if dispute.status is DisputeStatus.RESOLVED:
        raise ConflictError("This dispute is resolved; it takes no more messages")
    if dispute.status is DisputeStatus.RAISED:
        raise ConflictError("Classify the dispute first; the reply depends on its category")


async def _draft_of(db: AsyncSession, dispute: Dispute, message_id: uuid.UUID) -> DisputeMessage:
    message = await disputes_repo.lock_message(db, message_id)
    if message is None or message.dispute_id != dispute.id:
        raise NotFoundError("Message not found on this dispute")
    if message.kind is not DisputeMessageKind.DRAFT:
        raise ConflictError(f"That message is already {message.kind.value.replace('_', ' ')}; only a draft can be changed or sent")
    return message


def _result(dispute: Dispute, message: DisputeMessage, confidence: int | None, ungrounded: list[str]) -> DraftResult:
    subject, text = outbound.split(message.body)
    return DraftResult(
        dispute_id=dispute.id,
        message_id=message.id,
        subject=subject,
        body=text,
        confidence=confidence,
        ungrounded=ungrounded,
    )


async def new_draft(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, body: DraftRequest
) -> DraftResult:
    dispute = await disputes_repo.get_dispute(db, dispute_id)
    if dispute is None:
        raise NotFoundError("Dispute not found")
    _check_open(dispute)
    await authorize(db, user, dispute, must_own=True)
    try:
        draft = await clarification.generate(db, dispute, instructions=body.instructions)
    except clarification.ExtractionError as exc:
        raise ExternalServiceError(f"The e-mail could not be drafted right now: {exc}") from exc
    message = await disputes_repo.insert_message(
        db,
        dispute_id=dispute.id,
        kind=DisputeMessageKind.DRAFT,
        body=outbound.compose(
            outbound.subject_with_reference(draft.subject, clarification.reference_for(dispute.id)), draft.body
        ),
        created_by=user.id,
    )
    if dispute.clarification_confidence is None:
        dispute.clarification_confidence = draft.confidence
    result = _result(dispute, message, draft.confidence, draft.grounding.ungrounded)
    await unit_of_work.commit(db)
    return result


async def edit_draft(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, message_id: uuid.UUID, body: EditDraft
) -> DraftResult:
    dispute = await disputes_repo.lock_dispute(db, dispute_id)
    if dispute is None:
        raise NotFoundError("Dispute not found")
    _check_open(dispute)
    await authorize(db, user, dispute, must_own=True)
    message = await _draft_of(db, dispute, message_id)
    subject, text = outbound.split(message.body)
    message.body = outbound.compose(body.subject or subject or "Your query", body.body or text)
    result = _result(dispute, message, None, [])
    await unit_of_work.commit(db)
    return result


async def send_message(
    db: AsyncSession, user: User, dispute_id: uuid.UUID, message_id: uuid.UUID
) -> SendResult:
    dispute = await disputes_repo.lock_dispute(db, dispute_id)
    if dispute is None:
        raise NotFoundError("Dispute not found")
    _check_open(dispute)
    await authorize(db, user, dispute, must_own=True)
    message = await _draft_of(db, dispute, message_id)
    to = await outbound.send_draft(db, dispute, message, user_id=user.id)
    return SendResult(
        dispute_id=dispute.id, message_id=message.id, sent_to=to, status=dispute.status.value
    )
