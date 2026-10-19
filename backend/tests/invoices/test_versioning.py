import uuid
from datetime import date
from decimal import Decimal

import pytest

from core.services.invoices.versioning import compare, decide
from data.models.postgres import Customer, Invoice, InvoiceLineItem
from data.models.postgres.enums import InvoiceStatus, InvoiceType
from schemas.invoices.extraction import DocumentType, ExtractedInvoice, LineItem, TaxLine

D = Decimal
CUSTOMER = Customer(id=uuid.uuid4(), customer_name="Zenith", deducts_tds=True)


def saved(**over) -> Invoice:
    base = dict(
        id=uuid.uuid4(), customer_id=CUSTOMER.id, invoice_number="INV-1", type=InvoiceType.INVOICE,
        version=1, status=InvoiceStatus.OPEN, is_disputed=False, po_hold=False,
        invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31), invoice_currency="INR",
        po_number=None, subtotal=None, tax_total=None, tax_breakdown=None,
        amount_inr=D("1000.00"), amount_foreign=None, amount_paid=D("0"),
    )
    base.update(over)
    return Invoice(**base)


def lines(*rows) -> list[InvoiceLineItem]:
    return [InvoiceLineItem(description=d, quantity=D(q)) for d, q in rows]


def new(**over) -> ExtractedInvoice:
    base = dict(
        invoice_number="INV-1", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
        currency="INR", total_amount=D("1000"), line_items=[LineItem(description="Laptop", quantity=D("1"))],
    )
    base.update(over)
    return ExtractedInvoice(**base)


def fields(changes):
    return {c.field for c in changes}


def run(invoice=None, extracted=None, customer=CUSTOMER, **decide_kw):
    invoice = invoice or saved()
    changes = compare(invoice, lines(("Laptop", "1")), extracted or new(), customer, "Zenith")
    kw = dict(has_open_pending=False, po_number_linked=None) | decide_kw
    return changes, decide(invoice, changes, **kw)


# ---- comparing
def test_the_same_values_are_identical_even_if_formatted_differently():
    extracted = new(
        total_amount=D("1000.0000"),
        line_items=[LineItem(description="  Laptop ", quantity=D("1.0000"))],
        currency="inr",
    )
    changes, decision = run(extracted=extracted)
    assert changes == [] and decision.kind == "duplicate"


def test_each_kind_of_change_is_found():
    extracted = new(
        invoice_date=date(2026, 10, 2), due_date=date(2026, 11, 5), total_amount=D("1500"),
        subtotal=D("1271.19"), tax_total=D("228.81"), po_number="PO-9",
        tax_breakdown=[TaxLine(type="GST", rate=D("18"), amount=D("228.81"))],
        line_items=[LineItem(description="Laptop", quantity=D("2"))],
        document_type=DocumentType.DEBIT_NOTE,
    )
    changes, _ = run(extracted=extracted)
    assert fields(changes) == {
        "invoice_date", "due_date", "total_amount", "subtotal", "tax_total", "po_number",
        "tax_breakdown", "line_items", "document_type",
    }


def test_the_customer_change_is_named():
    other = Customer(id=uuid.uuid4(), customer_name="Meridian", deducts_tds=False)
    changes, _ = run(customer=other)
    change = next(c for c in changes if c.field == "customer")
    assert (change.old, change.new) == ("Zenith", "Meridian")


def test_a_foreign_invoice_total_is_compared_in_its_own_currency():
    invoice = saved(invoice_currency="USD", amount_foreign=D("1000.00"), amount_inr=D("96500.00"))
    changes, decision = run(invoice=invoice, extracted=new(currency="USD", total_amount=D("1000")))
    assert changes == [] and decision.kind == "duplicate"


# ---- deciding
def test_an_open_invoice_takes_a_changed_amount_without_review():
    _, decision = run(extracted=new(total_amount=D("1500")))
    assert decision.kind == "apply"


def test_partly_paid_with_a_changed_amount_needs_review():
    _, decision = run(saved(status=InvoiceStatus.PARTLY_PAID), new(total_amount=D("1500")))
    assert decision.kind == "review" and decision.can_approve
    assert "already received" in decision.reasons[0]


def test_partly_paid_with_only_a_due_date_change_applies():
    _, decision = run(saved(status=InvoiceStatus.PARTLY_PAID), new(due_date=date(2026, 12, 1)))
    assert decision.kind == "apply"


def test_a_paid_invoice_is_never_applied_and_cannot_be_approved():
    _, decision = run(saved(status=InvoiceStatus.PAID), new(due_date=date(2026, 12, 1)))
    assert decision.kind == "review" and decision.can_approve is False
    assert "already paid (closed)" in decision.reasons[0]


def test_an_identical_document_for_a_paid_invoice_is_just_a_duplicate():
    _, decision = run(saved(status=InvoiceStatus.PAID))
    assert decision.kind == "duplicate"


@pytest.mark.parametrize(
    ("extracted", "customer", "invoice_over", "needle"),
    [
        (new(), Customer(id=uuid.uuid4(), customer_name="Meridian", deducts_tds=False), {}, "customer changed"),
        (new(document_type=DocumentType.DEBIT_NOTE), CUSTOMER, {}, "document type changed"),
        (new(currency="USD"), CUSTOMER, {}, "currency changed"),
        (new(invoice_date=date(2026, 10, 3)), CUSTOMER, {"invoice_currency": "USD", "amount_foreign": D("1000.00")}, "exchange rate"),
        (new(po_number="PO-2"), CUSTOMER, {}, "PO number changed"),
        (new(total_amount=D("1100")), CUSTOMER, {"is_disputed": True}, "under dispute"),
    ],
)
def test_changes_that_need_a_person(extracted, customer, invoice_over, needle):
    invoice = saved(**invoice_over)
    if invoice.invoice_currency == "USD":
        extracted = extracted.model_copy(update={"currency": "USD"})
    _, decision = run(invoice=invoice, extracted=extracted, customer=customer)
    assert decision.kind == "review" and any(needle in r for r in decision.reasons)


def test_a_date_change_on_an_inr_invoice_needs_no_rate_so_it_applies():
    _, decision = run(extracted=new(invoice_date=date(2026, 10, 3)))
    assert decision.kind == "apply"


def test_a_linked_purchase_order_sends_the_change_to_review():
    _, decision = run(extracted=new(total_amount=D("1100")), po_number_linked="PO-7")
    assert decision.kind == "review" and "purchase order PO-7" in decision.reasons[0]


def test_every_reason_is_listed_not_just_the_first():
    other = Customer(id=uuid.uuid4(), customer_name="Meridian", deducts_tds=False)
    _, decision = run(
        saved(status=InvoiceStatus.PARTLY_PAID, is_disputed=True),
        new(total_amount=D("1100")),
        customer=other,
    )
    assert len(decision.reasons) == 3


def test_a_pending_decision_on_the_invoice_blocks_a_new_version():
    _, decision = run(extracted=new(total_amount=D("1500")), has_open_pending=True)
    assert decision.kind == "blocked" and "already waiting" in decision.reasons[0]
