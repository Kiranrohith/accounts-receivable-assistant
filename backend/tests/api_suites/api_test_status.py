import asyncio
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from docx import Document
from sqlalchemy import text

import core.services.invoices.fx as invoice_fx
import core.services.documents.intake as intake
import core.services.invoices.processing as proc
from core.services.documents.classification import ClassificationOutcome
from schemas.documents.classification import DocClassification, DocumentClass
from api.rest.app import app
from config.settings import get_settings
from core.services.fx.ecb import FxRate
from core.services.invoices.extraction import ExtractionError, ExtractionOutcome
from data.clients.db import SessionLocal, engine
from data.models.postgres import PurchaseOrder, PurchaseOrderLineItem
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem

s = get_settings()
assert "ar_st" in s.database_url, "refusing to run against a non-test database"
results = []
ALL = {f: 95 for f in FieldConfidences.model_fields}


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


def inv(number, **kw):
    base = dict(
        invoice_number=number, customer_name="Zenith Softlabs Pvt Ltd",
        tax_id_value="33AABCZ1234F1Z5", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
        currency="INR", total_amount=Decimal("10000"), confidence=FieldConfidences(**ALL),
        line_items=[LineItem(description="Consulting", quantity=Decimal("1"))],
    )
    base.update(kw)
    return ExtractedInvoice(**base)


CASES = {
    "review1": inv("INV-RV-1", customer_name="Unknown Corp Ltd", tax_id_value=None),
    "review2": inv("INV-RV-2", customer_name="Unknown Corp Ltd", tax_id_value=None),
    "ok1": inv("INV-LS-1", line_items=[LineItem(description="Laptop", quantity=Decimal("2")), LineItem(description="Monitor", quantity=Decimal("1"))]),
    "usd1": inv("INV-LS-2", currency="USD", total_amount=Decimal("1000"), invoice_date=date(2026, 10, 5)),
    "held1": inv("INV-LS-3", po_number="PO-LS", line_items=[LineItem(description="Laptop", quantity=Decimal("5"))]),
    "nodue": inv("INV-LS-4", due_date=None),
}
seen_forced = []


async def fake_extract(markdown):
    for key, value in CASES.items():
        if f"CASE:{key}" in markdown:
            if key == "boom":
                raise ExtractionError("model unavailable")
            return ExtractionOutcome(value, "fake-model", 1)
    if "CASE:boom" in markdown:
        raise ExtractionError("model unavailable")
    raise AssertionError("unknown case in document")


async def fake_fx(currency, on_date):
    return FxRate(currency, on_date, on_date, Decimal("96.5"), Decimal("108"), Decimal("1.1"))


async def fake_classify(markdown):
    return ClassificationOutcome(DocClassification(doc_class=DocumentClass.INVOICE, confidence=95, reason="test"), "fake-model", 1)


