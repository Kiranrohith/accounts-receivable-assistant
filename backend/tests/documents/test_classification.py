"""Routing of a classifier verdict (pure), and per-kind validation as the second net."""

from datetime import date
from decimal import Decimal

import pytest

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.services.documents.classification import Route, decide_route
from core.services.documents.routing import review_reasons
from core.services.invoices.validation import validate_extraction
from core.services.purchase_orders.validation import validate_purchase_order
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences
from schemas.invoices.validation import Outcome
from schemas.purchase_orders.extraction import (
    ExtractedPurchaseOrder,
    POFieldConfidences,
    POLineItem,
)


def verdict(doc_class: DocumentClass, confidence: int) -> DocClassification:
    return DocClassification(doc_class=doc_class, confidence=confidence, reason="because")


@pytest.mark.parametrize(
    ("doc_class", "confidence", "route"),
    [
        (DocumentClass.INVOICE, 100, Route.INVOICE),
        (DocumentClass.INVOICE, TRIAGE_HUMAN_THRESHOLD, Route.INVOICE),  # the bar is inclusive
        (DocumentClass.INVOICE, TRIAGE_HUMAN_THRESHOLD - 1, Route.REVIEW),
        (DocumentClass.PO, 90, Route.PO),
        (DocumentClass.PO, TRIAGE_HUMAN_THRESHOLD - 1, Route.REVIEW),
        (DocumentClass.OTHER, 95, Route.REJECT_OTHER),
        (DocumentClass.OTHER, TRIAGE_HUMAN_THRESHOLD, Route.REJECT_OTHER),
        (DocumentClass.OTHER, TRIAGE_HUMAN_THRESHOLD - 1, Route.REVIEW),  # unsure: never rejected
        (DocumentClass.UNCLEAR, 100, Route.REVIEW),  # cannot be separated, however sure
        (DocumentClass.UNCLEAR, 0, Route.REVIEW),
    ],
)
def test_decide_route(doc_class, confidence, route):
    assert decide_route(verdict(doc_class, confidence)) is route


def test_threshold_is_the_shared_75_constant():
    assert TRIAGE_HUMAN_THRESHOLD == 75


def test_review_reasons_explain_the_doubt():
    low = review_reasons(verdict(DocumentClass.PO, 60))
    assert "a purchase order" in low[0] and "60%" in low[0]
    unclear = review_reasons(verdict(DocumentClass.UNCLEAR, 90))
    assert "could not tell" in unclear[0]


def _invoice(**over) -> ExtractedInvoice:
    base = dict(
        invoice_number="INV-1",
        customer_name="Acme",
        invoice_date=date.today(),
        due_date=date.today(),
        currency="INR",
        total_amount=Decimal("100"),
        confidence=FieldConfidences(
            invoice_number=95, customer_name=95, invoice_date=95, due_date=95, currency=95,
            total_amount=95,
        ),
    )
    base.update(over)
    return ExtractedInvoice(**base)


def test_invoice_validation_is_the_second_net_for_a_misclassified_document():
    # the classifier (or a person) said invoice, but the document has none of the data:
    # every missing required field is a reason, and the verdict is FAIL (rejected list)
    result = validate_extraction(
        ExtractedInvoice(confidence=FieldConfidences(customer_name=95))
    )
    assert result.outcome is Outcome.FAIL
    assert {"invoice_number", "invoice_date", "due_date", "total_amount"} <= {
        i.field for i in result.issues
    }


def test_complete_invoice_passes():
    assert validate_extraction(_invoice()).outcome is Outcome.PASS


def _po(**over) -> ExtractedPurchaseOrder:
    base = dict(
        po_number=None,
        customer_name="Zenith",
        line_items=[POLineItem(description="Laptop", quantity=Decimal("2"))],
        confidence=POFieldConfidences(po_number=95, customer_name=95, line_items=95),
    )
    base.update(over)
    return ExtractedPurchaseOrder(**base)


def test_po_number_certainly_absent_is_a_fail():
    decided = validate_purchase_order(_po())
    assert decided.outcome is Outcome.FAIL
    assert {i.code for i in decided.issues} == {"missing_field"}


def test_unreadable_po_number_is_review():
    low = _po(confidence=POFieldConfidences(po_number=40, customer_name=95, line_items=95))
    decided = validate_purchase_order(low)
    assert decided.outcome is Outcome.REVIEW
    assert {i.code for i in decided.issues} == {"po_number_unreadable"}
