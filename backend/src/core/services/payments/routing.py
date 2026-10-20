"""Decide what an extracted payment advice becomes, and write it (Flow 2, steps 3-4).

- anything doubtful (validation REVIEW, customer not identified cleanly): a payment in state
  NeedsReview with the reasons, every money bucket at zero (awaiting a human is not cash)
- the same (customer, reference) as a payment already standing (D4): state Rejected, `duplicate_of`
  set, zero buckets. A junk or empty reference never dedups.
- otherwise: state Matching, customer and identity layer set, ready for the matcher

Callers commit. The payment row and its state_log entry go in one transaction.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import JUNK_REFERENCES
from core.services.invoices.fx import is_foreign
from core.services.payments.identity import IdentityResult, identify_payer
from data.models.postgres import Payment, SourceDocument
from data.models.postgres.enums import MatchedLayer, PaymentState, TriggerType
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from schemas.invoices.validation import ValidationResult
from schemas.payments.extraction import ExtractedPayment


class PaymentDisposition(str, Enum):
    MATCHING = "matching"  # identified and clean: waiting for the matcher
    REVIEW = "review"
    REJECTED = "rejected"  # a duplicate of a payment already standing


@dataclass
class PaymentRouteResult:
    disposition: PaymentDisposition
    payment_id: uuid.UUID
    state: PaymentState
    reasons: list[str] = field(default_factory=list)
    identity: IdentityResult | None = None
    duplicate_of: uuid.UUID | None = None

    @property
    def customer_id(self) -> uuid.UUID | None:
        customer = self.identity.customer if self.identity else None
        return customer.id if customer else None


def reference_key(reference: str | None) -> str | None:
    """The normalised reference used to spot a repeat, or None when it cannot identify one:
    empty, a bare bank-rail name (NEFT, IMPS...), or nothing but punctuation."""
    if not reference:
        return None
    key = reference.strip().upper()
    if not key or key in JUNK_REFERENCES or not any(ch.isalnum() for ch in key):
        return None
    return key


def _amount_fields(extracted: ExtractedPayment) -> dict:
    """The amount as received: INR is the authoritative amount straight away; a foreign amount
    waits in `amount_received_foreign` until a match gives it the invoice's frozen rate."""
    currency = (extracted.currency or "").strip().upper() or None
    amount: Decimal | None = extracted.amount
    fields: dict = {"payment_currency": currency}
    if amount is None:
        return fields
    if currency and is_foreign(currency):
        fields["amount_received_foreign"] = amount
    else:
        fields["amount_received_inr"] = amount
    return fields


def _state_log(db: AsyncSession, payment: Payment, state: PaymentState, reason: str) -> None:
    logs_repo.add_state_log(
        db,
        entity_type="payment",
        entity_id=payment.id,
        from_state=None,
        to_state=state.value,
        triggered_by_type=TriggerType.SYSTEM,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )


async def route_extracted_payment(
    db: AsyncSession,
    document: SourceDocument,
    extracted: ExtractedPayment,
    verdict: ValidationResult,
) -> PaymentRouteResult:
    settings = get_settings()
    identity = await identify_payer(db, extracted, our_company_name=settings.our_company_name)
    reasons = [i.message for i in verdict.issues] + identity.issues
    customer = identity.customer
    key = reference_key(extracted.reference_number)

    duplicate: Payment | None = None
    if customer is not None and key is not None and not identity.issues:
        duplicate = await payments_repo.find_duplicate(db, customer.id, key)

    common = dict(
        customer_id=customer.id if customer else None,
        payee_name=extracted.payer_name,
        payee_bank=extracted.payer_bank,
        purpose=extracted.purpose,
        payment_date=extracted.payment_date,
        reference_number=extracted.reference_number.strip() if extracted.reference_number else None,
        from_email=extracted.from_email,
        extracted_invoice_numbers=[i.model_dump(mode="json") for i in extracted.invoices],
        extracted_po=extracted.po_number,
        extracted_credit_note_ref=extracted.credit_note_reference,
        credit_note_requested=extracted.credit_note_requested,
        source_document_id=document.id,
        created_by=document.created_by,
        updated_by=document.created_by,
        **_amount_fields(extracted),
    )

    if duplicate is not None:
        reason = (
            f"Duplicate of an earlier payment: reference {extracted.reference_number.strip()} was "
            f"already received from {customer.customer_name}"
        )
        payment = await payments_repo.insert_payment(
            db, state=PaymentState.REJECTED, matched_layer=identity.layer, match_reason=reason,
            duplicate_of=duplicate.id, **common,
        )
        _state_log(db, payment, PaymentState.REJECTED, reason)
        return PaymentRouteResult(
            PaymentDisposition.REJECTED, payment.id, PaymentState.REJECTED, [reason], identity,
            duplicate.id,
        )

    if reasons:
        payment = await payments_repo.insert_payment(
            db, state=PaymentState.NEEDS_REVIEW, matched_layer=identity.layer,
            review_reasons=reasons, match_reason="Waiting for a person: " + reasons[0], **common,
        )
        _state_log(db, payment, PaymentState.NEEDS_REVIEW, "; ".join(reasons))
        return PaymentRouteResult(
            PaymentDisposition.REVIEW, payment.id, PaymentState.NEEDS_REVIEW, reasons, identity
        )

    reason = f"Identified as {customer.customer_name} by {_LAYER_WORDS[identity.layer]}"
    payment = await payments_repo.insert_payment(
        db, state=PaymentState.MATCHING, matched_layer=identity.layer, match_reason=reason, **common
    )
    _state_log(db, payment, PaymentState.MATCHING, reason)
    return PaymentRouteResult(
        PaymentDisposition.MATCHING, payment.id, PaymentState.MATCHING, [], identity
    )


_LAYER_WORDS = {
    MatchedLayer.L1: "the invoice number it quotes (L1)",
    MatchedLayer.L2: "the sender's e-mail (L2)",
    MatchedLayer.L3: "the payer's name (L3)",
    MatchedLayer.L4: "nothing (L4)",
}
