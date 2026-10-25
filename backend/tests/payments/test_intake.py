"""Payment-advice intake logic (pure): validation, the identity waterfall, duplicate key, routing."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.services.documents.classification import Route, decide_route
from core.services.payments.identity import IdentityFacts, decide_identity
from core.services.payments.reference import reference_key
from core.services.payments.validation import validate_payment
from data.models.postgres import Customer
from data.models.postgres.enums import MatchedLayer
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.validation import Outcome
from schemas.payments.extraction import ExtractedPayment, PaidInvoice, PaymentFieldConfidences

TODAY = date(2026, 10, 19)


def customer(name: str, active: bool = True) -> Customer:
    return Customer(id=uuid.uuid4(), customer_name=name, is_active=active)


def advice(**over) -> ExtractedPayment:
    base = dict(
        amount=Decimal("900"),
        currency="INR",
        payment_date=date(2026, 10, 15),
        reference_number="UTR123456",
        payer_name="Acme Traders",
        invoices=[PaidInvoice(invoice_number="INV-1", amount=Decimal("900"))],
        confidence=PaymentFieldConfidences(amount=95, currency=95, payment_date=95),
    )
    base.update(over)
    return ExtractedPayment(**base)


def codes(result) -> set[str]:
    return {i.code for i in result.issues}


# ---- classifier routing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "route"),
    [
        (95, Route.PAYMENT),
        (TRIAGE_HUMAN_THRESHOLD, Route.PAYMENT),  # the bar is inclusive
        (TRIAGE_HUMAN_THRESHOLD - 1, Route.REVIEW),
    ],
)
def test_a_payment_advice_routes_to_the_payment_pipeline(confidence, route):
    verdict = DocClassification(doc_class=DocumentClass.PAYMENT, confidence=confidence, reason="x")
    assert decide_route(verdict) is route


# ---- validation --------------------------------------------------------------------------


def test_a_clean_advice_passes():
    assert validate_payment(advice(), TODAY).outcome is Outcome.PASS


def test_missing_amount_goes_to_review_before_matching():
    result = validate_payment(advice(amount=None), TODAY)
    assert result.outcome is Outcome.REVIEW
    assert codes(result) == {"amount_missing"}


@pytest.mark.parametrize("amount", ["0", "-5"])
def test_zero_or_negative_amount_is_review_never_fail(amount):
    result = validate_payment(advice(amount=Decimal(amount)), TODAY)
    assert result.outcome is Outcome.REVIEW  # money is never silently discarded
    assert "non_positive_amount" in codes(result)


@pytest.mark.parametrize("reference", [None, "", "   ", "NEFT", "imps", "-", "NA"])
def test_no_usable_transaction_reference_goes_to_review(reference):
    # the reference is the only proof the money was sent: everything else clean is not enough
    result = validate_payment(advice(reference_number=reference), TODAY)
    assert result.outcome is Outcome.REVIEW
    assert codes(result) == {"reference_missing"}


def test_a_rail_name_with_a_number_is_a_reference():
    assert validate_payment(advice(reference_number="NEFT-98765"), TODAY).outcome is Outcome.PASS


def test_currency_must_be_present_and_valid():
    assert "currency_missing" in codes(validate_payment(advice(currency=None), TODAY))
    assert "invalid_currency" in codes(validate_payment(advice(currency="rupees"), TODAY))


def test_implausible_dates_are_review():
    assert "payment_date_too_old" in codes(validate_payment(advice(payment_date=date(2020, 1, 1)), TODAY))
    assert "payment_date_in_future" in codes(
        validate_payment(advice(payment_date=date(2026, 12, 25)), TODAY)
    )
    assert validate_payment(advice(payment_date=None), TODAY).outcome is Outcome.PASS  # date is optional


def test_low_or_absent_confidence_on_amount_and_currency_is_review():
    low = advice(confidence=PaymentFieldConfidences(amount=50, currency=95))
    assert "low_confidence" in codes(validate_payment(low, TODAY))
    none = advice(confidence=PaymentFieldConfidences())
    assert codes(validate_payment(none, TODAY)) == {"no_confidence"}


def test_every_problem_is_listed_not_just_the_first():
    result = validate_payment(advice(amount=None, currency="x", payment_date=date(2020, 1, 1), reference_number=None), TODAY)
    assert codes(result) == {"amount_missing", "invalid_currency", "payment_date_too_old", "reference_missing"}


# ---- duplicate key -----------------------------------------------------------------------


@pytest.mark.parametrize("reference", [None, "", "   ", "NEFT", "neft", " IMPS ", "-", "--", "NA", "***"])
def test_junk_and_empty_references_never_dedup(reference):
    assert reference_key(reference) is None


def test_real_references_are_normalised_for_comparison():
    assert reference_key("  utr123456 ") == "UTR123456"
    assert reference_key("UTR123456") == reference_key("utr123456")
    assert reference_key("NEFT-98765") == "NEFT-98765"  # a rail name plus a number is a real ref


# ---- identity waterfall ------------------------------------------------------------------


def test_l1_a_quoted_invoice_names_the_customer():
    acme = customer("Acme")
    result = decide_identity(IdentityFacts(quoted_owners=[acme]))
    assert result.customer is acme and result.layer is MatchedLayer.L1 and result.matched


def test_l1_wins_over_a_matching_email_and_name():
    acme = customer("Acme")
    result = decide_identity(
        IdentityFacts(quoted_owners=[acme], email_matches=[acme], name_matches=[acme])
    )
    assert result.layer is MatchedLayer.L1 and result.matched


def test_quoted_invoices_of_different_customers_go_to_review():
    result = decide_identity(IdentityFacts(quoted_owners=[customer("A"), customer("B")]))
    assert result.customer is None and result.layer is MatchedLayer.L1 and not result.matched  # the layer reached
    assert "different customers" in result.issues[0]


def test_c1_quoted_owner_differs_from_the_sender():
    acme, zenith = customer("Acme"), customer("Zenith")
    result = decide_identity(IdentityFacts(quoted_owners=[acme], email_matches=[zenith]))
    assert result.customer is None and "Identity conflict" in result.issues[0]


def test_quoted_owner_differs_from_the_payer_name():
    acme, zenith = customer("Acme"), customer("Zenith")
    result = decide_identity(IdentityFacts(quoted_owners=[acme], name_matches=[zenith]))
    assert result.customer is None and "payer name" in result.issues[0]


def test_a_shared_mailbox_that_includes_the_owner_is_fine():
    acme, other = customer("Acme"), customer("Other")
    result = decide_identity(IdentityFacts(quoted_owners=[acme], email_matches=[acme, other]))
    assert result.customer is acme and result.matched


def test_l2_sender_email_when_no_invoice_is_quoted():
    acme = customer("Acme")
    result = decide_identity(IdentityFacts(email_matches=[acme]))
    assert result.customer is acme and result.layer is MatchedLayer.L2 and result.matched


def test_l2_shared_email_address_is_review():
    result = decide_identity(IdentityFacts(email_matches=[customer("A"), customer("B")]))
    assert result.customer is None and "several customers" in result.issues[0]


def test_l2_email_and_payer_name_disagree():
    result = decide_identity(
        IdentityFacts(email_matches=[customer("Acme")], name_matches=[customer("Zenith")])
    )
    assert result.customer is None and "Identity conflict" in result.issues[0]


def test_l3_payer_name_when_nothing_else_identifies():
    acme = customer("Acme")
    result = decide_identity(IdentityFacts(name_matches=[acme]))
    assert result.customer is acme and result.layer is MatchedLayer.L3 and result.matched


def test_l3_name_matching_several_customers_is_review():
    result = decide_identity(IdentityFacts(name_matches=[customer("A"), customer("B")]))
    assert result.customer is None and "several customers" in result.issues[0]


def test_l4_nothing_identified():
    result = decide_identity(IdentityFacts())
    assert result.customer is None and result.layer is MatchedLayer.L4
    assert "could not be identified" in result.issues[0]


def test_l4_mentions_when_the_payer_read_was_our_own_company():
    result = decide_identity(IdentityFacts(payer_name_is_ours=True))
    assert "our own company" in result.issues[0]


def test_a_deactivated_customer_is_found_but_not_clean():
    gone = customer("Gone Ltd", active=False)
    result = decide_identity(IdentityFacts(quoted_owners=[gone]))
    assert result.customer is gone and not result.matched
    assert "deactivated" in result.issues[0]


def test_unknown_invoice_numbers_are_carried_through():
    acme = customer("Acme")
    result = decide_identity(IdentityFacts(quoted_owners=[acme], unknown_numbers=["INV-404"]))
    assert result.unknown_numbers == ["INV-404"] and result.matched


def test_conflicting_or_ambiguous_evidence_keeps_the_layer_it_was_found_at():
    a, b = customer("A"), customer("B")
    assert decide_identity(IdentityFacts(email_matches=[a, b])).layer is MatchedLayer.L2
    assert decide_identity(IdentityFacts(name_matches=[a, b])).layer is MatchedLayer.L3
    assert decide_identity(IdentityFacts(quoted_owners=[a], email_matches=[b])).layer is MatchedLayer.L1
    assert decide_identity(IdentityFacts(email_matches=[a], name_matches=[b])).layer is MatchedLayer.L2
    nothing = decide_identity(IdentityFacts())
    assert nothing.layer is MatchedLayer.L4 and nothing.customer is None  # only "nothing identified" is L4
