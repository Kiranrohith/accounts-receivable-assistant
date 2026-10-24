"""The customer's GSTIN is filled in from the customer's record when the document showed none. Real database, no model,
no mail. Proves, for invoices and for purchase orders: a customer matched by name leaves its own GSTIN in the stored
extraction (marked as coming from the customer) and in the review snapshot; a GSTIN the reader did find is never
replaced; an unmatched document gets nothing; a person who picks the customer in review gets the same fill, and the
document's detail shows it."""

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.invoices.routing import Disposition, route_extracted_invoice
from core.services.invoices.validation import validate_extraction
from core.services.purchase_orders.routing import PODisposition, route_extracted_po
from core.services.purchase_orders.validation import validate_purchase_order
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, SourceDocument
from data.models.postgres.enums import DocKind, UploadedVia
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POFieldConfidences, POLineItem

s = get_settings()
assert "ar_gstinfill" in s.database_url, "refusing to run against a non-test database"
results = []
D = Decimal
SURE = 99
GSTIN_ZEN = "29ABCDE1234F1Z5"
GSTIN_OTHER = "07AAACU9999A1Z1"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def customer(name, gstin, n):
    async with SessionLocal() as db:
        c = Customer(customer_name=name, contact="C", email=f"ap{n}@gstinfill.test", gstin=gstin, deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


def invoice(number, name, gstin=None):
    c = FieldConfidences(invoice_number=SURE, customer_name=SURE, customer_gstin=SURE if gstin else None, invoice_date=SURE, due_date=SURE, currency=SURE, total_amount=SURE, line_items=SURE)
    return ExtractedInvoice(invoice_number=number, customer_name=name, customer_gstin=gstin, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                            currency="INR", subtotal=D("1000"), tax_total=D("0"), total_amount=D("1000"), line_items=[LineItem(description="Services", quantity=D(1), unit_price=D("1000"), line_total=D("1000"))], confidence=c)


def po(number, name, gstin=None):
    c = POFieldConfidences(po_number=SURE, po_date=SURE, customer_name=SURE, customer_gstin=SURE if gstin else None, currency=SURE, total_amount=SURE, line_items=SURE)
    return ExtractedPurchaseOrder(po_number=number, po_date=date(2026, 10, 1), customer_name=name, customer_gstin=gstin, currency="INR", total_amount=D("1000"),
                                  line_items=[POLineItem(description="Widgets", quantity=D(10), unit_price=D("100"), line_total=D("1000"))], confidence=c)


async def document(kind, extraction, by=None):
    async with SessionLocal() as db:
        d = SourceDocument(doc_kind=kind, uploaded_via=UploadedVia.UPLOAD, file_name=f"{uuid.uuid4().hex[:6]}.pdf", file_hash=uuid.uuid4().hex, created_by=by,
                           raw_extraction_json=extraction.model_dump(mode="json"))
        db.add(d)
        await db.commit()
        return d.id


async def raw_of(doc_id):
    return (await q("select raw_extraction_json from source_documents where id=:i", i=doc_id))[0][0]


async def route_inv(extracted):
    doc_id = await document(DocKind.INVOICE, extracted)
    async with SessionLocal() as db:
        doc = await db.get(SourceDocument, doc_id)
        result = await route_extracted_invoice(db, doc, extracted, validate_extraction(extracted, today=date(2026, 10, 5)))
        await db.commit()
    return doc_id, result


async def route_po(extracted):
    doc_id = await document(DocKind.PO, extracted)
    async with SessionLocal() as db:
        doc = await db.get(SourceDocument, doc_id)
        result = await route_extracted_po(db, doc, extracted, validate_purchase_order(extracted))
        await db.commit()
    return doc_id, result


async def main():
    await customer("Zenith Softlabs Pvt Ltd", GSTIN_ZEN, 1)

    # ---- invoices routed by the pipeline ----
    doc_id, r = await route_inv(invoice("GF-INV-1", "Zenith Softlabs Pvt Ltd"))
    raw = await raw_of(doc_id)
    check("invoice matched by name, no GSTIN read: the invoice is created", r.disposition is Disposition.INVOICE_CREATED, str(r.reasons))
    check("... and the stored extraction now carries the customer's GSTIN, marked as from the customer", raw["customer_gstin"] == GSTIN_ZEN and raw["customer_gstin_from_customer"] is True, str(raw)[:200])

    doc_id, r = await route_inv(invoice("GF-INV-2", "Zenith Softlabs Pvt Ltd", GSTIN_ZEN))
    raw = await raw_of(doc_id)
    check("a GSTIN the reader found is left exactly as read, with no 'from customer' mark", raw["customer_gstin"] == GSTIN_ZEN and "customer_gstin_from_customer" not in raw, str(raw)[:160])

    doc_id, r = await route_inv(invoice("GF-INV-3", "Nobody Known Ltd"))
    raw = await raw_of(doc_id)
    snap = (await q("select proposed_snapshot from pending_invoice_changes where source_document_id=:d", d=doc_id))[0][0]
    check("an unmatched customer goes to review and nothing is filled in", r.disposition is Disposition.REVIEW and not raw.get("customer_gstin") and not snap["extraction"].get("customer_gstin"), f"{r.disposition} {raw.get('customer_gstin')}")

    # a name that matches but whose printed GSTIN is another customer's / unknown: reviewed, the printed value stays
    doc_id, r = await route_inv(invoice("GF-INV-4", "Zenith Softlabs Pvt Ltd", GSTIN_OTHER))
    raw = await raw_of(doc_id)
    check("a different printed GSTIN is never overwritten by the customer's (it goes to review)", r.disposition is Disposition.REVIEW and raw["customer_gstin"] == GSTIN_OTHER, f"{r.disposition} {raw['customer_gstin']}")

    # ---- purchase orders routed by the pipeline ----
    doc_id, r = await route_po(po("GF-PO-1", "Zenith Softlabs Pvt Ltd"))
    raw = await raw_of(doc_id)
    check("PO matched by name, no GSTIN read: created, and the stored extraction carries the customer's GSTIN", r.disposition is PODisposition.PO_CREATED and raw["customer_gstin"] == GSTIN_ZEN and raw["customer_gstin_from_customer"] is True, f"{r.disposition} {str(raw)[:120]}")
    doc_id, r = await route_po(po("GF-PO-2", "Nobody Known Ltd"))
    raw = await raw_of(doc_id)
    check("PO with an unmatched customer: review, nothing filled in", r.disposition is PODisposition.REVIEW and not raw.get("customer_gstin"), f"{r.disposition} {raw.get('customer_gstin')}")

    # ---- a person picks the customer in review ----
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        admin_id = (await q("select id from users where email=:e", e=s.seed_admin_email))[0][0]
        zen = (await q("select id from customers where gstin=:g", g=GSTIN_ZEN))[0][0]

        # the invoice from GF-INV-3 (unmatched) and the PO from GF-PO-2 were made by the seeded admin? they have no creator: reassign
        await q_exec("update pending_invoice_changes set created_by=:u", u=admin_id)

        review_inv = (await q("select id from pending_invoice_changes where proposed_snapshot->'extraction'->>'invoice_number' = 'GF-INV-3'"))[0][0]
        doc_inv = (await q("select source_document_id from pending_invoice_changes where id=:i", i=review_inv))[0][0]
        r = await c.post(f"/invoices/reviews/{review_inv}/approve", headers=ADM, json={"reason": "it is Zenith", "customer_id": str(zen)})
        raw = await raw_of(doc_inv)
        check("invoice review with a hand-picked customer: approved, and the document's extraction gets the customer's GSTIN", r.status_code == 200 and raw["customer_gstin"] == GSTIN_ZEN and raw["customer_gstin_from_customer"] is True, f"{r.status_code} {r.text[:120]}")
        detail = (await c.get(f"/documents/uploads/{doc_inv}", headers=ADM)).json()
        check("... and the document's detail shows it as the extracted customer GSTIN", (detail.get("extraction") or {}).get("customer_gstin") == GSTIN_ZEN, str(detail.get("extraction"))[:160])

        review_po = (await q("select id from pending_invoice_changes where proposed_snapshot->'extraction'->>'po_number' = 'GF-PO-2'"))[0][0]
        doc_po = (await q("select source_document_id from pending_invoice_changes where id=:i", i=review_po))[0][0]
        r = await c.post(f"/purchase-orders/reviews/{review_po}/approve", headers=ADM, json={"reason": "it is Zenith", "customer_id": str(zen)})
        raw = await raw_of(doc_po)
        check("PO review with a hand-picked customer: approved, and the extraction gets the customer's GSTIN", r.status_code == 200 and raw["customer_gstin"] == GSTIN_ZEN and raw["customer_gstin_from_customer"] is True, f"{r.status_code} {r.text[:120]}")

        # a GSTIN typed in the review edits is a person's value: it is kept, not replaced by the customer's
        review_inv2 = (await q("select id from pending_invoice_changes where proposed_snapshot->'extraction'->>'invoice_number' = 'GF-INV-4'"))[0][0]
        await q_exec("update pending_invoice_changes set created_by=:u where id=:i", u=admin_id, i=review_inv2)
        doc_inv2 = (await q("select source_document_id from pending_invoice_changes where id=:i", i=review_inv2))[0][0]
        r = await c.post(f"/invoices/reviews/{review_inv2}/approve", headers=ADM, json={"reason": "gstin was misread", "customer_id": str(zen), "edits": {"customer_gstin": GSTIN_ZEN}})
        raw = await raw_of(doc_inv2)
        check("a printed GSTIN in the document's extraction is not touched by a review approval", r.status_code == 200 and raw["customer_gstin"] == GSTIN_OTHER and "customer_gstin_from_customer" not in raw, f"{r.status_code} {raw['customer_gstin']}")

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


async def q_exec(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


asyncio.run(main())
