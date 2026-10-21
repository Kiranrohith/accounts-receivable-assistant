from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from core.services.disputes import deadlines as dl
from core.services.jobs.scheduler import Schedule, is_due
from data.models.postgres.enums import DisputeStatus

# Monday 2026-10-19 received: first response due Wed 21 Oct, resolution due Tue 3 Nov
# (2 and 10 Indian working days; Fri 2 Oct was a holiday but is before this).


def dispute(status=DisputeStatus.ASSIGNED, first_at=None, first_due=date(2026, 10, 21), res_due=date(2026, 11, 3)):
    return SimpleNamespace(
        status=status,
        first_response_due=first_due,
        first_response_at=first_at,
        resolution_due=res_due,
    )


def kinds(found):
    return [(d.kind, d.late) for d in found]


def test_nothing_is_missed_on_the_due_day_itself():
    assert dl.missed_deadlines(dispute(), date(2026, 10, 21)) == []


def test_the_first_response_is_missed_the_day_after_it_was_due():
    assert kinds(dl.missed_deadlines(dispute(), date(2026, 10, 22))) == [("first response", False)]


def test_a_response_sent_in_time_misses_nothing_a_late_one_does():
    on_time = datetime(2026, 10, 21, 18, 29, tzinfo=UTC)  # 23:59 IST on the due day: still in time
    late = datetime(2026, 10, 21, 18, 31, tzinfo=UTC)  # 00:01 IST the next day
    assert dl.missed_deadlines(dispute(first_at=on_time), date(2026, 10, 22)) == []
    assert kinds(dl.missed_deadlines(dispute(first_at=late), date(2026, 10, 22))) == [("first response", True)]


def test_both_deadlines_can_be_missed_together():
    assert [d.kind for d in dl.missed_deadlines(dispute(), date(2026, 11, 4))] == ["first response", "resolution"]
    answered = dispute(first_at=datetime(2026, 10, 20, 5, 0, tzinfo=UTC))
    assert [d.kind for d in dl.missed_deadlines(answered, date(2026, 11, 4))] == ["resolution"]


def test_a_resolved_dispute_has_missed_nothing():
    assert dl.missed_deadlines(dispute(status=DisputeStatus.RESOLVED), date(2027, 1, 1)) == []
    assert dl.approaching_deadlines(dispute(status=DisputeStatus.RESOLVED), date(2026, 10, 21)) == []


def test_the_warning_window_is_the_working_day_before_up_to_the_due_day():
    d = dispute()
    assert dl.approaching_deadlines(d, date(2026, 10, 19)) == []  # two working days out
    assert [x.kind for x in dl.approaching_deadlines(d, date(2026, 10, 20))] == ["first response"]
    assert [x.kind for x in dl.approaching_deadlines(d, date(2026, 10, 21))] == ["first response"]
    assert dl.approaching_deadlines(d, date(2026, 10, 22)) == []  # missed, no longer a warning


def test_the_working_day_before_skips_weekends():
    # due Monday 26 Oct: the day before is Friday 23 Oct, and the weekend is not in the window
    d = dispute(first_due=date(2026, 10, 26), res_due=date(2026, 12, 1))
    assert [x.kind for x in dl.approaching_deadlines(d, date(2026, 10, 23))] == ["first response"]
    assert dl.approaching_deadlines(d, date(2026, 10, 22)) == []
    assert dl.days_before(date(2026, 10, 26), 1) == date(2026, 10, 23)


def test_an_answered_first_response_needs_no_warning_but_the_resolution_still_does():
    d = dispute(first_at=datetime(2026, 10, 20, 5, 0, tzinfo=UTC))
    assert dl.approaching_deadlines(d, date(2026, 10, 21)) == []
    assert [x.kind for x in dl.approaching_deadlines(d, date(2026, 11, 2))] == ["resolution"]


def test_the_reason_reads_plainly():
    first = dl.Deadline("first response", date(2026, 10, 21))
    assert dl.describe_missed(first) == "The first response was due on 2026-10-21 and has not been sent"
    assert dl.describe_missed(dl.Deadline("first response", date(2026, 10, 21), late=True)).endswith("was sent late")
    assert "still open" in dl.describe_missed(dl.Deadline("resolution", date(2026, 11, 3)))


# ---- when a schedule is due ------------------------------------------------------------------------------
DAILY = Schedule("daily_ist", at=time(9, 15))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ist(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def test_a_new_daily_schedule_waits_for_the_scan_time_then_fires_once():
    assert not is_due(DAILY, EPOCH, ist(2026, 10, 21, 9, 14))
    assert is_due(DAILY, EPOCH, ist(2026, 10, 21, 9, 15))
    assert is_due(DAILY, EPOCH, ist(2026, 10, 21, 23, 59))


def test_a_daily_schedule_that_ran_today_is_not_due_until_tomorrow_morning():
    ran = ist(2026, 10, 21, 9, 16)
    assert not is_due(DAILY, ran, ist(2026, 10, 21, 18, 0))
    assert not is_due(DAILY, ran, ist(2026, 10, 22, 9, 14))
    assert is_due(DAILY, ran, ist(2026, 10, 22, 9, 15))


def test_the_ist_day_not_the_utc_day_decides():
    ran = ist(2026, 10, 21, 9, 16)  # 03:46 UTC on the 21st
    late_night_ist = ist(2026, 10, 21, 23, 50)  # 18:20 UTC: still the 21st in IST
    assert not is_due(DAILY, ran, late_night_ist)
    just_after_midnight_ist = ist(2026, 10, 22, 0, 10)  # 18:40 UTC on the 21st, but the 22nd in IST before 09:15
    assert not is_due(DAILY, ran, just_after_midnight_ist)


def test_an_interval_schedule_is_due_after_its_interval():
    every_minute = Schedule("interval", seconds=60)
    ran = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
    assert not is_due(every_minute, ran, ran + timedelta(seconds=59))
    assert is_due(every_minute, ran, ran + timedelta(seconds=60))


@pytest.mark.parametrize("name", ["deadline_scan"])
def test_the_deadline_scan_is_scheduled_daily_at_0915_ist(name):
    from core.services.jobs.scheduler import SCHEDULES

    assert SCHEDULES[name] == Schedule("daily_ist", at=time(9, 15))
