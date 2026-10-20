import uuid
from types import SimpleNamespace

import pytest

from core.services.disputes import identity
from core.services.disputes.processing import reader_notes
from core.services.documents.classification import Route, decide_route, kinds_for
from core.services.payments.identity import IdentityFacts
from data.models.postgres.enums import DocKind
from schemas.disputes.extraction import DisputeFieldConfidences, ExtractedDispute
from schemas.documents.classification import DocClassification, DocumentClass


def verdict(doc_class, confidence=90, **flags):
    return DocClassification(doc_class=DocumentClass(doc_class), confidence=confidence, reason="x", **flags)


def test_a_confident_complaint_routes_to_the_dispute_pipeline():
    assert decide_route(verdict("dispute")) is Route.DISPUTE
    assert decide_route(verdict("dispute", 74)) is Route.REVIEW  # under the threshold: a person decides


@pytest.mark.parametrize(
    ("result", "kinds"),
    [
        (verdict("invoice"), [DocKind.INVOICE]),
        (verdict("po"), [DocKind.PO]),
        (verdict("payment"), [DocKind.PAYMENT]),
        (verdict("dispute"), [DocKind.DISPUTE]),
        (verdict("dispute", also_payment=True), [DocKind.DISPUTE, DocKind.PAYMENT]),
        (verdict("payment", also_dispute=True), [DocKind.DISPUTE, DocKind.PAYMENT]),  # dispute FIRST either way
        (verdict("invoice", also_dispute=True), [DocKind.INVOICE]),  # the flags only matter for these two
        (verdict("other"), []),
        (verdict("unclear"), []),
    ],
)
def test_what_runs_for_a_verdict_and_in_what_order(result, kinds):
    assert kinds_for(result) == kinds


def customer(name, active=True):
    return SimpleNamespace(id=uuid.uuid4(), customer_name=name, is_active=active)


def invoice(number, owner):
    return SimpleNamespace(id=uuid.uuid4(), invoice_number=number, customer_id=owner.id)


def test_the_invoices_it_quotes_name_the_customer_and_only_those_invoices_are_kept():
    zen = customer("Zenith")
    a, b = invoice("A-1", zen), invoice("A-2", zen)
    found = identity.decide(IdentityFacts(quoted_invoices=[a, b], quoted_owners=[zen], unknown_numbers=["A-9"]))
    assert found.customer is zen and found.invoices == [a, b]
    assert found.unknown_numbers == ["A-9"] and found.issues == []


def test_evidence_that_disagrees_names_no_customer():
    zen, mer = customer("Zenith"), customer("Meridian")
    found = identity.decide(
        IdentityFacts(quoted_invoices=[invoice("A-1", zen)], quoted_owners=[zen], email_matches=[mer])
    )
    assert found.customer is None and found.invoices == []
    assert "conflict" in found.issues[0].lower()
    assert "payer" not in " ".join(found.issues)  # worded for a complaint, not a payment


def test_a_complainant_nobody_knows_names_no_customer():
    found = identity.decide(IdentityFacts())
    assert found.customer is None and "could not be identified" in found.issues[0]


def test_a_deactivated_customer_is_still_heard():
    gone = customer("Dormant", active=False)
    found = identity.decide(IdentityFacts(name_matches=[gone]))
    assert found.customer is gone and found.issues == []


def test_a_quoted_invoice_of_another_customer_is_reported_not_attached():
    zen, mer = customer("Zenith"), customer("Meridian")
    mine, theirs = invoice("A-1", zen), invoice("M-1", mer)
    found = identity.decide(IdentityFacts(email_matches=[zen], quoted_invoices=[mine, theirs], quoted_owners=[zen, mer]))
    assert found.customer is None  # two owners: a person decides


def extracted(category, sure, **kw):
    return ExtractedDispute(category=category, confidence=DisputeFieldConfidences(category=sure), **kw)


def test_the_reader_notes_explain_why_a_person_is_needed():
    none = identity.DisputeIdentity(None, [], ["The customer could not be identified"], ["X-1"], ["M-1"])
    notes = reader_notes(extracted("Pricing", 60), none)
    text = " | ".join(notes)
    assert "could not be identified" in text and "X-1" in text and "M-1" in text and "60% sure" in text
    assert reader_notes(extracted(None, None), none)[-1] == "The reader could not tell the reason for the complaint"


def test_a_sure_category_for_a_known_customer_needs_no_note():
    zen = customer("Zenith")
    assert reader_notes(extracted("Quality", 92), identity.DisputeIdentity(zen, [], [], [], [])) == []
