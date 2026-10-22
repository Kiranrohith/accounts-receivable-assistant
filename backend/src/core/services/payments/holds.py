"""Where money meets disputes: what an open dispute does to a payment aimed at its invoice.

  - a dispute that HOLDS money (uncategorised, Other, or about the payment itself) freezes the
    invoice's slice of the remittance: it sits in payments.held_amount, pinned to the oldest holding
    dispute of the invoice, and the rest of the payment applies as usual
  - a dispute that does not hold lets the payment apply; its owner is told (PaymentDuringDispute)
  - several holding disputes on one invoice are ONE hold; it is released only when none is left

The functions here read and notify; the writes are in apply.py (placing a hold) and release.py
(letting one go).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.services import notifications
from core.services.disputes import holding
from core.services.disputes.outbound import reference_for
from data.models.postgres import Dispute, Invoice, Payment
from data.models.postgres.enums import NotificationType
from data.repositories.disputes import disputes_repo


@dataclass(frozen=True)
class Touch:
    """Money that reached a disputed invoice, for the dispute owners' notice."""

    invoice: Invoice
    disputes: list[Dispute]
    what: str  # "applied INR 100.00", "held INR 100.00"


async def open_disputes_by_invoice(
    db: AsyncSession, invoices: list[Invoice]
) -> dict[uuid.UUID, list[Dispute]]:
    """Open disputes per invoice, oldest first. Only invoices flagged disputed are looked up."""
    grouped: dict[uuid.UUID, list[Dispute]] = {}
    for dispute in await disputes_repo.open_on_invoices(db, [i.id for i in invoices if i.is_disputed]):
        grouped.setdefault(dispute.invoice_id, []).append(dispute)
    return grouped


def holding_of(disputes: list[Dispute] | None) -> list[Dispute]:
    return holding.holding_in_order(disputes or [])


def names(disputes: list[Dispute]) -> str:
    return ", ".join(reference_for(d.id) for d in disputes)


async def tell_owners(
    db: AsyncSession, payment: Payment, touches: list[Touch], actor: uuid.UUID | None
) -> None:
    """One PaymentDuringDispute notice per dispute owner, listing what happened on their disputes.
    Disputes still in the classify queue have no owner yet: their assignment will tell the owner."""
    per_owner: dict[uuid.UUID, list[str]] = {}
    for touch in touches:
        for dispute in touch.disputes:
            if dispute.assigned_to is None:
                continue
            per_owner.setdefault(dispute.assigned_to, []).append(
                f"{touch.what} on invoice {touch.invoice.invoice_number} (dispute {reference_for(dispute.id)})"
            )
    reference = payment.reference_number or "(no reference)"
    for owner_id, lines in per_owner.items():
        await notifications.create(
            db,
            owner_id,
            NotificationType.PAYMENT_DURING_DISPUTE,
            f"Payment {reference}: " + "; ".join(lines),
            entity_type="payment",
            entity_id=payment.id,
            actor=actor,
        )
