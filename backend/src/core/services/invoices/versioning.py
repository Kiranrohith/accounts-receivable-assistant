"""A document arrives whose invoice number is already saved: is it a duplicate, a new version
that can simply be applied, or a change a person has to look at? (Flow 1, D3)

compare()  - what differs between the saved invoice and the new document (pure)
decide()   - duplicate / apply / review / blocked, from those differences and the invoice's state (pure)
route_existing() - decide, then write the consequence:
    duplicate -> a rejected-list entry pointing at the saved invoice (nothing changes)
    blocked   -> a rejected-list entry: a decision is already waiting on this invoice
    review    -> a pending change (reason version_upload) for the uploader
    apply     -> the old state is saved in invoice_versions, the invoice is updated

Rules for applying without a person:
  Open invoice                       apply, whatever changed (if nothing below stops it)
  PartlyPaid                         apply only if the amount did not change (payments were made
                                     against the old amount)
  Paid (closed)                      never applied: review says it is already closed
  customer / type / currency changed, a foreign invoice's date changed (its frozen exchange
  rate may no longer fit), PO number changed, invoice disputed, invoice linked to a purchase
  order                              review
  another decision already pending   blocked

Callers commit.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ValidationFailure
from core.services.fx.ecb import FxRate
from core.services.invoices.amounts import freeze_amounts, money
from core.services.invoices.fx import is_foreign
from core.services.payments.parking import queue_parked_payments
from core.services.purchase_orders.fulfillment import build_snapshot, check_against_po, write_ledger
from data.models.postgres import Customer, Invoice, InvoiceLineItem, SourceDocument
from data.models.postgres.enums import (
    InvoiceStatus,
    InvoiceType,
    PendingChangeReason,
)
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.platform import logs_repo
from data.repositories.purchase_orders import purchase_orders_repo as po_repo
from schemas.invoices.extraction import DocumentType, ExtractedInvoice

VERSION_KIND = "invoice_version"  # tag on pending-change snapshots

LABELS = {
    "customer": "Customer",
    "document_type": "Document type",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "currency": "Currency",
    "po_number": "PO number",
    "subtotal": "Subtotal",
    "tax_total": "Tax total",
    "total_amount": "Total amount",
    "tax_breakdown": "Tax lines",
    "line_items": "Line items",
}
# A change to any of these is a change of what the customer owes.
MONEY_FIELDS = {"total_amount", "subtotal", "tax_total", "tax_breakdown", "currency"}


# ---------------------------------------------------------------- comparing (pure)
def _jsonable(value: Any) -> Any:
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class Change:
    field: str
    old: Any
    new: Any

    def out(self) -> dict:
        return {
            "field": self.field,
            "label": LABELS.get(self.field, self.field),
            "old": _jsonable(self.old),
            "new": _jsonable(self.new),
        }


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _money(value: Decimal | None) -> Decimal | None:
    return None if value is None else money(value)


def _qty(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).normalize()


def _line_key(description: str, quantity, unit_price, line_total, tax_rate) -> tuple:
    return (_text(description), _qty(quantity), _money(unit_price), _money(line_total), _qty(tax_rate))


def _line_view(key: tuple) -> dict:
    return {
        "description": key[0], "quantity": key[1], "unit_price": key[2],
        "line_total": key[3], "tax_rate": key[4],
    }


def stored_total(invoice: Invoice) -> Decimal:
    """The invoice total in the invoice's own currency."""
    return invoice.amount_foreign if invoice.amount_foreign is not None else invoice.amount_inr


def compare(
    invoice: Invoice,
    lines: list[InvoiceLineItem],
    extracted: ExtractedInvoice,
    customer: Customer,
    old_customer_name: str,
) -> list[Change]:
    changes: list[Change] = []

    def differ(name: str, old: Any, new: Any, old_show: Any = None, new_show: Any = None) -> None:
        if old != new:
            changes.append(Change(name, old if old_show is None else old_show, new if new_show is None else new_show))

    if invoice.customer_id != customer.id:
        changes.append(Change("customer", old_customer_name, customer.customer_name))
    new_type = InvoiceType.DEBIT_NOTE if extracted.document_type is DocumentType.DEBIT_NOTE else InvoiceType.INVOICE
    differ("document_type", invoice.type, new_type, invoice.type.value, new_type.value)
    differ("invoice_date", invoice.invoice_date, extracted.invoice_date)
    differ("due_date", invoice.due_date, extracted.due_date)
    differ("currency", (invoice.invoice_currency or "").strip(), (extracted.currency or "").strip().upper())
    differ("po_number", _text(invoice.po_number), _text(extracted.po_number))
    differ("subtotal", _money(invoice.subtotal), _money(extracted.subtotal))
    differ("tax_total", _money(invoice.tax_total), _money(extracted.tax_total))
    differ("total_amount", money(stored_total(invoice)), _money(extracted.total_amount))

    old_tax = [
        ((t.get("type") or "").strip().upper(), _qty(Decimal(t["rate"])) if t.get("rate") is not None else None, _money(Decimal(t["amount"])))
        for t in (invoice.tax_breakdown or [])
    ]
    new_tax = [
        (t.type.strip().upper(), _qty(t.rate), _money(t.amount)) for t in extracted.tax_breakdown
    ]
    differ("tax_breakdown", old_tax, new_tax, [list(t) for t in old_tax], [list(t) for t in new_tax])

    old_lines = [_line_key(l.description, l.quantity, l.unit_price, l.line_total, l.tax_rate) for l in lines]
    new_lines = [
        _line_key(l.description, l.quantity, l.unit_price, l.line_total, l.tax_rate)
        for l in extracted.line_items
    ]
    differ("line_items", old_lines, new_lines, [_line_view(k) for k in old_lines], [_line_view(k) for k in new_lines])
    return changes


