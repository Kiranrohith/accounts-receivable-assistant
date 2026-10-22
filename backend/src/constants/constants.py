"""Locked business constants, transcribed from handoff supplement §4 (and D6/D7).

Values only, no logic. Change a value here, never inline in a service.
Money and rates are Decimal; durations are seconds unless the name says otherwise.
"""

from datetime import time
from decimal import Decimal
from typing import Final

# --- Confidence (D6) ---
CONFIDENCE_AUTO_APPLY: Final = 85  # inclusive; >= 85 applies automatically
CONFIDENCE_MAX: Final = 100  # stored/displayed clamp only
TIE_MARGIN: Final = 10  # inclusive; compared on UNCLAMPED raw scores
TRIAGE_HUMAN_THRESHOLD: Final = 75  # triage / classify: below -> human
CLARIFICATION_AUTO_SEND: Final = 85  # DB-grounding capped

# Assumed, not in the supplement: the design sends low-confidence extractions to review
# but gives no number, so this reuses the 75 human threshold.
EXTRACTION_CONFIDENCE_MIN: Final = 75

LAYER_BASE_SCORE: Final = {"L1": 60, "L2": 45, "L3": 35}
AMOUNT_FIT_SCORE: Final = {"T1": 35, "T2": 30, "OVER_SHORT": 15}
MODIFIER_DUE_WITHIN_60D: Final = 5
MODIFIER_SINGLE_OPEN_INVOICE: Final = 5  # skipped for multi-invoice sets
MODIFIER_KNOWN_REF_PATTERN: Final = 3
DUE_DATE_MODIFIER_WINDOW_DAYS: Final = 60
# Assumed, not in the supplement: how many open invoices the subset-sum search will combine.
# More than this and the payment goes to review instead of a long search.
MAX_SUBSET_INVOICES: Final = 12
CREDIT_NOTE_PREFIX: Final = "CN"  # note numbers: CN-<year>-<6 digits>

# --- Tax, tolerance, FX (D7) ---
TDS_RATE: Final = Decimal("0.10")  # frozen at invoice creation; DEBIT_NOTE skips TDS
ROUNDING_TOLERANCE: Final = Decimal("1")  # arithmetic checks (tax totals, an advice's own split): 1 unit
TOLERANCE_T1_INR: Final = Decimal("1.00")  # T1: INR payment vs INR invoice, +/- this, INCLUSIVE
FX_BAND_PCT: Final = Decimal("1.5")  # T2: same-currency foreign payment, +/- % of the balance, INCLUSIVE
# Invoice numbers are compared with everything but letters and digits removed ("INV-1176" = "inv 1176").
# A number without its prefix ("1176") is a different number: it never matches "INV1176".
INVOICE_NO_NORMALIZE: Final = "uppercase; strip non-alphanumerics; prefix required"

# Per-currency constants map (not per-date; stated limitation). Only INR is known:
# the foreign rates are a business input that is not in the design docs yet.
# A currency missing here must route to review (edge case A12), never a guess.
FX_RATES: Final = {"INR": Decimal("1")}

# ECB Data Portal (SDMX REST): reference rates are EUR-based, one per TARGET working day.
ECB_API_URL: Final = "https://data-api.ecb.europa.eu/service/data/EXR"
ECB_TIMEOUT_SECONDS: Final = 10
FX_LOOKBACK_DAYS: Final = 10  # weekends / holidays: use the latest rate published before the date
# A rate older than this (vs the invoice date) is not used automatically: the invoice goes to
# review. Covers a weekend plus a holiday run (Easter, Christmas) with room to spare.
FX_MAX_STALE_DAYS: Final = 5

# --- Mail labels (decision 60; ensure_labels() creates these at boot once the mail module exists) ---
GMAIL_LABEL_PROCESSED_PO: Final = "AR/Processed-PO"

