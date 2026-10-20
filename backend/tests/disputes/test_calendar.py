"""Working-day arithmetic for dispute deadlines."""

from datetime import UTC, date, datetime, timedelta

import pytest

from core.services.disputes.calendar import (
    add_working_days,
    first_response_due,
    is_holiday,
    is_working_day,
    met,
    missed,
    previous_working_day,
    resolution_due,
    to_ist_date,
)

D = date
# 2026: 19 Oct is a Monday; 2 Oct (Gandhi Jayanti, Friday), 15 Aug (Saturday), 25 Dec (Friday),
# 26 Jan (Monday) are the fixed holidays.


def test_the_calendar_used_here():
    assert D(2026, 10, 19).weekday() == 0  # Monday
    assert D(2026, 10, 2).weekday() == 4  # Friday


@pytest.mark.parametrize(
    ("day", "working"),
    [
        (D(2026, 10, 19), True),  # Monday
        (D(2026, 10, 23), True),  # Friday
        (D(2026, 10, 24), False),  # Saturday
        (D(2026, 10, 25), False),  # Sunday
        (D(2026, 10, 2), False),  # Gandhi Jayanti, a Friday
        (D(2026, 12, 25), False),  # Christmas, a Friday
        (D(2026, 1, 26), False),  # Republic Day, a Monday
        (D(2026, 8, 15), False),  # Independence Day (a Saturday anyway)
        (D(2027, 1, 26), False),  # holidays repeat every year
    ],
)
def test_working_days(day, working):
    assert is_working_day(day) is working


def test_holidays_are_fixed_dates():
    assert is_holiday(D(2026, 10, 2)) and is_holiday(D(2030, 10, 2))
    assert not is_holiday(D(2026, 10, 3))


@pytest.mark.parametrize(
    ("start", "days", "due"),
    [
        (D(2026, 10, 19), 2, D(2026, 10, 21)),  # Mon + 2 = Wed
        (D(2026, 10, 21), 2, D(2026, 10, 23)),  # Wed + 2 = Fri
        (D(2026, 10, 22), 2, D(2026, 10, 26)),  # Thu + 2 = Mon (over the weekend)
        (D(2026, 10, 23), 2, D(2026, 10, 27)),  # Fri + 2 = Tue
        (D(2026, 10, 24), 2, D(2026, 10, 27)),  # Sat: the clock starts Monday, so + 2 = Tue
        (D(2026, 10, 25), 2, D(2026, 10, 27)),  # Sun likewise
        (D(2026, 10, 25), 1, D(2026, 10, 26)),  # Sun + 1 = Mon
        (D(2026, 10, 19), 0, D(2026, 10, 19)),  # zero days = the same day
        (D(2026, 10, 19), 10, D(2026, 11, 2)),  # two working weeks
        (D(2026, 10, 24), 10, D(2026, 11, 6)),  # from a Saturday
    ],
)
def test_add_working_days(start, days, due):
    assert add_working_days(start, days) == due


def test_a_holiday_is_skipped():
    # Thu 1 Oct + 2: Fri 2 Oct is a holiday, so the days are Mon 5 (1) and Tue 6 (2)
    assert add_working_days(D(2026, 10, 1), 2) == D(2026, 10, 6)
    # Wed 30 Sep + 2: Thu 1 Oct (1), Fri 2 Oct holiday, Mon 5 Oct (2)
    assert add_working_days(D(2026, 9, 30), 2) == D(2026, 10, 5)
    # the 10-day resolution window over a holiday is one day longer than the weekend-only count
    assert add_working_days(D(2026, 9, 30), 10) == D(2026, 10, 15)


def test_a_holiday_on_a_weekend_costs_nothing():
    # 15 Aug 2026 is a Saturday: Fri 14 Aug + 1 = Mon 17 Aug either way
    assert add_working_days(D(2026, 8, 14), 1) == D(2026, 8, 17)


def test_consecutive_holidays_and_a_year_boundary():
    # Thu 24 Dec 2026 + 2: Fri 25 Dec holiday, weekend, Mon 28 (1), Tue 29 (2)
    assert add_working_days(D(2026, 12, 24), 2) == D(2026, 12, 29)
    # Thu 31 Dec 2026 + 1 = Fri 1 Jan 2027 (not a holiday)
    assert add_working_days(D(2026, 12, 31), 1) == D(2027, 1, 1)


def test_negative_days_are_refused():
    with pytest.raises(ValueError):
        add_working_days(D(2026, 10, 19), -1)


def test_previous_working_day():
    assert previous_working_day(D(2026, 10, 20)) == D(2026, 10, 19)  # Tue -> Mon
    assert previous_working_day(D(2026, 10, 19)) == D(2026, 10, 16)  # Mon -> Fri
    assert previous_working_day(D(2026, 10, 5)) == D(2026, 10, 1)  # Mon after a Fri holiday -> Thu


# ---- IST dates ------------------------------------------------------------------------------


def test_the_ist_date_decides_the_day_not_the_utc_date():
    late_utc = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)  # 01:30 IST on Saturday 17 Oct
    assert to_ist_date(late_utc) == D(2026, 10, 17)
    early_utc = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 23:30 IST on Sunday 18 Oct
    assert to_ist_date(early_utc) == D(2026, 10, 18)
    assert to_ist_date(datetime(2026, 10, 18, 18, 30, tzinfo=UTC)) == D(2026, 10, 19)  # midnight IST


def test_a_naive_datetime_is_taken_as_utc():
    assert to_ist_date(datetime(2026, 10, 16, 20, 0)) == D(2026, 10, 17)


def test_deadlines_from_an_arrival_moment():
    received = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # Monday 09:30 IST
    assert first_response_due(received) == D(2026, 10, 21)  # Wed
    assert resolution_due(received) == D(2026, 11, 2)  # 10 working days later
    saturday_in_ist = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)  # still Friday in UTC
    assert first_response_due(saturday_in_ist) == D(2026, 10, 20)  # Sat + 2 = Tue


# ---- met / missed ---------------------------------------------------------------------------


def test_met_compares_the_ist_date():
    due = D(2026, 10, 21)
    assert met(due, datetime(2026, 10, 21, 18, 29, tzinfo=UTC))  # 23:59 IST on the due day: in time
    assert not met(due, datetime(2026, 10, 21, 18, 30, tzinfo=UTC))  # midnight IST: next day, late
    assert met(due, datetime(2026, 10, 19, 9, 0, tzinfo=UTC))


def test_missed():
    due = D(2026, 10, 21)
    assert not missed(None, None, D(2030, 1, 1))  # no deadline set
    assert not missed(due, None, D(2026, 10, 21))  # the due day itself is still in time
    assert missed(due, None, D(2026, 10, 22))  # not done, date passed
    assert not missed(due, datetime(2026, 10, 20, 5, 0, tzinfo=UTC), D(2026, 12, 1))  # done in time
    assert missed(due, datetime(2026, 10, 22, 5, 0, tzinfo=UTC), D(2026, 10, 22))  # done late


def test_the_ten_day_window_is_always_after_the_two_day_window():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for offset in range(0, 400, 7):
        received = start + timedelta(days=offset)
        assert resolution_due(received) > first_response_due(received)