# ---------------------------------------------------------------- deciding (pure)
@dataclass
class Decision:
    kind: Literal["duplicate", "blocked", "apply", "review"]
    reasons: list[str] = field(default_factory=list)
    can_approve: bool = True  # a review the invoice's state forbids approving (already paid)


def _shown(value: Any) -> str:
    return "none" if value in (None, "") else str(value)


def decide(
    invoice: Invoice,
    changes: list[Change],
    *,
    has_open_pending: bool,
    po_number_linked: str | None,
) -> Decision:
    number = invoice.invoice_number
    if not changes:
        return Decision(
            "duplicate",
            [f"Identical to invoice {number} already saved (version {invoice.version}); nothing to change"],
        )
    if has_open_pending:
        return Decision(
            "blocked",
            [f"A decision is already waiting for invoice {number}; resolve it before uploading another version"],
        )

    by = {c.field: c for c in changes}
    reasons: list[str] = []
    can_approve = True
    if invoice.status is InvoiceStatus.PAID:
        reasons.append(f"Invoice {number} is already paid (closed): a changed version cannot be applied")
        can_approve = False
    if "customer" in by:
        reasons.append(f"The customer changed from '{by['customer'].old}' to '{by['customer'].new}'")
    if "document_type" in by:
        reasons.append(f"The document type changed from {by['document_type'].old} to {by['document_type'].new}")
    if "currency" in by:
        reasons.append(f"The currency changed from {by['currency'].old} to {by['currency'].new}")
    if "invoice_date" in by and is_foreign(invoice.invoice_currency):
        reasons.append(
            "The invoice date changed on a foreign-currency invoice, so the exchange rate fixed "
            "when it was created may no longer fit"
        )
    if "po_number" in by:
        reasons.append(
            f"The PO number changed from {_shown(by['po_number'].old)} to {_shown(by['po_number'].new)}"
        )
    if invoice.status is InvoiceStatus.PARTLY_PAID and (MONEY_FIELDS & set(by)):
        reasons.append(
            "Payments were already received against the previous amount, and the amount changed "
            f"(total {by['total_amount'].old} to {by['total_amount'].new})"
            if "total_amount" in by
            else "Payments were already received against the previous amount, and the amounts changed"
        )
    if invoice.is_disputed:
        reasons.append("The invoice is under dispute")
    if po_number_linked or invoice.po_hold:
        reasons.append(
            f"The invoice is linked to purchase order {po_number_linked or invoice.po_number}; "
            "applying the change re-checks it against the order"
        )
    if reasons:
        return Decision("review", reasons, can_approve)
    return Decision("apply")


# ---------------------------------------------------------------- applying
def snapshot_of(invoice: Invoice, lines: list[InvoiceLineItem]) -> dict:
    return _jsonable(
        {
            "invoice_number": invoice.invoice_number,
            "version": invoice.version,
            "type": invoice.type.value,
            "status": invoice.status.value,
            "customer_id": invoice.customer_id,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "invoice_currency": (invoice.invoice_currency or "").strip(),
            "po_number": invoice.po_number,
            "subtotal": invoice.subtotal,
            "tax_total": invoice.tax_total,
            "tax_breakdown": invoice.tax_breakdown,
            "amount_inr": invoice.amount_inr,
            "amount_foreign": invoice.amount_foreign,
            "fx_rate_invoice": invoice.fx_rate_invoice,
            "tds_amount": invoice.tds_amount,
            "collectible_total": invoice.collectible_total,
            "amount_paid": invoice.amount_paid,
            "line_items_version": invoice.line_items_version,
            "source_document_id": invoice.source_document_id,
            "line_items": [
                {
                    "description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
                    "line_total": l.line_total, "tax_rate": l.tax_rate,
                }
                for l in lines
            ],
        }
    )


