"""The dispute deadline scan: warn before a deadline, escalate after one is missed.

Run once a day (09:15 IST) by the scheduler, or on demand by an admin. It is IDEMPOTENT: running it
twice in a day changes nothing the second time, and each dispute is handled in its own transaction
under its row lock, judged by the timestamps as they are at that moment (a dispute resolved, or
answered in time, a moment before the scan is left alone: E8).

For every open, classified dispute:
  - a deadline already MISSED (the first response not sent by its date, or sent after it; the
    resolution still open past its date) -> Escalated: the least-loaded FTM (an admin when there is
    none) takes it over, the deadlines stay as they were (the clock is inherited), the admins and the
    new owner are notified, and the customer is sent a status update. Holds on payments are not
    touched: escalation preserves them.
  - a deadline falling due within DISPUTE_WARNING_WORKING_DAYS working days -> one DeadlineWarning to
    the current owner per deadline.

A dispute still in the classify queue (no category, nobody working it) is not escalated here: it
shows as overdue on the dashboard and in the list.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from config.settings import get_settings
from constants.constants import DISPUTE_WARNING_WORKING_DAYS
from core.services import notifications
from core.services.disputes import assignment, calendar, outbound
from core.services.mail import smtp
from data.clients.db import session_scope
from data.models.postgres import Dispute
from data.models.postgres.enums import DisputeMessageKind, DisputeStatus, NotificationType
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import invoices_repo
from prompts.disputes import status_update

logger = logging.getLogger(__name__)

FIRST_RESPONSE = "first response"
RESOLUTION = "resolution"


@dataclass(frozen=True)
class Deadline:
    kind: str  # FIRST_RESPONSE or RESOLUTION
    due: date
    late: bool = False  # the first response WAS sent, but after its date


# ---------------------------------------------------------------- the rules (pure)
def days_before(day: date, working_days: int) -> date:
    for _ in range(working_days):
        day = calendar.previous_working_day(day)
    return day


def missed_deadlines(d: Any, today: date) -> list[Deadline]:
    """Deadlines this dispute has missed as of `today` (a resolved dispute has none)."""
    if d.status is DisputeStatus.RESOLVED:
        return []
    found: list[Deadline] = []
    if d.first_response_due is not None and calendar.missed(d.first_response_due, d.first_response_at, today):
        found.append(Deadline(FIRST_RESPONSE, d.first_response_due, late=d.first_response_at is not None))
    if d.resolution_due is not None and today > d.resolution_due:
        found.append(Deadline(RESOLUTION, d.resolution_due))
    return found


def approaching_deadlines(d: Any, today: date) -> list[Deadline]:
    """Deadlines not yet missed that fall due within the warning window: from the working day
    before the date up to and including the date."""
    if d.status is DisputeStatus.RESOLVED:
        return []
    found: list[Deadline] = []
    if (
        d.first_response_due is not None
        and d.first_response_at is None
        and days_before(d.first_response_due, DISPUTE_WARNING_WORKING_DAYS) <= today <= d.first_response_due
    ):
        found.append(Deadline(FIRST_RESPONSE, d.first_response_due))
    if (
        d.resolution_due is not None
        and days_before(d.resolution_due, DISPUTE_WARNING_WORKING_DAYS) <= today <= d.resolution_due
    ):
        found.append(Deadline(RESOLUTION, d.resolution_due))
    return found


def describe_missed(d: Deadline) -> str:
    if d.kind == FIRST_RESPONSE:
        how = "was sent late" if d.late else "has not been sent"
        return f"The first response was due on {d.due.isoformat()} and {how}"
    return f"The resolution was due on {d.due.isoformat()} and the dispute is still open"


# ---------------------------------------------------------------- the scan
@dataclass
class ScanResult:
    looked_at: int = 0
    escalated: list[dict] = field(default_factory=list)
    warned: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "looked_at": self.looked_at,
            "escalated": self.escalated,
            "warned": self.warned,
        }


async def scan(now: datetime | None = None) -> ScanResult:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    today = calendar.to_ist_date(now)
    result = ScanResult()
    async with session_scope() as db:
        ids = await disputes_repo.scan_candidates(db)
        await db.rollback()
    result.looked_at = len(ids)
    for dispute_id in ids:
        try:
            await _process(dispute_id, now, today, result)
        except Exception:  # one bad dispute must not stop the rest; the next scan tries it again
            logger.exception("deadline scan failed for dispute %s", dispute_id)
    return result


async def run_scheduled(now: datetime) -> dict[str, Any]:
    """The scheduler's entry point."""
    return (await scan(now)).as_dict()


