"""Validation of an extracted purchase order (Flow 1b). Pure logic: no I/O.

FAIL   - cannot become a purchase order: no PO number that is certainly absent, no customer
         identity at all, no line items, a quantity or amount that is zero or negative, an
         invalid currency.
REVIEW - a human should look: the PO number may have been missed, a line has no quantity,
         or a value was read with low confidence.
PASS   - clean.
That the document is a PO at all was decided before extraction (classifier or a person).
Customer matching and the duplicate (customer, po_number) check need the database and are
separate steps.
"""

import re

from constants.constants import EXTRACTION_CONFIDENCE_MIN
from schemas.invoices.validation import Outcome, Severity, ValidationIssue, ValidationResult
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_CHECKED_FOR_CONFIDENCE = [
    ("customer_name", "Customer name"),
    ("total_amount", "Total amount"),
]


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_purchase_order(po: ExtractedPurchaseOrder) -> ValidationResult:
    issues: list[ValidationIssue] = []

    def add(severity: Severity, code: str, field: str | None, message: str) -> None:
        issues.append(ValidationIssue(code=code, field=field, severity=severity, message=message))

    # --- PO number is compulsory ---
    if _missing(po.po_number):
        confidence = po.confidence.po_number
        if confidence is not None and confidence >= EXTRACTION_CONFIDENCE_MIN:
            add(Severity.FAIL, "missing_field", "po_number", "The document has no PO number")
        else:
            add(Severity.REVIEW, "po_number_unreadable", "po_number",
                "No PO number could be read; it may be on the document, please check")

    # --- customer identity ---
    if _missing(po.customer_name) and _missing(po.tax_id_value):
        add(Severity.FAIL, "missing_field", "customer_name", "The customer is missing")

    # --- line items ---
    if not po.line_items:
        add(Severity.FAIL, "no_line_items", "line_items", "The purchase order has no line items")
    for number, line in enumerate(po.line_items, start=1):
        if _missing(line.description):
            add(Severity.FAIL, "missing_field", "line_items", f"Line {number} has no description")
        if line.quantity is None:
            add(Severity.REVIEW, "missing_quantity", "line_items",
                f"Line {number} ('{line.description}') has no quantity")
        elif line.quantity <= 0:
            add(Severity.FAIL, "non_positive_quantity", "line_items",
                f"Line {number} ('{line.description}') has a quantity of {line.quantity}; "
                "it must be greater than zero")

    # --- value rules ---
    if po.total_amount is not None and po.total_amount <= 0:
        add(Severity.FAIL, "non_positive_amount", "total_amount",
            f"Total amount must be greater than zero (found {po.total_amount})")
    if not _missing(po.currency) and not _CURRENCY.match(po.currency.strip()):
        add(Severity.FAIL, "invalid_currency", "currency",
            f"Currency '{po.currency}' is not a valid 3-letter code such as INR or USD")

    # --- low confidence on a field that was read (review) ---
    checked = list(_CHECKED_FOR_CONFIDENCE)
    if not _missing(po.po_number):
        checked.append(("po_number", "PO number"))
    if po.line_items:
        checked.append(("line_items", "Line items"))
    for field, label in checked:
        if field != "line_items" and _missing(getattr(po, field)):
            continue
        confidence = getattr(po.confidence, field, None)
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
