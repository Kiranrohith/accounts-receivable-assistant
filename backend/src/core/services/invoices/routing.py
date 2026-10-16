"""Decide what a processed document becomes, and write it (Flow 1, step 3 onward).

- validation FAIL (not an invoice, or invoice number / dates / amount missing): rejected_invoices
- anything doubtful (validation REVIEW, customer not matched cleanly, non-INR, invoice
  number already taken): pending_invoice_changes, for a human
- otherwise: an Open invoice for the matched customer, TDS frozen. A foreign-currency invoice
  is converted at the ECB rate for its invoice date (looked up only then); if no usable rate
  can be had, it goes to review instead

Callers commit; the business rows and the state_log entry go in one transaction.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.services.customers.resolution import CustomerMatch, resolve_customer
from core.exceptions.errors import ExternalServiceError, ValidationFailure
from core.services.fx.ecb import FxRate
from core.services.invoices.amounts import freeze_amounts, money
from core.services.invoices.fx import is_foreign, rate_for_invoice
from core.services.purchase_orders.fulfillment import link_new_invoice
from data.models.postgres import Customer, SourceDocument
from data.models.postgres.enums import InvoiceType, PendingChangeReason
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.invoices.extraction import DocumentType, ExtractedInvoice
from schemas.invoices.validation import Outcome, Severity, ValidationResult



@dataclass(frozen=True)
class CreatedInvoice:
    id: uuid.UUID
    po_hold: bool = False  # linked to a purchase order it does not match: on hold for review
    po_reasons: list[str] = field(default_factory=list)


class Disposition(str, Enum):
    INVOICE_CREATED = "invoice_created"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass
class RouteResult:
    disposition: Disposition
    reasons: list[str] = field(default_factory=list)
    match: CustomerMatch | None = None
    invoice_id: uuid.UUID | None = None
    po_hold: bool = False
    po_reasons: list[str] = field(default_factory=list)
    fx: dict | None = None  # the rate used, for the step log
    pending_change_id: uuid.UUID | None = None
    rejected_invoice_id: uuid.UUID | None = None

    @property
    def customer_id(self) -> uuid.UUID | None:
        return self.match.customer.id if self.match and self.match.customer else None


async def route_extracted_invoice(
    db: AsyncSession,
    document: SourceDocument,
    invoice: ExtractedInvoice,
    verdict: ValidationResult,
) -> RouteResult:
    snapshot = invoice.model_dump(mode="json")

    if verdict.outcome is Outcome.FAIL:
        rejected = await invoices_repo.insert_rejected(
            db,
            source_document_id=document.id,
            attempted_file_name=document.file_name,
            extracted_snapshot=snapshot,
            rejection_reasons=[i.model_dump(mode="json") for i in verdict.issues],
            created_by=document.created_by,
            updated_by=document.created_by,
        )
        return RouteResult(
            Disposition.REJECTED,
            reasons=[i.message for i in verdict.issues],
            rejected_invoice_id=rejected.id,
        )

    settings = get_settings()
    match = await resolve_customer(
        db,
        gstin=invoice.customer_gstin,
        name=invoice.customer_name,
        our_gstin=settings.our_gstin,
        our_company_name=settings.our_company_name,
    )

    reasons = [i.message for i in verdict.issues if i.severity is Severity.REVIEW]
    reasons += match.issues
    if invoice.invoice_number and await invoices_repo.invoice_number_exists(
        db, invoice.invoice_number.strip()
    ):
        reasons.append(
            f"An invoice numbered {invoice.invoice_number.strip()} already exists "
            "(re-uploads of changed invoices are not handled yet)"
        )

    po_number = (invoice.po_number or "").strip()
    if po_number and match.customer is not None:
        # G2: the number is another customer's PO, not this customer's: never auto-link.
        if await po_repo.get_by_customer_number(db, match.customer.id, po_number) is None:
            elsewhere = await po_repo.owner_of_number_elsewhere(db, match.customer.id, po_number)
            if elsewhere:
                reasons.append(
                    f"PO number {po_number} belongs to a purchase order of '{elsewhere}', "
                    f"not of '{match.customer.customer_name}'"
                )

    # Only a foreign-currency invoice that would otherwise be created needs a rate.
    fx: FxRate | None = None
    if not reasons and match.matched and is_foreign(invoice.currency):
        try:
            fx = await rate_for_invoice(invoice.currency.strip().upper(), invoice.invoice_date)
        except ValidationFailure as exc:
            reasons.append(f"Cannot convert the {invoice.currency.strip().upper()} invoice to INR: {exc.message}")
        except ExternalServiceError:
            reasons.append(
                f"The exchange-rate service (ECB) is not available right now, so the "
                f"{invoice.currency.strip().upper()} invoice could not be converted to INR; "
                "approve it from the review list once the service is back"
            )

    if reasons or not match.matched:
        change = await invoices_repo.insert_pending_change(
            db,
            invoice_id=None,
            source_document_id=document.id,
            reason=PendingChangeReason.EXTRACTION_REVIEW,
            proposed_snapshot={
                "extraction": snapshot,
                "matched_customer_id": str(match.customer.id) if match.customer else None,
                "match_method": match.method,
                "reasons": reasons,
            },
            created_by=document.created_by,
        )
        return RouteResult(
            Disposition.REVIEW, reasons=reasons, match=match, pending_change_id=change.id
        )

    created = await create_invoice(db, document, invoice, match.customer, document.created_by, fx)
    return RouteResult(
        Disposition.INVOICE_CREATED,
        match=match,
        invoice_id=created.id,
        po_hold=created.po_hold,
        po_reasons=created.po_reasons,
        fx=_fx_summary(fx),
    )


def _fx_summary(fx: FxRate | None) -> dict | None:
    if fx is None:
        return None
    return {
        "currency": fx.currency,
        "inr_per_unit": str(fx.inr_per_unit),
        "rate_date": fx.rate_date.isoformat(),
        "days_back": fx.days_back,
        "source": fx.source,
    }


async def create_invoice(
    db: AsyncSession,
    document: SourceDocument,
    extracted: ExtractedInvoice,
    customer: Customer,
    actor: uuid.UUID | None,
    fx: FxRate | None = None,
) -> CreatedInvoice:
    """Insert an Open invoice (amounts and TDS frozen) with its line items and state-log entry, then
    link it to its purchase order (if the customer has one) and run the fulfilment check.

    `fx` is the rate for a foreign-currency invoice (required then, None for INR); the amounts
    are converted and frozen here, and never recomputed.
    """
    currency = (extracted.currency or "").strip().upper()
    if not currency:
        raise ValidationFailure("The invoice has no currency")
    if is_foreign(currency) and fx is None:
        raise ValidationFailure(f"No exchange rate was given for the {currency} invoice")
    is_debit_note = extracted.document_type is DocumentType.DEBIT_NOTE
    frozen = freeze_amounts(
        extracted.total_amount,
        rate=fx.inr_per_unit if fx else Decimal("1"),
        foreign=fx is not None,
        deducts_tds=customer.deducts_tds,
        is_debit_note=is_debit_note,
    )
    invoice = await invoices_repo.insert_invoice(
        db,
        customer_id=customer.id,
        invoice_number=extracted.invoice_number.strip(),
        type=InvoiceType.DEBIT_NOTE if is_debit_note else InvoiceType.INVOICE,
        invoice_date=extracted.invoice_date,
        due_date=extracted.due_date,
        invoice_currency=currency,
        po_number=extracted.po_number,
        subtotal=money(extracted.subtotal) if extracted.subtotal is not None else None,
        tax_total=money(extracted.tax_total) if extracted.tax_total is not None else None,
        tax_breakdown=[t.model_dump(mode="json") for t in extracted.tax_breakdown] or None,
        amount_inr=frozen.amount_inr,
        amount_foreign=frozen.amount_foreign,
        fx_rate_invoice=frozen.fx_rate,
        tds_amount=frozen.tds_amount,
        collectible_total=frozen.collectible_total,
        source_document_id=document.id,
        created_by=actor,
        updated_by=actor,
    )
    await invoices_repo.insert_line_items(
        db,
        [
            {
                "invoice_id": invoice.id,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": money(item.unit_price) if item.unit_price is not None else None,
                "line_total": money(item.line_total) if item.line_total is not None else None,
                "tax_rate": item.tax_rate,
                "created_by": actor,
            }
            for item in extracted.line_items
        ],
    )
    logs_repo.add_state_log(
        db,
        entity_type="invoice",
        entity_id=invoice.id,
        from_state=None,
        to_state="Open",
        reason=f"Created from uploaded document {document.file_name}"
        + (
            f"; {currency} converted at {fx.inr_per_unit} INR per unit "
            f"(ECB rate of {fx.rate_date})"
            if fx
            else ""
        ),
        occurred_at=datetime.now(UTC),
    )
    linked = await link_new_invoice(db, invoice, actor)
    if linked is not None and not linked.passed:
        return CreatedInvoice(invoice.id, po_hold=True, po_reasons=linked.reasons)
    return CreatedInvoice(invoice.id)
