"""A customer's tax ID is any country's: a GSTIN, a VAT number, a TRN, an EIN. Real database, no model (the reader is a
stub that returns what a model would), no mail. Proves, through the real extraction step and the real routing:
(a) an invoice printing 'VAT Reg No: GB2847731 09' finds the UK customer at the tax-ID layer, whatever the spaces;
(b) a corrupted id is dropped with a reason in the log and the same customer still resolves by name;
(c) a valid-shaped id nobody holds matches nothing and falls through without an error;
(d) an Indian invoice resolves by GSTIN exactly as before;
and: a TRN and an EIN resolve; an id of type OTHER is never used to find a customer; an id and a name that point at two
customers are an identity conflict; a matched customer's own id and type are kept in the extraction; our own GSTIN
returned as the customer's is replaced by the document's one other id; the customer's id is unique whatever the spelling."""

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
from sqlalchemy import text

from config.settings import get_settings
from core.services.invoices import extraction as invoice_extraction
from core.services.invoices.routing import Disposition, route_extracted_invoice
from core.services.invoices.validation import validate_extraction
from core.services.purchase_orders import extraction as po_extraction
from core.services.purchase_orders.routing import PODisposition, route_extracted_po
from core.services.purchase_orders.validation import validate_purchase_order
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, SourceDocument
from data.models.postgres.enums import DocKind, TaxIdType, UploadedVia
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POFieldConfidences, POLineItem

s = get_settings()
assert "ar_taxid" in s.database_url, "refusing to run against a non-test database"
OURS = "33AAHCG2210A1Z7"
s.our_gstin = OURS
s.our_company_name = "Genworx Technologies Pvt Ltd"
results = []
D = Decimal
SURE = 99
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def customer(name, tax_id, kind, country="India"):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=name, contact="C", email=f"ap{_n[0]}@taxid.test", tax_id=tax_id, tax_id_type=kind,
                     deducts_tds=False, billing_currency="INR", country=country)
        db.add(c)
        await db.commit()
        return c.id


def invoice(number, name, kind=None, value=None, label=None):
    c = FieldConfidences(invoice_number=SURE, customer_name=SURE, tax_id=SURE if value else None, invoice_date=SURE, due_date=SURE,
                         currency=SURE, total_amount=SURE, line_items=SURE)
    return ExtractedInvoice(invoice_number=number, customer_name=name, tax_id_type=kind, tax_id_value=value, tax_id_raw_label=label,
                            invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="INR", subtotal=D("1000"), tax_total=D("0"),
                            total_amount=D("1000"), line_items=[LineItem(description="Services", quantity=D(1), unit_price=D("1000"), line_total=D("1000"))],
                            confidence=c)


async def run_invoice(extracted, text_printed="Bill To: someone"):
    """The real extraction step with a stub reader, then the real routing."""
    async def reader(schema, messages):
        return extracted, "stub", 1

    original = invoice_extraction.run_structured_extraction
    invoice_extraction.run_structured_extraction = reader
    try:
        outcome = await invoice_extraction.extract_invoice(text_printed)
    finally:
        invoice_extraction.run_structured_extraction = original
    inv = outcome.invoice
    async with SessionLocal() as db:
        doc = SourceDocument(doc_kind=DocKind.INVOICE, uploaded_via=UploadedVia.UPLOAD, file_name=f"{uuid.uuid4().hex[:6]}.pdf", file_hash=uuid.uuid4().hex,
                             raw_extraction_json=inv.model_dump(mode="json"))
        db.add(doc)
        await db.flush()
        result = await route_extracted_invoice(db, doc, inv, validate_extraction(inv, today=date(2026, 10, 5)))
        await db.commit()
        raw = doc.raw_extraction_json
    return outcome, result, raw


