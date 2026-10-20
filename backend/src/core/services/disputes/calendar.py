"""Working-day arithmetic for dispute deadlines (E5). Pure, no I/O.

Indian working days: Monday to Friday, minus the fixed holidays in constants.HOLIDAYS_FIXED.
Deadlines are DATES in IST. The clock starts on the next working day after the day the complaint
arrived, so "2 working days" from a Monday is Wednesday, from a Saturday is Tuesday.
A deadline is met when `date(event_at in IST) <= due_date`.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from constants.constants import (
    BUSINESS_TIMEZONE,
    DISPUTE_FIRST_RESPONSE_WORKING_DAYS,
    DISPUTE_RESOLUTION_WORKING_DAYS,
    HOLIDAYS_FIXED,
)

IST = ZoneInfo(BUSINESS_TIMEZONE)


def to_ist_date(moment: datetime) -> date:
    """The IST calendar date of a moment (a naive datetime is taken as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST).date()


def today_ist() -> date:
    return datetime.now(IST).date()


def is_holiday(day: date) -> bool:
    return (day.month, day.day) in HOLIDAYS_FIXED


def is_working_day(day: date) -> bool:
    return day.weekday() < 5 and not is_holiday(day)


def add_working_days(start: date, days: int) -> date:
    """The date `days` working days after `start` (day 1 is the next working day)."""
    if days < 0:
        raise ValueError("days must not be negative")
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if is_working_day(current):
            remaining -= 1
    return current


def previous_working_day(day: date) -> date:
    """The working day before `day`."""
    current = day - timedelta(days=1)
    while not is_working_day(current):
        current -= timedelta(days=1)
    return current


def first_response_due(received_at: datetime) -> date:
    return add_working_days(to_ist_date(received_at), DISPUTE_FIRST_RESPONSE_WORKING_DAYS)


def resolution_due(received_at: datetime) -> date:
    return add_working_days(to_ist_date(received_at), DISPUTE_RESOLUTION_WORKING_DAYS)


def met(due: date, done_at: datetime) -> bool:
    """Was something done in time? Compared as the IST date of the moment it happened."""
    return to_ist_date(done_at) <= due


def missed(due: date | None, done_at: datetime | None, today: date) -> bool:
    """A deadline is missed when it was done late, or it is still not done after its date."""
    if due is None:
        return False
    if done_at is not None:
        return not met(due, done_at)
    return today > due