intake.classify_document = fake_classify
proc.extract_invoice = fake_extract
invoice_fx.fetch_rate_to_inr = fake_fx


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())

    def doc(name, key):
        d = Document()
        d.add_paragraph(f"CASE:{key}\nsome document text for {name}")
        d.save(tmp / f"{name}.docx")
        return f"{name}.docx"

    files = {k: doc(k, k) for k in ("review1", "review2", "ok1", "usd1", "held1", "nodue", "boom")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=600) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        await c.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", tax_id="33AABCZ1234F1Z5", deducts_tds=True),
                     cust(customer_name="Meridian Tech Solutions Pvt Ltd", email="ap@meridian.com", tax_id="29AABCM5678K1Z3", deducts_tds=False)):
            assert (await c.post("/customers", headers=ADM, json=body)).status_code == 201
        async with SessionLocal() as db:
            zen = (await db.execute(text("select id from customers where tax_id='33AABCZ1234F1Z5'"))).scalar()
            po = PurchaseOrder(customer_id=zen, po_number="PO-LS", po_date=date(2026, 9, 1))
            db.add(po)
            await db.flush()
            db.add(PurchaseOrderLineItem(po_id=po.id, description="Laptop", normalized_description="laptop", quantity=Decimal("1")))
            await db.commit()

        async def up(H, name, **params):
            return await c.post("/documents/upload", headers=H, params=params, files={"file": (name, (tmp / name).read_bytes())})

        async def status_of(sid, H=ADM):
            for _ in range(120):
                u = next(x for x in (await c.get("/documents/uploads", headers=H)).json() if x["source_document_id"] == sid)
                if u["processing_status"] in ("Succeeded", "Failed"):
                    return u
                await asyncio.sleep(0.5)
            return u

        ids = {}
        async def upload_wait(key, H=ADM):
            r = await up(H, files[key])
            assert r.status_code == 202, (key, r.text)
            ids[key] = r.json()["source_document_id"]
            return await status_of(ids[key], H)

        async def file_path(sid):
            async with SessionLocal() as db:
                return (await db.execute(text("select file_path from source_documents where id=:i"), {"i": sid})).scalar()

        # ===================================================== review statuses
        u = await upload_wait("review1")
        check("doubtful extraction (unknown customer) -> 'Needs review', extraction review", u["status"] == "Needs review" and u["review_kind"] == "extraction" , str(u["status"]))
        u2 = await upload_wait("review2")
        r = await c.post(f"/invoices/reviews/{u['review_id']}/approve", headers=ADM, json={"reason": "Customer confirmed", "customer_id": str(zen)})
        check("approving it creates the invoice", r.status_code == 200 and r.json()["invoice_id"], r.text[:150])
        check("... status becomes 'Invoice created'", (await status_of(ids["review1"]))["status"] == "Invoice created")
        r = await c.post(f"/invoices/reviews/{u2['review_id']}/reject", headers=ADM, json={"reason": "Really a receipt"})
        u2 = await status_of(ids["review2"])
        check("rejecting in review -> status 'Rejected', file kept", r.status_code == 200 and u2["status"] == "Rejected" and await file_path(ids["review2"]) is not None, str(u2["status"]))

        u = await upload_wait("ok1")
        check("clean invoice -> 'Invoice created'", u["status"] == "Invoice created", str(u["status"]))

        # ===================================================== the other statuses
        u = await upload_wait("nodue")
        check("invoice failing validation -> status 'Rejected'", u["status"] == "Rejected" , str(u["status"]))
        r = await up(ADM, files["boom"])
        ids["boom"] = r.json()["source_document_id"]
        u = await status_of(ids["boom"])
        check("extraction crash -> status 'Failed'", u["status"] == "Failed" and u["error"], str(u["status"]))
        u = await upload_wait("usd1")
        check("USD invoice -> 'Invoice created'", u["status"] == "Invoice created")
        u = await upload_wait("held1")
        check("invoice held on a PO mismatch -> 'Invoice created' with po_hold true", u["status"] == "Invoice created" and u["po_hold"] is True, str(u["status"]))

        # ===================================================== GET /invoices
        r = await c.get("/invoices", headers=ADM)
        allv = r.json()
        nums = [x["invoice_number"] for x in allv]
        check("GET /invoices lists the 4 created invoices, newest first", r.status_code == 200 and sorted(nums) == sorted(["INV-RV-1", "INV-LS-1", "INV-LS-2", "INV-LS-3"]) and nums[0] == "INV-LS-3", str(nums))
        row = {x["invoice_number"]: x for x in allv}
        a = row["INV-LS-1"]
        check("INR invoice row: customer name, TDS 10%, balance = collectible, no foreign amount", a["customer_name"] == "Zenith Softlabs Pvt Ltd" and a["invoice_currency"] == "INR" and Decimal(a["tds_amount"]) == Decimal("1000") and Decimal(a["collectible_balance"]) == Decimal("9000") and a["amount_foreign"] is None and Decimal(a["fx_rate"]) == 1, str(a))
        b = row["INV-LS-2"]
        check("USD invoice row: foreign amount, frozen rate, INR amount", b["invoice_currency"] == "USD" and Decimal(b["amount_foreign"]) == 1000 and Decimal(b["fx_rate"]) == Decimal("96.5") and Decimal(b["amount_inr"]) == Decimal("96500"), str(b))
        check("held invoice row: po_hold true, po_number", row["INV-LS-3"]["po_hold"] is True and row["INV-LS-3"]["po_number"] == "PO-LS")
        check("filter po_hold=true -> only the held one", [x["invoice_number"] for x in (await c.get("/invoices?po_hold=true", headers=ADM)).json()] == ["INV-LS-3"])
        check("filter status=Paid -> none; status=Open -> all 4", (await c.get("/invoices?status=Paid", headers=ADM)).json() == [] and len((await c.get("/invoices?status=Open", headers=ADM)).json()) == 4)
        check("filter customer_id (Meridian has none) -> []", (await c.get("/invoices?customer_id=00000000-0000-0000-0000-000000000001", headers=ADM)).json() == [] and len((await c.get(f"/invoices?customer_id={zen}", headers=ADM)).json()) == 4)
        p1 = (await c.get("/invoices?limit=2&offset=0", headers=ADM)).json()
        p2 = (await c.get("/invoices?limit=2&offset=2", headers=ADM)).json()
        check("pagination: pages don't overlap and cover the list", len(p1) == 2 and len(p2) == 2 and not ({x["invoice_id"] for x in p1} & {x["invoice_id"] for x in p2}))
        check("bad filter value -> 422", (await c.get("/invoices?status=Bogus", headers=ADM)).status_code == 422 and (await c.get("/invoices?limit=0", headers=ADM)).status_code == 422)
        check("an FA can list (invoices.view); no token -> 401", (await c.get("/invoices", headers=FA)).status_code == 200 and (await c.get("/invoices")).status_code == 401)

        # ===================================================== GET /invoices/{id}
        d = (await c.get(f"/invoices/{row['INV-LS-3']['invoice_id']}", headers=ADM)).json()
        check("detail: line items, source file, linked PO (po_first), open PO-mismatch review id", [l["description"] for l in d["line_items"]] == ["Laptop"] and d["source_file_name"] == "held1.docx" and d["linked_po"]["po_number"] == "PO-LS" and d["linked_po"]["match_direction"] == "po_first" and d["po_mismatch_review_id"], str(d)[:300])
        mm = (await c.get(f"/purchase-orders/mismatch-reviews/{d['po_mismatch_review_id']}", headers=ADM)).json()
        check("... that review id is the real PO-mismatch review of this invoice", mm["invoice_number"] == "INV-LS-3" and mm["status"] == "Pending")
        d1 = (await c.get(f"/invoices/{row['INV-LS-1']['invoice_id']}", headers=ADM)).json()
        check("detail of a plain invoice: 2 lines, no PO link, no review", [l["description"] for l in d1["line_items"]] == ["Laptop", "Monitor"] and d1["linked_po"] is None and d1["po_mismatch_review_id"] is None)
        check("unknown invoice id -> 404", (await c.get("/invoices/00000000-0000-0000-0000-000000000009", headers=ADM)).status_code == 404)
        check("static paths are not shadowed by /{invoice_id}", (await c.get("/invoices/rejected", headers=ADM)).status_code == 200 and (await c.get("/documents/uploads", headers=ADM)).status_code == 200 and (await c.get("/invoices/reviews", headers=ADM)).status_code == 200)

        # ===================================================== GET /invoices/rejected
        rl = (await c.get("/invoices/rejected", headers=FA)).json()
        by = {x["file_name"]: x for x in rl}
        check("rejected list: the validation reject and the review-rejected one", set(by) == {"nodue.docx", "review2.docx"}, str(sorted(by)))
        nd = by["nodue.docx"]
        check("validation reject: kind invoice, reason in plain English, invoice number shown, file kept", nd["kind"] == "invoice" and any("Due date is missing" in r for r in nd["reasons"]) and nd["reason_codes"] == ["missing_field"] and nd["invoice_number"] == "INV-LS-4" and nd["file_kept"] is True, str(nd))
        u2r = by["review2.docx"]
        check("review-rejected: reviewer's reason first, then the original reason", u2r["reasons"][0] == "Really a receipt" and len(u2r["reasons"]) >= 2, str(u2r["reasons"]))
        check("kind=purchase_order -> none; kind=all includes the invoice-side ones; kind=other empty", (await c.get("/invoices/rejected?kind=purchase_order", headers=ADM)).json() == [] and len((await c.get("/invoices/rejected?kind=all", headers=ADM)).json()) == 2 and (await c.get("/invoices/rejected?kind=other", headers=ADM)).json() == [])
        check("rejected list: bad kind -> 422, no token -> 401", (await c.get("/invoices/rejected?kind=x", headers=ADM)).status_code == 422 and (await c.get("/invoices/rejected")).status_code == 401)
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
