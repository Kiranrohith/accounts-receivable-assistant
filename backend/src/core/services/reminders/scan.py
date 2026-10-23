"""The morning scan: who is chased today.

Run once a day (09:00 IST) by the scheduler, or on demand by an admin. It is IDEMPOTENT and CRASH-SAFE (a second run the
same day changes nothing: the (invoice, tier) unique index and the rules in `rules.plan` see to that), and each invoice
is handled in its own transaction, so one bad row never stops the rest.

  1. clear out: a draft waiting for a person whose invoice has since been paid, put in dispute, or whose customer now
     holds unapplied cash is cancelled; a collection task whose invoice was paid closes
  2. for every Open / PartlyPaid invoice at least a day overdue that may be chased (not disputed, not on a purchase
     order hold, customer without unapplied cash): write the reminder due today (only the highest tier reached), record
     the lower tiers it skipped, or, at 60 days, hand the invoice to a person
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import BUSINESS_TIMEZONE
from core.services.reminders import drafts, handoff, lifecycle, rules
from data.clients.db import session_scope
from data.models.postgres import Invoice
from data.models.postgres.enums import ReminderState
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.reminders import reminders_repo

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    drafted: int = 0
    skipped_tiers: int = 0
    handed_off: int = 0
    cancelled: int = 0
    tasks_closed: int = 0
    suppressed: int = 0  # invoices a reminder would have been due for, but must not be chased now
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "drafted": self.drafted,
            "skipped_tiers": self.skipped_tiers,
            "handed_off": self.handed_off,
            "cancelled": self.cancelled,
            "tasks_closed": self.tasks_closed,
            "suppressed": self.suppressed,
            "failed": self.failed,
            "errors": self.errors,
        }


def today_ist(now: datetime) -> date:
    return now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()


async def reason_to_hold_back(db: AsyncSession, invoice: Invoice, cash_holders: set | None = None) -> str | None:
    """Why this invoice must not be chased right now (None: it may be)."""
    holders = cash_holders if cash_holders is not None else await reminders_repo.customers_holding_cash(db, [invoice.customer_id])
    return rules.suppression_reason(
        status=invoice.status,
        balance=invoice.collectible_balance,
        is_disputed=invoice.is_disputed,
        po_hold=invoice.po_hold,
        holds_unapplied_cash=invoice.customer_id in holders,
    )


async def scan(now: datetime | None = None) -> ScanResult:
    now = now or datetime.now(UTC)
    today = today_ist(now)
    result = ScanResult()
    await _clear_out(now, result)
    async with session_scope() as db:
        invoice_ids = await reminders_repo.chaseable_invoice_ids(db, today)
    for invoice_id in invoice_ids:
        try:
            await _process(invoice_id, today, result)
        except Exception as exc:  # one invoice never stops the rest
            logger.exception("reminder scan failed for invoice %s", invoice_id)
            result.failed += 1
            result.errors.append(f"{invoice_id}: {exc}")
    logger.info("reminder scan %s: %s", today, result.as_dict())
    return result


async def run_scheduled(now: datetime) -> dict[str, Any]:
    """The scheduler's entry point."""
    return (await scan(now)).as_dict()


async def _clear_out(now: datetime, result: ScanResult) -> None:
    async with session_scope() as db:
        pending = await reminders_repo.pending_with_invoices(db)
        holders = await reminders_repo.customers_holding_cash(db, list({c.id for _, _, c in pending}))
        for reminder, invoice, _ in pending:
            reason = await reason_to_hold_back(db, invoice, holders)
            if reason and reminder.state in (ReminderState.DRAFTED, ReminderState.APPROVED):
                before = reminder.state
                reminder.state = ReminderState.AUTO_CANCELLED
                reminder.cancel_reason = reason
                lifecycle.log_reminder(db, reminder, before, f"Cancelled by the scan: {reason}", scheduler=True)
                lifecycle.timeline(db, invoice, f"Reminder draft (day {reminder.reminder_tier}) cancelled: {reason}", scheduler=True)
                result.cancelled += 1
        for task in await reminders_repo.open_tasks_of_paid_invoices(db):
            invoice = await invoices_repo.get_invoice(db, task.invoice_id)
            await lifecycle.close_task(db, task, lifecycle.TaskClosedReason.PAID, scheduler=True)
            if invoice is not None:
                lifecycle.timeline(db, invoice, "Collection task closed: the invoice was paid", scheduler=True)
            result.tasks_closed += 1
        await unit_of_work.commit(db)


async def _process(invoice_id, today: date, result: ScanResult) -> None:  # noqa: ANN001
    async with session_scope() as db:
        invoice = await invoices_repo.get_invoice(db, invoice_id)
        if invoice is None:
            return
        customer = await customers_repo.get_by_id(db, invoice.customer_id)
        if customer is None:
            return
        if await reason_to_hold_back(db, invoice):
            result.suppressed += 1
            await db.rollback()
            return
        rows: dict[int, set[str]] = {}
        for existing in await reminders_repo.rows_for_invoice(db, invoice.id):
            rows.setdefault(existing.reminder_tier, set()).add(existing.state.value)
        plan = rules.plan(invoice.due_date, today, rows)

        if plan.handoff:
            if await handoff.hand_off(db, invoice, customer, today) is not None:
                result.handed_off += 1
            await unit_of_work.commit(db)
            return
        for tier in plan.skipped:
            reminder = await reminders_repo.insert_reminder(
                db, invoice_id=invoice.id, reminder_tier=tier.tier, scheduled_date=tier.threshold,
                weekend_shift_days=tier.shift_days, state=ReminderState.SKIPPED_PAST_THRESHOLD,
                cancel_reason="the invoice was already past this tier when the scan first saw it",
            )
            lifecycle.log_reminder(db, reminder, None, f"Day {tier.tier} skipped: already past it when first seen", scheduler=True)
            result.skipped_tiers += 1
        if plan.draft is not None:
            tier = plan.draft
            try:
                async with db.begin_nested():  # a scan that ran at the same moment loses to the unique index, quietly
                    reminder = await reminders_repo.insert_reminder(
                        db, invoice_id=invoice.id, reminder_tier=tier.tier, scheduled_date=tier.threshold,
                        weekend_shift_days=tier.shift_days, state=ReminderState.DRAFTED,
                        draft_body=drafts.compose(invoice, customer, tier.tier),
                    )
            except IntegrityError:
                await unit_of_work.commit(db)
                return
            shifted = f", moved {tier.shift_days} day(s) by the weekend" if tier.shift_days else ""
            lifecycle.log_reminder(
                db, reminder, None,
                f"Drafted: {rules.days_overdue(invoice.due_date, today)} days overdue, day {tier.tier} reminder{shifted}",
                scheduler=True,
            )
            lifecycle.timeline(db, invoice, f"Reminder drafted (day {tier.tier}), waiting for a finance associate", scheduler=True)
            result.drafted += 1
        await unit_of_work.commit(db)
