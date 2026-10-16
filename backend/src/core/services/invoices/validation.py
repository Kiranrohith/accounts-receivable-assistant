"""Validation of an extracted invoice (Flow 1, step 3). Pure logic: no I/O.

Whether the document is an invoice at all was decided before extraction (the classifier, or a
person in the doc-type review). Every rule runs and every reason is collected (no
short-circuit), then:
FAIL   - a hard failure: invoice number, invoice date, due date or total amount is
         missing, or a value is impossible. The document is rejected.
REVIEW - no hard failure, but something is doubtful: a human checks it.
PASS   - clean.
Customer matching and the duplicate-invoice-number check need the database and are
separate steps.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from constants.constants import (
    BUSINESS_TIMEZONE,
    DATE_MAX_FUTURE_DAYS,
    DATE_MAX_PAST_YEARS,
    DUE_DATE_MAX_FUTURE_YEARS,
    EXTRACTION_CONFIDENCE_MIN,
    ROUNDING_TOLERANCE,
)
from schemas.invoices.extraction import ExtractedInvoice
from schemas.invoices.validation import Outcome, Severity, ValidationIssue, ValidationResult

_CURRENCY = re.compile(r"^[A-Z]{3}$")

# Without any of these there is no invoice: rejected.
_MUST_HAVE = [
    ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date"),
    ("due_date", "Due date"),
    ("total_amount", "Total amount"),
]
# Read-confidence is checked for these when present (customer identity itself is judged
# by customer matching; a missing currency needs a human, not a rejection).
_CHECKED_FOR_CONFIDENCE = _MUST_HAVE + [("customer_name", "Customer name"), ("currency", "Currency")]


def _today_ist() -> date:
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb in a non-leap target year
        return day.replace(year=day.year - years, day=28)


def _years_after(day: date, years: int) -> date:
    return _years_before(day, -years)


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_extraction(invoice: ExtractedInvoice, today: date | None = None) -> ValidationResult:
    today = today or _today_ist()
    issues: list[ValidationIssue] = []

    def add(severity: Severity, code: str, field: str | None, message: str) -> None:
        issues.append(ValidationIssue(code=code, field=field, severity=severity, message=message))

    # --- required fields (hard failure) ---
    for field, label in _MUST_HAVE:
        if _missing(getattr(invoice, field)):
            add(Severity.FAIL, "missing_field", field, f"{label} is missing")
    if _missing(invoice.currency):
        add(Severity.REVIEW, "missing_currency", "currency", "Currency is missing")

    # --- value rules (hard failures) ---
    total = invoice.total_amount
    if total is not None and total <= 0:
        add(Severity.FAIL, "non_positive_amount", "total_amount",
            f"Total amount must be greater than zero (found {total})")

    currency = invoice.currency
    if not _missing(currency) and not _CURRENCY.match(currency.strip()):
        add(Severity.FAIL, "invalid_currency", "currency",
            f"Currency '{currency}' is not a valid 3-letter code such as INR or USD")

    issued, due = invoice.invoice_date, invoice.due_date
    if issued and due and due < issued:
        add(Severity.FAIL, "due_before_invoice", "due_date",
            f"Due date {due} is before the invoice date {issued}")

    # --- date sanity (review) ---
    if issued:
        oldest, latest = _years_before(today, DATE_MAX_PAST_YEARS), today + timedelta(days=DATE_MAX_FUTURE_DAYS)
        if issued < oldest:
            add(Severity.REVIEW, "invoice_date_too_old", "invoice_date",
                f"Invoice date {issued} is more than {DATE_MAX_PAST_YEARS} years ago")
        elif issued > latest:
            add(Severity.REVIEW, "invoice_date_in_future", "invoice_date",
                f"Invoice date {issued} is more than {DATE_MAX_FUTURE_DAYS} days in the future")
    if issued and due and due >= issued and due > _years_after(issued, DUE_DATE_MAX_FUTURE_YEARS):
        add(Severity.REVIEW, "due_date_too_far", "due_date",
            f"Due date {due} is more than {DUE_DATE_MAX_FUTURE_YEARS} year after the invoice date")

    # --- arithmetic (review): subtotal + tax must equal the total, within the tolerance ---
    if None not in (invoice.subtotal, invoice.tax_total, total):
        gap = abs(invoice.subtotal + invoice.tax_total - total)
        if gap > Decimal(ROUNDING_TOLERANCE):
            add(Severity.REVIEW, "total_mismatch", "total_amount",
                f"Subtotal {invoice.subtotal} + tax {invoice.tax_total} = "
                f"{invoice.subtotal + invoice.tax_total}, but the total is {total}")

    # --- low confidence on a field that was read (review) ---
    for field, label in _CHECKED_FOR_CONFIDENCE:
        if _missing(getattr(invoice, field)):
            continue
        confidence = getattr(invoice.confidence, field, None)
        if confidence is None:
            add(Severity.REVIEW, "no_confidence", field, f"{label}: the reader gave no confidence score")
        elif confidence < EXTRACTION_CONFIDENCE_MIN:
            add(Severity.REVIEW, "low_confidence", field,
                f"{label} was read with low confidence ({confidence}); please check it")

    if any(i.severity is Severity.FAIL for i in issues):
        outcome = Outcome.FAIL
    elif issues:
        outcome = Outcome.REVIEW
    else:
        outcome = Outcome.PASS
    return ValidationResult(outcome=outcome, issues=issues)