# --- Retries and backoff (all jittered) ---
BACKOFF_JITTER: Final = Decimal("0.30")  # +/-30%
LLM_RETRY_DELAYS_SECONDS: Final = (3, 9)  # then fallback model, own 2-retry budget
OTHER_RETRY_DELAYS_SECONDS: Final = (3, 9, 15)
CONTENTION_BACKOFF_SECONDS: Final = (0.1, 0.5, 2.0)  # allocation re-matches
OUTAGE_BACKOFF_SECONDS: Final = (60, 300, 900)  # external service down
REMATCH_CAP: Final = 3  # then -> review

# --- Job runner ---
WORKER_POLL_SECONDS: Final = (1, 2)  # FOR UPDATE SKIP LOCKED claim loop
HEARTBEAT_SECONDS: Final = 30  # background thread, independent of step boundaries
STALE_HEARTBEAT_SECONDS: Final = 90  # worker died, reclaim fast (>= 3x heartbeat)
WATCHDOG_INTERVAL_SECONDS: Final = 60  # must stay well under STALE_HEARTBEAT_SECONDS
MAX_JOB_RUNTIME_MINUTES: Final = 15  # pathological job, fail it (benchmark-set)

assert STALE_HEARTBEAT_SECONDS >= 3 * HEARTBEAT_SECONDS
assert WATCHDOG_INTERVAL_SECONDS < STALE_HEARTBEAT_SECONDS

# --- Schedules ---
POLL_INTERVAL_MINUTES: Final = (2, 5)  # gmail_poll
REMINDER_SCAN_TIME_IST: Final = time(9, 0)
DEADLINE_SCAN_TIME_IST: Final = time(9, 15)  # stagger only, NO data dependency
SCHEDULER_TICK_SECONDS: Final = 30  # how often a worker looks for due schedules
BUSINESS_TIMEZONE: Final = "Asia/Kolkata"  # business dates IST; storage UTC

# --- Disputes: working days ---
# Fixed-date national holidays as (month, day), every year: Republic Day, Independence Day, Gandhi
# Jayanti, Christmas. Festivals that move (Diwali, Holi, Eid...) are not here; add the year's dates
# if the business wants them counted. Weekends are always non-working.
HOLIDAYS_FIXED: Final = ((1, 26), (8, 15), (10, 2), (12, 25))
DISPUTE_WARNING_WORKING_DAYS: Final = 1  # warn this many working days before a deadline
DISPUTE_CLASSIFY_THRESHOLD: Final = 75  # category confidence below this waits for a person
DISPUTE_DEFAULT_CONFIDENCE: Final = 100  # a person's own classification
DISPUTE_OWNER_ROLE: Final = "FA"  # normal disputes go to the least-loaded user of this role
DISPUTE_ESCALATION_ROLE: Final = "FTM"  # escalated ones to this role
DISPUTE_FALLBACK_ROLE: Final = "ADM"  # when the role above has nobody active

# --- Outgoing mail ---
SMTP_RETRY_DELAYS_SECONDS: Final = (3, 9, 15)  # transport retries: the first try plus one per delay
SMTP_TIMEOUT_SECONDS: Final = 15
CLARIFICATION_AUTO_SEND_CONFIDENCE: Final = 85  # first clarification only; also needs a grounded draft

# --- Reminders and disputes ---
REMINDER_TIERS_DAYS: Final = (1, 15, 30)
COLLECTION_HANDOFF_DAYS: Final = 60
DISPUTE_FIRST_RESPONSE_WORKING_DAYS: Final = 2  # Indian working days, date columns
DISPUTE_RESOLUTION_WORKING_DAYS: Final = 10

# --- Files and validation ---
MAX_FILE_MB: Final = 20
MAX_PAGES: Final = 30
MIN_ATTACHMENT_KB: Final = 5
DATE_MAX_PAST_YEARS: Final = 2
DATE_MAX_FUTURE_DAYS: Final = 30
DUE_DATE_MAX_FUTURE_YEARS: Final = 1
JUNK_REFERENCES: Final = frozenset({"NEFT", "IMPS", "RTGS", "PAYMENT", "NA", "-"})

GSTIN_PATTERN: Final = r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"  # 15 characters

# --- Auth ---
JWT_EXPIRY_HOURS: Final = 8
