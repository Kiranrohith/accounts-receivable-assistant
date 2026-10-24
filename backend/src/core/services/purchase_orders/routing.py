"""Decide what a processed purchase-order document becomes, and write it (Flow 1b).

- validation FAIL: rejected_invoices (the file is kept; only non-invoices are discarded)
- anything doubtful (validation REVIEW, customer not matched cleanly, a PO with this number
  already exists for the customer): pending_invoice_changes, for a human
- otherwise: the purchase order and its lines, then every waiting invoice that quotes its
  number is linked and checked (retro-link)

Callers commit; business rows and state_log entries go in one transaction.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.services.customers.gstin import with_customer_gstin
from core.services.customers.resolution import CustomerMatch, resolve_customer
from core.services.purchase_orders.fulfillment import retro_link
from core.services.purchase_orders.normalize import normalize_description
from data.models.postgres import Customer, SourceDocument
from data.models.postgres.enums import PendingChangeReason
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.invoices.validation import Outcome, Severity, ValidationResult
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

PO_KIND = "purchase_order"  # tag on pending-change snapshots and rejected snapshots


class PODisposition(str, Enum):
    PO_CREATED = "po_created"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass
class PORouteResult:
    disposition: PODisposition
    reasons: list[str] = field(default_factory=list)
    match: CustomerMatch | None = None
    po_id: uuid.UUID | None = None
    pending_change_id: uuid.UUID | None = None
    rejected_invoice_id: uuid.UUID | None = None
    linked_invoices: list[dict] = field(default_factory=list)

    @property
    def customer_id(self) -> uuid.UUID | None:
        return self.match.customer.id if self.match and self.match.customer else None


async def route_extracted_po(
    db: AsyncSession,
    document: SourceDocument,
    po: ExtractedPurchaseOrder,
    verdict: ValidationResult,
) -> PORouteResult:
    snapshot = {"kind": PO_KIND, **po.model_dump(mode="json")}

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
        return PORouteResult(
            PODisposition.REJECTED,
            reasons=[i.message for i in verdict.issues],
            rejected_invoice_id=rejected.id,
        )

    reasons = [i.message for i in verdict.issues if i.severity is Severity.REVIEW]
    settings = get_settings()
    match = await resolve_customer(
        db,
        gstin=po.customer_gstin,
        name=po.customer_name,
        our_gstin=settings.our_gstin,
        our_company_name=settings.our_company_name,
    )
    if match.matched:  # the customer is known: a GSTIN the reader did not find is filled in from the customer's record
        po = with_customer_gstin(po, match.customer.gstin)
        documents_repo.fill_customer_gstin(document, match.customer.gstin)
    reasons += match.issues
    if (
        match.customer is not None
        and po.po_number
        and await po_repo.get_by_customer_number(db, match.customer.id, po.po_number.strip())
    ):
        reasons.append(
            f"A purchase order numbered {po.po_number.strip()} already exists for "
            f"{match.customer.customer_name}"
        )

    if reasons or not match.matched:
        change = await invoices_repo.insert_pending_change(
            db,
            invoice_id=None,
            source_document_id=document.id,
            reason=PendingChangeReason.EXTRACTION_REVIEW,
            proposed_snapshot={
                "kind": PO_KIND,
                "extraction": po.model_dump(mode="json"),
                "matched_customer_id": str(match.customer.id) if match and match.customer else None,
                "match_method": match.method if match else None,
                "reasons": reasons,
            },
            created_by=document.created_by,
        )
        return PORouteResult(
            PODisposition.REVIEW, reasons=reasons, match=match, pending_change_id=change.id
        )

    po_id, linked = await create_purchase_order(db, document, po, match.customer, document.created_by)
    return PORouteResult(PODisposition.PO_CREATED, match=match, po_id=po_id, linked_invoices=linked)


async def create_purchase_order(
    db: AsyncSession,
    document: SourceDocument,
    extracted: ExtractedPurchaseOrder,
    customer: Customer,
    actor: uuid.UUID | None,
) -> tuple[uuid.UUID, list[dict]]:
    """Insert the PO with its lines, then link and check the invoices already waiting for it."""
    order = await po_repo.insert_purchase_order(
        db,
        customer_id=customer.id,
        po_number=extracted.po_number.strip(),
        po_date=extracted.po_date,
        currency=(extracted.currency or "").strip().upper() or None,
        total_amount=extracted.total_amount,
        source_document_id=document.id,
        created_by=actor,
        updated_by=actor,
    )
    await po_repo.insert_po_lines(
        db,
        [
            {
                "po_id": order.id,
                "description": line.description,
                "normalized_description": normalize_description(line.description),
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "line_total": line.line_total,
                "created_by": actor,
            }
            for line in extracted.line_items
        ],
    )
    logs_repo.add_state_log(
        db,
        entity_type="purchase_order",
        entity_id=order.id,
        from_state=None,
        to_state="Created",
        reason=f"Created from uploaded document {document.file_name}",
        occurred_at=datetime.now(UTC),
    )
    linked = [
        {
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "passed": result.passed,
            "reasons": result.reasons,
        }
        for invoice, result in await retro_link(db, order, actor)
    ]
    return order.id, linked
