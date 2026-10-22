"""When an invoice is chased: the reminder tiers, the weekend shift and the 60-day handoff. Pure rules, no I/O.

Days overdue count from the DUE date (due date + 1 is one day overdue) and never restart: a part payment lowers the
balance quoted, not the count. Three tiers (1, 15, 30 days) escalate in tone; at 60 days the reminders stop and a
person takes the invoice over.

The scan is a CATCH-UP scan, not an event: each morning it looks at where every invoice stands today.
  - only the HIGHEST tier reached is drafted (an invoice first seen 18 days past due gets the firm reminder, not two
    mails in one morning); a lower tier that was never written down is recorded as SkippedPastThreshold
  - a tier whose day falls on a weekend moves to the next weekday (reminders do not wait for holidays: those are for
    dispute deadlines only); the days it was pushed are kept, and the count quoted to the customer is the true one
  - nothing is ever drafted twice for one tier while a draft or a sent mail for it exists
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from constants.constants import COLLECTION_HANDOFF_DAYS, REMINDER_TIERS
from data.models.postgres.enums import InvoiceStatus

WEEKDAYS = 5  # Monday..Friday


@dataclass(frozen=True)
class Tier:
    tier: int  # 1 / 15 / 30
    threshold: date  # due date + tier: the day it was crossed
    scheduled: date  # the day it is drafted: the threshold, or the next weekday
    shift_days: int  # how many days the weekend pushed it

    @property
    def label(self) -> str:
        return f"day {self.tier}"


@dataclass
class Plan:
    draft: Tier | None = None  # the reminder to write now
    skipped: list[Tier] = field(default_factory=list)  # lower tiers to record as SkippedPastThreshold
    handoff: bool = False  # 60 days: stop reminding, hand the invoice to a person


def days_overdue(due: date, today: date) -> int:
    return (today - due).days


def next_weekday(day: date) -> date:
    while day.weekday() >= WEEKDAYS:
        day += timedelta(days=1)
    return day


def tiers_for(due: date) -> list[Tier]:
    out = []
    for tier in REMINDER_TIERS:
        threshold = due + timedelta(days=tier)
        scheduled = next_weekday(threshold)
        out.append(Tier(tier, threshold, scheduled, (scheduled - threshold).days))
    return out


def reached(due: date, today: date) -> list[Tier]:
    """The tiers whose day has come (weekend shift applied), lowest first."""
    return [t for t in tiers_for(due) if t.scheduled <= today]


def plan(due: date, today: date, rows: dict[int, set[str]]) -> Plan:
    """What to do for one eligible invoice today. `rows` maps a tier to the states of the rows that already exist for
    it (every state counts for a skip; "active" ones, i.e. not AutoCancelled / Rejected, block a new draft)."""
    out = Plan()
    if days_overdue(due, today) >= COLLECTION_HANDOFF_DAYS:
        out.handoff = True  # reminders stop here: no more drafts at all
        return out
    crossed = reached(due, today)
    if not crossed:
        return out
    highest = crossed[-1]
    active = {t for t, states in rows.items() if states - {"AutoCancelled", "Rejected"}}
    if highest.tier not in active:
        out.draft = highest
    out.skipped = [t for t in crossed[:-1] if t.tier not in rows]
    return out


def suppression_reason(
    *, status: InvoiceStatus, balance: Any, is_disputed: bool, po_hold: bool, holds_unapplied_cash: bool
) -> str | None:
    """Why an invoice must not be chased right now, or None. The same test decides whether to draft, whether a draft
    still stands, and whether an approval may go out."""
    if status is InvoiceStatus.PAID or balance is None or balance <= 0:
        return "the invoice is paid"
    if is_disputed:
        return "the invoice is in dispute"
    if po_hold:
        return "the invoice is held for a purchase order mismatch"
    if holds_unapplied_cash:
        return "the customer has payments we hold that are not applied yet"
    return None
