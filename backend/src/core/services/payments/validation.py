"""Validation of an extracted payment advice. Pure logic: no I/O.

Money is never discarded on a doubt: every problem here is REVIEW (a person looks), never FAIL.

REVIEW - no amount (D3b: the reviewer finds it and enters it), an amount of zero or less (C9),
         a currency that is missing or not a 3-letter code, an implausible payment date, or the
         amount / currency read with low confidence.
PASS   - clean. Who paid, duplicates and matching need the database and are separate steps.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from constants.constants import (
    BUSINESS_TIMEZONE,
    DATE_MAX_FUTURE_DAYS,
    DATE_MAX_PAST_YEARS,
    EXTRACTION_CONFIDENCE_MIN,
)
from schemas.invoices.validation import Outcome, Severity, ValidationIssue, ValidationResult
from schemas.payments.extraction import ExtractedPayment

_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_payment(payment: ExtractedPayment, today: date | None = None) -> ValidationResult:
    today = today or datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()
    issues: list[ValidationIssue] = []

    def add(code: str, field: str | None, message: str) -> None:
        issues.append(
            ValidationIssue(code=code, field=field, severity=Severity.REVIEW, message=message)
        )

    # --- amount ---
    if payment.amount is None:
        add("amount_missing", "amount",
            "The advice does not state the amount paid; please find it and enter it")
    elif payment.amount <= 0:
        add("non_positive_amount", "amount",
            f"The amount paid must be greater than zero (found {payment.amount})")

    # --- currency ---
    if _missing(payment.currency):
        add("currency_missing", "currency", "The advice does not state the currency")
    elif not _CURRENCY.match(payment.currency.strip()):
        add("invalid_currency", "currency",
            f"Currency '{payment.currency}' is not a valid 3-letter code such as INR or USD")

    # --- date sanity ---
    paid = payment.payment_date
    if paid:
        oldest = today - timedelta(days=365 * DATE_MAX_PAST_YEARS)
        latest = today + timedelta(days=DATE_MAX_FUTURE_DAYS)
        if paid < oldest:
            add("payment_date_too_old", "payment_date",
                f"Payment date {paid} is more than {DATE_MAX_PAST_YEARS} years ago")
        elif paid > latest:
            add("payment_date_in_future", "payment_date",
                f"Payment date {paid} is more than {DATE_MAX_FUTURE_DAYS} days in the future")

    # --- low confidence on a field that was read ---
    for field, label in (("amount", "Amount"), ("currency", "Currency")):
        if _missing(getattr(payment, field)):
            continue
        confidence = getattr(payment.confidence, field, None)
        if confidence is None:
            add("no_confidence", field, f"{label}: the reader gave no confidence score")
        elif confidence < EXTRACTION_CONFIDENCE_MIN:
            add("low_confidence", field,
                f"{label} was read with low confidence ({confidence}); please check it")

    return ValidationResult(outcome=Outcome.REVIEW if issues else Outcome.PASS, issues=issues)