async def main():
    brightwave = await customer("Brightwave Digital Ltd", "GB284773109", TaxIdType.VAT, "United Kingdom")
    alnoor = await customer("Al Noor Trading Technologies LLC", "100234567800003", TaxIdType.TRN, "United Arab Emirates")
    halcyon = await customer("Halcyon Systems Inc", "943456789", TaxIdType.EIN, "United States")
    zenith = await customer("Zenith Softlabs Pvt Ltd", "33AABCZ1234F1Z5", TaxIdType.GSTIN)
    kestrel = await customer("Kestrel Cloud Pte Ltd", "UEN201934512K", TaxIdType.OTHER, "Singapore")

    # ---- (a) the VAT number, written with odd spaces, finds the UK customer by id alone (the name matches nobody)
    out, r, raw = await run_invoice(invoice("TX-1", "Brightwave Digital Limited (UK office)", "VAT", "GB2847731 09", "VAT Reg No"))
    check("(a) 'VAT Reg No: GB2847731 09' finds the UK customer at the tax-ID layer", r.disposition is Disposition.INVOICE_CREATED and r.match.method == "tax_id" and r.match.customer.id == brightwave, f"{r.disposition} {r.match.method if r.match else None} {r.reasons}")
    check("(a) the value as printed stays in the extraction", raw["tax_id_value"] == "GB2847731 09" and raw["tax_id_type"] == "VAT" and raw["tax_id_raw_label"] == "VAT Reg No", str(raw)[:200])

    # ---- (b) a corrupted id is dropped (with a reason for the log); the name still finds the customer
    out, r, raw = await run_invoice(invoice("TX-2", "Brightwave Digital Ltd", "VAT", "G284773109", "VAT Reg No"))
    check("(b) the corrupted id is left out, with a reason naming the VAT shape", out.invoice.tax_id_value is None and out.tax_id_note and "VAT" in out.tax_id_note and "G284773109" in out.tax_id_note, str(out.tax_id_note))
    check("(b) ... with a low confidence", out.invoice.confidence.tax_id is not None and out.invoice.confidence.tax_id < 50, str(out.invoice.confidence.tax_id))
    check("(b) the same customer still resolves by name and the invoice is created", r.disposition is Disposition.INVOICE_CREATED and r.match.method == "name" and r.match.customer.id == brightwave, f"{r.disposition} {r.reasons}")
    check("(b) the document is not rejected for the bad id", r.disposition is not Disposition.REJECTED)

    # ---- (c) a valid-shaped id nobody holds
    out, r, raw = await run_invoice(invoice("TX-3", "Nobody Known Ltd", "VAT", "GB999999999", "VAT No"))
    check("(c) an unknown valid-shaped id and an unknown name -> review 'Unknown customer', no error", r.disposition is Disposition.REVIEW and any("Unknown customer" in x and "GB999999999" in x for x in r.reasons), str(r.reasons))
    out, r, raw = await run_invoice(invoice("TX-4", "Brightwave Digital Ltd", "VAT", "GB999999999", "VAT No"))
    check("(c) an unknown id with a known name -> review asking which is right (never guessed)", r.disposition is Disposition.REVIEW and any("not in the customer master" in x for x in r.reasons), str(r.reasons))

    # ---- (d) an Indian invoice resolves by GSTIN as before
    out, r, raw = await run_invoice(invoice("TX-5", "Zenith Softlabs Private Limited", "GSTIN", "33AABCZ1234F1Z5", "GSTIN"))
    check("(d) an Indian invoice resolves by GSTIN", r.disposition is Disposition.INVOICE_CREATED and r.match.method == "tax_id" and r.match.customer.id == zenith, f"{r.disposition} {r.reasons}")
    out, r, raw = await run_invoice(invoice("TX-6", "Zenith Softlabs Private Limited", "GSTIN", "33aabcz1234f1z5"))
    check("(d) ... lower case too", r.match.method == "tax_id" and r.match.customer.id == zenith, str(r.reasons))
    out, r, raw = await run_invoice(invoice("TX-7", "Zenith Softlabs Pvt Ltd", "GSTIN", "33AABCZ1234F1Z"))
    check("(d) a short GSTIN is left out and the exact name still matches", r.disposition is Disposition.INVOICE_CREATED and r.match.method == "name" and out.tax_id_note, f"{r.disposition} {r.reasons}")

    # ---- other kinds
    out, r, raw = await run_invoice(invoice("TX-8", "Al Noor Trading Tech", "TRN", "100234567800003", "TRN"))
    check("a TRN finds the UAE customer", r.match.method == "tax_id" and r.match.customer.id == alnoor, f"{r.disposition} {r.reasons}")
    out, r, raw = await run_invoice(invoice("TX-9", "Halcyon Systems", "EIN", "94-3456789", "EIN"))
    check("an EIN written with its hyphen finds the US customer", r.match.method == "tax_id" and r.match.customer.id == halcyon, f"{r.disposition} {r.reasons}")
    out, r, raw = await run_invoice(invoice("TX-10", "Kestrel Cloud Pte Ltd", "OTHER", "UEN 201934512K", "UEN"))
    check("an id of type OTHER is kept but the customer is found by name, never by the id", r.match.method == "name" and r.match.customer.id == kestrel and out.invoice.tax_id_value == "UEN 201934512K", f"{r.match.method} {r.reasons}")
    out, r, raw = await run_invoice(invoice("TX-11", "Nobody Known Ltd", "OTHER", "UEN 201934512K", "UEN"))
    check("... and an OTHER id equal to a customer's stored one matches nothing on its own", r.disposition is Disposition.REVIEW and r.match.customer is None, f"{r.disposition} {r.reasons}")

    # ---- the identity-conflict guard stays
    out, r, raw = await run_invoice(invoice("TX-12", "Zenith Softlabs Pvt Ltd", "VAT", "GB284773109", "VAT Reg No"))
    check("a tax ID of one customer with the name of another -> identity conflict, review", r.disposition is Disposition.REVIEW and any("Identity conflict" in x for x in r.reasons), str(r.reasons))

    # ---- the customer's own id and type are kept when the document showed none
    out, r, raw = await run_invoice(invoice("TX-13", "Brightwave Digital Ltd"))
    check("name match, no id read: the customer's own VAT number and type are kept in the extraction, marked as from the customer",
          r.disposition is Disposition.INVOICE_CREATED and raw["tax_id_value"] == "GB284773109" and raw["tax_id_type"] == "VAT" and raw["tax_id_from_customer"] is True, str(raw)[:240])

    # ---- our own GSTIN returned for the customer: the document's one other id is taken
    text_two_ids = f"Genworx Technologies Pvt Ltd GSTIN: {OURS}\nBill To: Brightwave Digital Limited (UK office)\nVAT Reg No: GB 284 7731 09\n"
    out, r, raw = await run_invoice(invoice("TX-14", "Brightwave Digital Limited (UK office)", "GSTIN", OURS, "GSTIN"), text_two_ids)
    check("our own GSTIN returned as the customer's: the one other id printed (a VAT number) is used and finds the customer",
          out.invoice.tax_id_type is not None and out.invoice.tax_id_type.value == "VAT", str(out.invoice.tax_id_type))
    check("... and the invoice is matched by it", r.match is not None and r.match.method == "tax_id" and r.match.customer.id == brightwave, f"{r.disposition} {r.reasons}")
    out, r, raw = await run_invoice(invoice("TX-15", "Brightwave Digital Limited (UK office)", "GSTIN", OURS, "GSTIN"), f"GSTIN: {OURS}\nBill To: nobody")
    check("our own GSTIN and no other id printed: reviewed, never used", r.disposition is Disposition.REVIEW and any("our own GSTIN" in x for x in r.reasons), str(r.reasons))

    # ---- a purchase order goes the same way
    async def po_reader(schema, messages):
        return ExtractedPurchaseOrder(po_number="TX-PO-1", po_date=date(2026, 10, 1), customer_name="Al Noor Trading", tax_id_type="TRN", tax_id_value="100 2345 6780 0003",
                                      currency="INR", total_amount=D("1000"), line_items=[POLineItem(description="Widgets", quantity=D(10), unit_price=D("100"), line_total=D("1000"))],
                                      confidence=POFieldConfidences(po_number=SURE, po_date=SURE, customer_name=SURE, tax_id=SURE, currency=SURE, total_amount=SURE, line_items=SURE)), "stub", 1

    original = po_extraction.run_structured_extraction
    po_extraction.run_structured_extraction = po_reader
    try:
        outcome = await po_extraction.extract_purchase_order("Purchase order from Al Noor")
    finally:
        po_extraction.run_structured_extraction = original
    po = outcome.purchase_order
    async with SessionLocal() as db:
        doc = SourceDocument(doc_kind=DocKind.PO, uploaded_via=UploadedVia.UPLOAD, file_name="po.pdf", file_hash=uuid.uuid4().hex, raw_extraction_json=po.model_dump(mode="json"))
        db.add(doc)
        await db.flush()
        pr = await route_extracted_po(db, doc, po, validate_purchase_order(po))
        await db.commit()
    check("a purchase order with a TRN (written with spaces) finds the UAE customer and is created", pr.disposition is PODisposition.PO_CREATED and pr.match.method == "tax_id" and pr.match.customer.id == alnoor, f"{pr.disposition} {pr.reasons}")

    # ---- old extractions still waiting for review keep their GSTIN
    legacy = ExtractedInvoice.model_validate({"invoice_number": "OLD-1", "customer_name": "Old Zenith", "customer_gstin": "33AABCZ1234F1Z5", "confidence": {"customer_gstin": 80}})
    check("an extraction saved with the old customer_gstin field is read as a GSTIN", legacy.tax_id_type.value == "GSTIN" and legacy.tax_id_value == "33AABCZ1234F1Z5" and legacy.confidence.tax_id == 80)
    async with SessionLocal() as db:
        from core.services.customers.resolution import resolve_customer
        m = await resolve_customer(db, tax_id=legacy.tax_id_value, tax_id_type=legacy.tax_id_type, name="Old Zenith")
        check("... and finds its customer", m.matched and m.customer.id == zenith and m.method == "tax_id")

    # ---- the database keeps the tax ID unique whatever the spelling it came in
    async with SessionLocal() as db:
        from sqlalchemy.exc import IntegrityError
        db.add(Customer(customer_name="Dup Brightwave", contact="C", email="dup@taxid.test", tax_id="GB284773109", tax_id_type=TaxIdType.VAT, deducts_tds=False, billing_currency="GBP", country="United Kingdom"))
        try:
            await db.commit()
            refused = False
        except IntegrityError:
            await db.rollback()
            refused = True
    check("the database refuses a second customer with the same stored tax ID", refused)
    check("every customer's stored id is already in normalized form",
          (await q("select count(*) from customers where tax_id <> upper(regexp_replace(tax_id, '[\\s./-]', '', 'g'))"))[0][0] == 0)

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
