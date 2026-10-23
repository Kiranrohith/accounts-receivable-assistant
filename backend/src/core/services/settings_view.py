"""A read-only view of the business constants for the Settings screen.

The values are read from `constants.constants` every time, so the screen can never disagree with the running code. The
labels and descriptions live here; a constant that is not listed is not shown (secrets and plumbing stay out).
"""

from datetime import time
from decimal import Decimal
from typing import Any

from constants import constants as c
from schemas.settings.settings import SettingItem, SettingsOut, SettingsSection

NOTE = "Constants are code-managed in this version: editing will arrive with DB-backed configuration."

# (constant, label, description) per section, in the order the screen shows them.
_SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    ("matching", "Matching and confidence", (
        ("CONFIDENCE_AUTO_APPLY", "Automatic match from", "A payment match scoring at least this is applied without a person."),
        ("TIE_MARGIN", "Tie margin", "Two matches closer than this are a tie and go to a person."),
        ("TRIAGE_HUMAN_THRESHOLD", "Mail triage: person below", "A mail the reader is less sure of than this waits for a person to route it."),
        ("EXTRACTION_CONFIDENCE_MIN", "Extraction: person below", "A document read with less confidence than this is sent for review."),
        ("CLARIFICATION_AUTO_SEND", "Clarification: auto-send from", "A first clarification e-mail may go out by itself at this confidence or above."),
        ("LAYER_BASE_SCORE", "Base score per matching layer", "L1 invoice number, L2 sender e-mail, L3 payer name."),
        ("AMOUNT_FIT_SCORE", "Score for how well the amount fits", "T1 rounding, T2 exchange-rate band, otherwise over or short."),
        ("DUE_DATE_MODIFIER_WINDOW_DAYS", "Due-soon window", "An invoice due within this many days scores slightly higher."),
        ("MAX_SUBSET_INVOICES", "Most invoices combined", "More open invoices than this and the payment goes to review instead of a long search."),
    )),
    ("tolerances", "Tolerances, tax and exchange rates", (
        ("TDS_RATE", "TDS rate", "Taken off what we expect to collect from a customer that deducts TDS. Debit notes skip it."),
        ("ROUNDING_TOLERANCE", "Arithmetic rounding", "How far a document's own totals may be off, in whole currency units."),
        ("TOLERANCE_T1_INR", "INR payment tolerance (T1)", "A payment this close to an invoice's balance counts as paying it, either way."),
        ("FX_BAND_PCT", "Foreign payment band (T2), %", "A same-currency foreign payment within this percentage of the balance counts as paying it."),
        ("FX_LOOKBACK_DAYS", "Exchange-rate look-back", "On a weekend or holiday the latest rate published before the date is used, up to this many days back."),
        ("FX_MAX_STALE_DAYS", "Exchange rate too old after", "A rate older than this is not used automatically; the invoice goes to review."),
        ("CREDIT_NOTE_PREFIX", "Credit note prefix", "Note numbers read CN-year-number."),
    )),
    ("reminders", "Reminders and collection", (
        ("REMINDER_TIERS", "Reminder tiers (days overdue)", "Gentle, firm and final."),
        ("COLLECTION_HANDOFF_DAYS", "Hand to a person after", "Reminders stop and a collection task is created this many days past due."),
        ("COLLECTION_ROLE", "Collection tasks go to", "The role that gets the tasks; an admin when nobody is active."),
    )),
    ("disputes", "Disputes", (
        ("DISPUTE_FIRST_RESPONSE_WORKING_DAYS", "First response within", "Working days from when the complaint is received."),
        ("DISPUTE_RESOLUTION_WORKING_DAYS", "Resolution within", "Working days from when the complaint is received."),
        ("DISPUTE_WARNING_WORKING_DAYS", "Warn before a deadline", "Working days of warning before a due date."),
        ("DISPUTE_CLASSIFY_THRESHOLD", "Category: person below", "A category the reader is less sure of than this waits in the classify queue."),
        ("CLARIFICATION_AUTO_SEND_CONFIDENCE", "Auto-send a clarification from", "Also needs a draft grounded in our own data."),
        ("HOLIDAYS_FIXED", "Fixed holidays (month-day)", "Not counted as working days, every year. Weekends never are."),
        ("DISPUTE_OWNER_ROLE", "Disputes go to", "The least-loaded active user of this role."),
        ("DISPUTE_ESCALATION_ROLE", "Escalated disputes go to", "The least-loaded active user of this role."),
        ("DISPUTE_FALLBACK_ROLE", "When nobody is active", "The role that takes it instead."),
    )),
    ("runner", "Background work", (
        ("REMATCH_CAP", "Re-matches before review", "A payment that cannot settle after this many tries goes to a person."),
        ("LLM_RETRY_DELAYS_SECONDS", "Reader retry delays (s)", "Then the fallback model."),
        ("OTHER_RETRY_DELAYS_SECONDS", "Other retry delays (s)", "For anything else that fails and can be tried again."),
        ("OUTAGE_BACKOFF_SECONDS", "Outage back-off (s)", "When an outside service is down."),
        ("HEARTBEAT_SECONDS", "Worker heartbeat (s)", "How often a running job says it is alive."),
        ("STALE_HEARTBEAT_SECONDS", "Job taken back after (s)", "A job silent for this long is treated as lost and run again."),
        ("MAX_JOB_RUNTIME_MINUTES", "Longest a job may run (min)", "Beyond this a job is failed."),
        ("SMTP_RETRY_DELAYS_SECONDS", "Mail send retry delays (s)", "The first try plus one more per delay."),
    )),
    ("schedules", "Schedules", (
        ("REMINDER_SCAN_TIME_IST", "Reminder scan", "Every day, India time."),
        ("DEADLINE_SCAN_TIME_IST", "Dispute deadline scan", "Every day, India time."),
        ("POLL_INTERVAL_MINUTES", "Mail check interval (min)", "How often the mailbox is read."),
        ("GMAIL_POLL_SECONDS", "Mail check every (s)", "The interval actually used."),
        ("GMAIL_MAX_PER_POLL", "Most mails per check", "A flood is taken over several checks, oldest first."),
        ("UNCLASSIFIED_MAIL_MINUTES", "Unclassified mail goes to a person after (min)", "A mail the reader has not classified by then (the models are down) waits in Mail triage."),
        ("BUSINESS_TIMEZONE", "Business time zone", "Business dates are in this zone; storage is UTC."),
    )),
    ("files", "Files and validation", (
        ("MAX_FILE_MB", "Largest file (MB)", "Larger uploads are refused."),
        ("MAX_PAGES", "Most pages", "Longer documents are refused."),
        ("MIN_ATTACHMENT_KB", "Smallest image attachment (KB)", "Smaller images are skipped; a PDF of any size above zero is read."),
        ("DATE_MAX_PAST_YEARS", "Dates in the past (years)", "An older invoice date is sent for review."),
        ("DATE_MAX_FUTURE_DAYS", "Invoice dates in the future (days)", "A later invoice date is sent for review."),
        ("DUE_DATE_MAX_FUTURE_YEARS", "Due dates in the future (years)", "A later due date is sent for review."),
    )),
    ("auth", "Sign-in", (
        ("ACCESS_TOKEN_MINUTES", "Access token lasts (min)", "The short-lived token the screen sends on every call."),
        ("REFRESH_TOKEN_DAYS", "Stay signed in for (days)", "How long the refresh cookie can renew the session."),
    )),
)


_PERCENT = {"TDS_RATE"}  # stored as a fraction, read as a percentage


def render(value: Any) -> str:
    """A constant as a person reads it."""
    if isinstance(value, time):
        return value.strftime("%H:%M") + " IST"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return ", ".join(f"{k} {render(v)}" for k, v in value.items())
    if isinstance(value, (tuple, list, set, frozenset)):
        parts = sorted(value) if isinstance(value, (set, frozenset)) else value
        return ", ".join("-".join(f"{p:02d}" for p in item) if isinstance(item, tuple) else render(item) for item in parts)
    return str(value)


def _show(name: str, value: Any) -> str:
    return format((Decimal(value) * 100).normalize(), "f") + "%" if name in _PERCENT else render(value)


def build() -> SettingsOut:
    return SettingsOut(
        editable=False,
        note=NOTE,
        sections=[
            SettingsSection(
                key=key,
                title=title,
                items=[
                    SettingItem(name=name, label=label, value=_show(name, getattr(c, name)), description=description)
                    for name, label, description in items
                ],
            )
            for key, title, items in _SECTIONS
        ],
    )