@dataclass
class AppliedVersion:
    version: int
    po_hold: bool = False
    po_reasons: list[str] = field(default_factory=list)


def needs_new_rate(changes: list[Change], currency: str) -> bool:
    """A foreign invoice whose currency or date changed needs a fresh exchange rate."""
    fields = {c.field for c in changes}
    return is_foreign(currency) and bool(fields & {"currency", "invoice_date"})


async def apply_version(
    db: AsyncSession,
    invoice: Invoice,
    document: SourceDocument,
    extracted: ExtractedInvoice,
    customer: Customer,
    actor: uuid.UUID | None,
    changes: list[Change],
    fx: FxRate | None = None,
) -> AppliedVersion:
    """Save the current state as a superseded version, then update the invoice from the document."""
    changed = {c.field for c in changes}
    currency = (extracted.currency or "").strip().upper()
    foreign = is_foreign(currency)
    if needs_new_rate(changes, currency):
        if fx is None:
            raise ValidationFailure(f"No exchange rate was given for the {currency} invoice")
        rate = fx.inr_per_unit
    elif "currency" in changed:
        rate = Decimal("1")  # changed to INR
    else:
        rate = invoice.fx_rate_invoice or Decimal("1")  # frozen at creation
    is_debit_note = extracted.document_type is DocumentType.DEBIT_NOTE
    # TDS treatment is frozen with the invoice; a new customer or type is judged afresh
    deducts_tds = (
        customer.deducts_tds
        if changed & {"customer", "document_type"}
        else (invoice.tds_amount or Decimal("0")) > 0
    )
    frozen = freeze_amounts(
        extracted.total_amount, rate=rate, foreign=foreign, deducts_tds=deducts_tds, is_debit_note=is_debit_note
    )
    if frozen.collectible_total < invoice.amount_paid:
        raise ValidationFailure(
            f"The new amount ({frozen.collectible_total}) is less than what was already paid "
            f"({invoice.amount_paid}); this needs a credit note, not a new version"
        )

    lines = await invoices_repo.current_line_items(db, invoice)
    old_version, old_document = invoice.version, invoice.source_document_id
    await invoices_repo.insert_invoice_version(
        db,
        invoice_id=invoice.id,
        version_no=old_version,
        snapshot=snapshot_of(invoice, lines),
        change_summary={
            "changes": [c.out() for c in changes],
            "new_version": old_version + 1,
            "new_source_document_id": str(document.id),
        },
        source_document_id=old_document,
        created_by=actor,
    )

    invoice.customer_id = customer.id
    invoice.type = InvoiceType.DEBIT_NOTE if is_debit_note else InvoiceType.INVOICE
    invoice.invoice_date = extracted.invoice_date
    invoice.due_date = extracted.due_date
    invoice.invoice_currency = currency
    invoice.po_number = extracted.po_number
    invoice.subtotal = _money(extracted.subtotal)
    invoice.tax_total = _money(extracted.tax_total)
    invoice.tax_breakdown = [t.model_dump(mode="json") for t in extracted.tax_breakdown] or None
    invoice.amount_inr = frozen.amount_inr
    invoice.amount_foreign = frozen.amount_foreign
    invoice.fx_rate_invoice = frozen.fx_rate
    invoice.tds_amount = frozen.tds_amount
    invoice.collectible_total = frozen.collectible_total
    invoice.version = old_version + 1
    invoice.source_document_id = document.id
    invoice.updated_by = actor
    invoice.line_items_version += 1
    await invoices_repo.insert_line_items(
        db,
        [
            {
                "invoice_id": invoice.id,
                "version_no": invoice.line_items_version,
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
    summary = ", ".join(LABELS.get(c.field, c.field).lower() for c in changes)
    logs_repo.add_state_log(
        db,
        entity_type="invoice",
        entity_id=invoice.id,
        from_state=invoice.status.value,
        to_state=invoice.status.value,
        reason=f"Version {invoice.version} applied from uploaded document {document.file_name} ({summary})",
        triggered_by_id=actor,
        occurred_at=datetime.now(UTC),
    )
    await db.flush()
    return await _recheck_purchase_order(db, invoice, actor)


async def _recheck_purchase_order(
    db: AsyncSession, invoice: Invoice, actor: uuid.UUID | None
) -> AppliedVersion:
    """The invoice's lines changed: take back what it consumed on its purchase order, check the new
    lines against it, and either record them or put the invoice on hold with a mismatch review."""
    result = AppliedVersion(invoice.version)
    was_held = invoice.po_hold
    match = await po_repo.get_match(db, invoice.id)
    if match is None:
        return result
    po = await po_repo.lock_purchase_order(db, match.po_id)
    consumed = await po_repo.consumed_by_line_for_invoice(db, invoice.id)
    reversal = [
        {"po_line_item_id": line_id, "invoice_id": invoice.id, "quantity_invoiced": -total, "created_by": actor}
        for line_id, total in consumed.items()
        if total != 0
    ]
    if reversal:
        po_repo.add_fulfillments(db, reversal)
        await db.flush()
    checks, reasons = await check_against_po(db, po, invoice)
    if not reasons:
        write_ledger(db, invoice, checks, actor)
        po_repo.set_po_hold(invoice, False)
        if was_held:  # payments waiting on this hold are matched again
            await queue_parked_payments(db, invoice.customer_id, actor)
        return result
    po_repo.set_po_hold(invoice, True)
    await invoices_repo.insert_pending_change(
        db,
        invoice_id=invoice.id,
        source_document_id=invoice.source_document_id,
        reason=PendingChangeReason.PO_MISMATCH,
        proposed_snapshot=build_snapshot(po, checks, reasons),
        created_by=actor,
    )
    logs_repo.add_state_log(
        db,
        entity_type="invoice",
        entity_id=invoice.id,
        from_state=invoice.status.value,
        to_state=invoice.status.value,
        reason=f"On PO hold: new version does not match purchase order {po.po_number}",
        occurred_at=datetime.now(UTC),
    )
    result.po_hold, result.po_reasons = True, reasons
    return result


# ---------------------------------------------------------------- routing a document with a known number
@dataclass
class VersionOutcome:
    kind: Literal["duplicate", "blocked", "applied", "review"]
    invoice_id: uuid.UUID
    reasons: list[str] = field(default_factory=list)
    version: int | None = None
    review_id: uuid.UUID | None = None
    rejected_id: uuid.UUID | None = None
    po_hold: bool = False
    po_reasons: list[str] = field(default_factory=list)


async def route_existing(
    db: AsyncSession,
    document: SourceDocument,
    extracted: ExtractedInvoice,
    customer: Customer,
    actor: uuid.UUID | None,
    *,
    write_rejections: bool = True,
) -> VersionOutcome:
    """Decide what a document with an already-saved invoice number becomes, and write it.

    `write_rejections=False` is for a caller that turns a duplicate or blocked outcome into an
    error itself (approving an extraction review) instead of a rejected-list entry.
    """
    invoice = await invoices_repo.get_by_number(db, extracted.invoice_number.strip(), for_update=True)
    assert invoice is not None
    lines = await invoices_repo.current_line_items(db, invoice)
    old_customer = await customers_repo.get_by_id(db, invoice.customer_id)
    changes = compare(invoice, lines, extracted, customer, old_customer.customer_name if old_customer else "")
    match = await po_repo.get_match(db, invoice.id)
    linked_po = None
    if match is not None:
        order = await po_repo.get_purchase_order(db, match.po_id)
        linked_po = order.po_number if order else invoice.po_number
    decision = decide(
        invoice,
        changes,
        has_open_pending=await invoices_repo.open_pending_for_invoice(db, invoice.id) is not None,
        po_number_linked=linked_po,
    )
    snapshot = extracted.model_dump(mode="json")

    if decision.kind in ("duplicate", "blocked"):
        rejected_id = None
        if write_rejections:
            rejected = await invoices_repo.insert_rejected(
                db,
                source_document_id=document.id,
                duplicate_of_source_document_id=invoice.source_document_id,
                attempted_file_name=document.file_name,
                extracted_snapshot=snapshot,
                rejection_reasons=[
                    {
                        "code": "duplicate_invoice" if decision.kind == "duplicate" else "version_blocked",
                        "field": "invoice_number",
                        "severity": "fail",
                        "message": decision.reasons[0],
                    }
                ],
                created_by=actor,
                updated_by=actor,
            )
            rejected_id = rejected.id
        return VersionOutcome(decision.kind, invoice.id, decision.reasons, rejected_id=rejected_id)

    if decision.kind == "review":
        change = await invoices_repo.insert_pending_change(
            db,
            invoice_id=invoice.id,
            source_document_id=document.id,
            reason=PendingChangeReason.VERSION_UPLOAD,
            proposed_snapshot={
                "kind": VERSION_KIND,
                "extraction": snapshot,
                "matched_customer_id": str(customer.id),
                "reasons": decision.reasons,
                "can_approve": decision.can_approve,
                "base_version": invoice.version,
                "changes": [c.out() for c in changes],
            },
            diff={"changes": [c.out() for c in changes]},
            created_by=actor,
        )
        return VersionOutcome("review", invoice.id, decision.reasons, review_id=change.id)

    applied = await apply_version(db, invoice, document, extracted, customer, actor, changes)
    return VersionOutcome(
        "applied", invoice.id, version=applied.version, po_hold=applied.po_hold, po_reasons=applied.po_reasons
    )
