"""The resolution e-mail (decision 64): a DRAFT the dispute owner approves, never sent by itself.

When a dispute is resolved, its outcome narrative (written for staff, so it may not be fit for the
customer to read as it stands) is dropped into the resolution template and stored as a draft message.
The owner edits it into customer-safe wording and sends it with the ordinary draft endpoints
(PATCH / POST .../send); sending is logged on the dispute. A reply to it opens a new linked dispute.

A clarification draft that was never sent is set aside as an internal note first, so the only draft a
resolved dispute can have is this one (and so the only thing that can still be sent from it).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.disputes import outbound
from core.services.mail import templates
from data.models.postgres import Dispute, DisputeMessage
from data.models.postgres.enums import DisputeMessageKind
from data.repositories.customers import customers_repo
from data.repositories.disputes import disputes_repo

SET_ASIDE = "Unsent draft, set aside when the dispute was resolved:\n"


async def set_aside_stale_drafts(db: AsyncSession, dispute: Dispute) -> int:
    count = 0
    for message, _ in await disputes_repo.messages_for(db, dispute.id):
        if message.kind is DisputeMessageKind.DRAFT:
            message.kind = DisputeMessageKind.INTERNAL_NOTE
            message.body = SET_ASIDE + message.body
            count += 1
    return count


async def draft_for(db: AsyncSession, dispute: Dispute, narrative: str, *, actor) -> DisputeMessage:  # noqa: ANN001
    customer = await customers_repo.get_by_id(db, dispute.customer_id) if dispute.customer_id else None
    rendered = templates.render_email(
        "dispute_resolved",
        dispute_ref=outbound.reference_for(dispute.id),
        customer_name=(customer.customer_name if customer else None) or "Customer",
        contact_name=customer.contact if customer else None,
        narrative=narrative.strip(),
    )
    await set_aside_stale_drafts(db, dispute)
    return await disputes_repo.insert_message(
        db,
        dispute_id=dispute.id,
        kind=DisputeMessageKind.DRAFT,
        body=outbound.compose(rendered.subject, rendered.body),
        created_by=actor,
    )