async def _process(dispute_id, now: datetime, today: date, result: ScanResult) -> None:  # noqa: ANN001
    escalation: dict | None = None
    async with session_scope() as db:
        dispute = await disputes_repo.lock_dispute(db, dispute_id)
        if dispute is None or dispute.status not in (
            DisputeStatus.CATEGORISED, DisputeStatus.ASSIGNED, DisputeStatus.CLARIFICATION_SENT, DisputeStatus.ESCALATED
        ):
            await db.rollback()  # resolved (or gone) since the list was read: leave it
            return
        missed = missed_deadlines(dispute, today)
        label = await assignment.label(db, dispute)
        if missed and dispute.status is not DisputeStatus.ESCALATED:
            escalation = await _escalate(db, dispute, missed, label, now)
        # a warning for a deadline that is close but not yet missed (also on an escalated dispute)
        missed_kinds = {m.kind for m in missed}
        owner_id = dispute.assigned_to
        for upcoming in approaching_deadlines(dispute, today):
            if upcoming.kind in missed_kinds or owner_id is None:
                continue
            if await notifications.already_told(
                db, owner_id, NotificationType.DEADLINE_WARNING, "dispute", dispute.id, marker=upcoming.kind
            ):
                continue
            await notifications.create(
                db,
                owner_id,
                NotificationType.DEADLINE_WARNING,
                f"The {upcoming.kind} deadline of dispute {label} is {upcoming.due.isoformat()}"
                + (" (today)" if upcoming.due == today else ""),
                entity_type="dispute",
                entity_id=dispute.id,
            )
            result.warned.append(
                {"dispute_id": str(dispute.id), "to": str(owner_id), "deadline": upcoming.kind, "due": upcoming.due.isoformat()}
            )
        await db.commit()
    if escalation is not None:
        escalation["customer_update"] = await _tell_customer(dispute_id)
        result.escalated.append(escalation)


async def _escalate(db, dispute: Dispute, missed: list[Deadline], label: str, now: datetime) -> dict:  # noqa: ANN001
    reason = "; ".join(describe_missed(m) for m in missed)
    owner = await assignment.pick_owner(db, escalated=True)
    before = dispute.status
    dispute.status = DisputeStatus.ESCALATED
    dispute.escalated_at = now
    if owner is not None:
        dispute.assigned_to = owner.id
    assignment.log_state(
        db, dispute, before, f"Escalated: {reason}" + (f"; taken over by {owner.username}" if owner else ""), scheduler=True
    )
    if owner is not None:
        await notifications.create(
            db, owner.id, NotificationType.ESCALATION,
            f"Dispute {label} was escalated to you. {reason}.",
            entity_type="dispute", entity_id=dispute.id,
        )
    for admin in await disputes_repo.active_users_of_role(db, "ADM"):
        if owner is not None and admin.id == owner.id:
            continue
        await notifications.create(
            db, admin.id, NotificationType.ESCALATION,
            f"Dispute {label} was escalated" + (f" to {owner.username}" if owner else "") + f". {reason}.",
            entity_type="dispute", entity_id=dispute.id,
        )
    return {
        "dispute_id": str(dispute.id),
        "owner_id": str(owner.id) if owner else None,
        "owner": owner.username if owner else None,
        "reason": reason,
    }


async def _tell_customer(dispute_id) -> str:  # noqa: ANN001
    """The status update. Never fails the escalation: a problem is reported to the new owner."""
    async with session_scope() as db:
        dispute = await disputes_repo.get_dispute(db, dispute_id)
        to = await outbound.recipient_for(db, dispute)
        if to is None:
            return "no_address"
        invoice_number = None
        if dispute.invoice_id is not None:
            invoice = await invoices_repo.get_invoice(db, dispute.invoice_id)
            invoice_number = invoice.invoice_number if invoice else None
        subject, text = status_update.render(invoice_number, get_settings().our_company_name)
        subject = outbound.subject_with_reference(subject, outbound.reference_for(dispute.id))
        mail = smtp.build_message(to=to, subject=subject, body=text, sender=get_settings().smtp_from)
        try:
            sent = await smtp.send(mail)
        except smtp.SmtpError as exc:
            if dispute.assigned_to is not None:
                await notifications.create(
                    db, dispute.assigned_to, NotificationType.SEND_FAILURE,
                    f"Could not send the customer the escalation update for dispute "
                    f"{await assignment.label(db, dispute)}: {exc.message}",
                    entity_type="dispute", entity_id=dispute.id,
                )
            await db.commit()
            return "failed"
        await disputes_repo.insert_message(
            db,
            dispute_id=dispute.id,
            kind=DisputeMessageKind.SENT,
            body=outbound.compose(subject, text),
            sent_at=datetime.now(UTC),
            gmail_message_id=sent.message_id,
        )
        await db.commit()
        return "sent"
