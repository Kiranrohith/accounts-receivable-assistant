"""The reminder rules: tiers, the weekend shift, catch-up (only the highest tier), skips, the 60-day handoff and
what suppresses a reminder."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from core.services.reminders import rules
from data.models.postgres.enums import InvoiceStatus

MON = date(2026, 10, 19)  # a Monday


def test_the_tiers_are_one_fifteen_and_thirty_days_after_the_due_date():
    t = rules.tiers_for(MON)
    assert [x.tier for x in t] == [1, 15, 30]
    assert [x.threshold for x in t] == [MON + timedelta(days=1), MON + timedelta(days=15), MON + timedelta(days=30)]


def test_a_tier_on_a_weekend_moves_to_monday_and_remembers_the_shift():
    sat_due = date(2026, 10, 9)  # Friday: +1 is Saturday 10th, +15 is Saturday 24th, +30 is Sunday Nov 8
    by_tier = {t.tier: t for t in rules.tiers_for(sat_due)}
    assert by_tier[1].threshold == date(2026, 10, 10) and by_tier[1].scheduled == date(2026, 10, 12) and by_tier[1].shift_days == 2
    assert by_tier[15].scheduled == date(2026, 10, 26) and by_tier[15].shift_days == 2
    assert by_tier[30].threshold == date(2026, 11, 8) and by_tier[30].scheduled == date(2026, 11, 9) and by_tier[30].shift_days == 1


def test_a_tier_on_a_weekday_is_not_shifted():
    t = rules.tiers_for(MON)[0]
    assert t.scheduled == t.threshold and t.shift_days == 0


def test_days_overdue_counts_from_the_due_date():
    assert rules.days_overdue(MON, MON) == 0 and rules.days_overdue(MON, MON + timedelta(days=1)) == 1


def test_nothing_is_due_before_the_first_tier():
    assert rules.plan(MON, MON, {}) == rules.Plan()
    assert rules.reached(MON, MON) == []


def test_the_first_day_overdue_drafts_the_gentle_reminder():
    p = rules.plan(MON, MON + timedelta(days=1), {})
    assert p.draft and p.draft.tier == 1 and not p.skipped and not p.handoff


def test_an_invoice_first_seen_eighteen_days_late_gets_only_the_firm_reminder_and_tier_one_is_skipped():
    p = rules.plan(MON, MON + timedelta(days=18), {})
    assert p.draft.tier == 15 and [t.tier for t in p.skipped] == [1]


def test_a_thirty_five_day_old_invoice_gets_the_final_reminder_and_both_lower_tiers_are_skipped():
    p = rules.plan(MON, MON + timedelta(days=35), {})
    assert p.draft.tier == 30 and [t.tier for t in p.skipped] == [1, 15]


def test_a_tier_with_a_draft_or_a_sent_mail_is_not_drafted_again():
    today = MON + timedelta(days=16)
    assert rules.plan(MON, today, {15: {"Drafted"}, 1: {"Sent"}}).draft is None
    assert rules.plan(MON, today, {15: {"Sent"}, 1: {"Sent"}}).draft is None
    assert rules.plan(MON, today, {15: {"Approved"}, 1: {"Sent"}}).draft is None


def test_a_cancelled_or_rejected_tier_can_be_drafted_again_after_the_cause_is_over():
    today = MON + timedelta(days=16)
    assert rules.plan(MON, today, {15: {"AutoCancelled"}, 1: {"Sent"}}).draft.tier == 15
    assert rules.plan(MON, today, {15: {"Rejected"}, 1: {"Sent"}}).draft.tier == 15


def test_a_skip_is_written_only_when_the_tier_has_no_row_of_any_kind():
    today = MON + timedelta(days=16)
    assert [t.tier for t in rules.plan(MON, today, {}).skipped] == [1]
    for state in ("Sent", "Drafted", "AutoCancelled", "Rejected", "SkippedPastThreshold"):
        assert rules.plan(MON, today, {1: {state}}).skipped == [], state


def test_a_weekend_tier_waits_for_monday_and_then_the_count_is_the_true_one():
    due = date(2026, 10, 9)  # tier 1 falls on Saturday the 10th
    assert rules.plan(due, date(2026, 10, 10), {}).draft is None  # Saturday
    assert rules.plan(due, date(2026, 10, 11), {}).draft is None  # Sunday
    monday = rules.plan(due, date(2026, 10, 12), {})
    assert monday.draft.tier == 1 and monday.draft.shift_days == 2
    assert rules.days_overdue(due, date(2026, 10, 12)) == 3


def test_at_sixty_days_the_reminders_stop_and_the_invoice_is_handed_over():
    p = rules.plan(MON, MON + timedelta(days=60), {30: {"Sent"}})
    assert p.handoff and p.draft is None and p.skipped == []
    assert not rules.plan(MON, MON + timedelta(days=59), {30: {"Sent"}}).handoff


def test_an_invoice_loaded_already_ninety_days_late_is_handed_over_without_reminders():
    p = rules.plan(MON, MON + timedelta(days=90), {})
    assert p.handoff and p.draft is None and p.skipped == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (dict(status=InvoiceStatus.PAID, balance=Decimal("0")), "paid"),
        (dict(status=InvoiceStatus.OPEN, balance=Decimal("0.00")), "paid"),
        (dict(status=InvoiceStatus.OPEN, balance=Decimal("10"), is_disputed=True), "dispute"),
        (dict(status=InvoiceStatus.PARTLY_PAID, balance=Decimal("10"), po_hold=True), "purchase order"),
        (dict(status=InvoiceStatus.OPEN, balance=Decimal("10"), holds_unapplied_cash=True), "not applied"),
    ],
)
def test_what_suppresses_a_reminder(kwargs, reason):
    base = dict(is_disputed=False, po_hold=False, holds_unapplied_cash=False)
    assert reason in rules.suppression_reason(**{**base, **kwargs})


def test_an_open_and_a_partly_paid_invoice_with_a_balance_are_chased():
    base = dict(is_disputed=False, po_hold=False, holds_unapplied_cash=False)
    assert rules.suppression_reason(status=InvoiceStatus.OPEN, balance=Decimal("1"), **base) is None
    assert rules.suppression_reason(status=InvoiceStatus.PARTLY_PAID, balance=Decimal("1"), **base) is None
