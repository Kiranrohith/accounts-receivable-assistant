import asyncio
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from docx import Document

import core.services.documents.intake as intake
import core.services.invoices.processing as proc
import core.services.purchase_orders.processing as po_proc
from api.rest.app import app
from config.settings import get_settings
from core.services.documents.classification import ClassificationOutcome
from core.services.invoices.extraction import ExtractionOutcome
from core.services.purchase_orders.extraction import PurchaseOrderOutcome
from data.clients.db import engine
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POFieldConfidences, POLineItem

s = get_settings()
assert "ar_pl" in s.database_url, "refusing to run against a non-test database"
results = []
D = Decimal
ZEN = dict(customer_name="Zenith Softlabs Pvt Ltd", tax_id_value="33AABCZ1234F1Z5")
MER = dict(customer_name="Meridian Tech Solutions Pvt Ltd", tax_id_value="29AABCM5678K1Z3")


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


def po(number, who, lines):
    return ExtractedPurchaseOrder(
        po_number=number, currency="INR", total_amount=D("1000"),
        line_items=[POLineItem(description=d, quantity=(D(q) if q else None)) for d, q in lines],
        confidence=POFieldConfidences(**{f: 95 for f in POFieldConfidences.model_fields}), **who,
    )


def inv(number, po_number, lines, **kw):
    return ExtractedInvoice(
        invoice_number=number, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="INR",
        total_amount=D("1000"), po_number=po_number, **ZEN,
        line_items=[LineItem(description=d, quantity=D(q)) for d, q in lines],
        confidence=FieldConfidences(**{f: 95 for f in FieldConfidences.model_fields}), **kw,
    )


CASES = {
    "po1": po("PO-L1", ZEN, [("Laptop", "10"), ("Monitor", "5")]),
    "po2": po("PO-L2", MER, [("Server", "2")]),
    "po3": po("PO-L3", ZEN, [("Cable", "10")]),
    "i1": inv("INV-L1", "PO-L1", [("Laptop", "6")]),
    "i2": inv("INV-L2", "PO-L1", [("Laptop", "6")]),
    "i3": inv("INV-L3", "PO-L1", [("Laptop", "4"), ("Monitor", "5")]),
    "i5": inv("INV-L5", "PO-L3", [("Cable", "3")]),
}


def find(markdown):
    return next(k for k in CASES if f"CASE:{k}\n" in markdown + "\n")


async def classify(markdown):
    cls = DocumentClass.PO if find(markdown).startswith("po") else DocumentClass.INVOICE
    return ClassificationOutcome(DocClassification(doc_class=cls, confidence=95, reason="test"), "fake", 1)


async def extract_invoice(markdown):
    return ExtractionOutcome(CASES[find(markdown)], "fake", 1)


async def extract_po(markdown):
    return PurchaseOrderOutcome(CASES[find(markdown)], "fake", 1)


intake.classify_document = classify
proc.extract_invoice = extract_invoice
po_proc.extract_purchase_order = extract_po


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())
    for k in CASES:
        d = Document()
        d.add_paragraph(f"CASE:{k}")
        d.save(tmp / f"{k}.docx")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        await c.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (cust(customer_name=ZEN["customer_name"], email="ap@zenith.com", tax_id=ZEN["tax_id_value"], deducts_tds=True),
                     cust(customer_name=MER["customer_name"], email="ap@meridian.com", tax_id=MER["tax_id_value"], deducts_tds=False)):
            assert (await c.post("/customers", headers=ADM, json=body)).status_code == 201
        zen_id = next(x["id"] for x in (await c.get("/customers", headers=ADM)).json() if x["customer_name"] == ZEN["customer_name"])

        async def up(k):
            r = await c.post("/documents/upload", headers=ADM, files={"file": (f"{k}.docx", (tmp / f"{k}.docx").read_bytes())})
            assert r.status_code == 202, r.text
            sid = r.json()["source_document_id"]
            for _ in range(240):
                st = (await c.get(f"/documents/uploads/{sid}", headers=ADM)).json()
                if st["processing_status"] in ("Succeeded", "Failed"):
                    return st
                await asyncio.sleep(0.5)

        for k in ("po1", "po2", "po3", "i1", "i2", "i3", "i5"):
            st = await up(k)
            assert st["status"] in ("Invoice created", "Purchase order created"), (k, st["status"], st["disposition_reasons"])

        # ===================================================== list
        r = await c.get("/purchase-orders", headers=ADM)
        rows = r.json()
        by = {x["po_number"]: x for x in rows}
        check("list: 3 POs, newest first", r.status_code == 200 and [x["po_number"] for x in rows] == ["PO-L3", "PO-L2", "PO-L1"], str([x["po_number"] for x in rows]))
        check("PO-L1: laptop 10/10 and monitor 5/5 -> Fully invoiced, 2 lines, 3 linked, 1 on hold", by["PO-L1"]["fulfillment"] == "Fully invoiced" and by["PO-L1"]["line_count"] == 2 and by["PO-L1"]["linked_invoices"] == 3 and by["PO-L1"]["invoices_on_hold"] == 1, str(by["PO-L1"]))
        check("PO-L2: Not invoiced, nothing linked", by["PO-L2"]["fulfillment"] == "Not invoiced" and by["PO-L2"]["linked_invoices"] == 0 and by["PO-L2"]["customer_name"] == MER["customer_name"])
        check("PO-L3: Partly invoiced (3 of 10)", by["PO-L3"]["fulfillment"] == "Partly invoiced" and by["PO-L3"]["linked_invoices"] == 1 and by["PO-L3"]["invoices_on_hold"] == 0)
        check("list item carries currency, total, source document", by["PO-L1"]["currency"] == "INR" and D(by["PO-L1"]["total_amount"]) == D("1000") and by["PO-L1"]["source_document_id"])
        check("filter customer_id (Zenith) -> PO-L1, PO-L3", sorted(x["po_number"] for x in (await c.get("/purchase-orders", params={"customer_id": zen_id}, headers=ADM)).json()) == ["PO-L1", "PO-L3"])
        check("search 'l2' (case-insensitive contains) -> PO-L2", [x["po_number"] for x in (await c.get("/purchase-orders", params={"search": "l2"}, headers=ADM)).json()] == ["PO-L2"])
        check("search 'PO-L' -> all 3; search '%' is literal -> none", len((await c.get("/purchase-orders", params={"search": "PO-L"}, headers=ADM)).json()) == 3 and (await c.get("/purchase-orders", params={"search": "%"}, headers=ADM)).json() == [])
        p1 = (await c.get("/purchase-orders", params={"limit": 2, "offset": 0}, headers=ADM)).json()
        p2 = (await c.get("/purchase-orders", params={"limit": 2, "offset": 2}, headers=ADM)).json()
        check("pagination: pages don't overlap and cover the list", len(p1) == 2 and len(p2) == 1 and not ({x["purchase_order_id"] for x in p1} & {x["purchase_order_id"] for x in p2}))
        check("bad limit / bad customer_id -> 422", (await c.get("/purchase-orders?limit=0", headers=ADM)).status_code == 422 and (await c.get("/purchase-orders?customer_id=nope", headers=ADM)).status_code == 422)
        check("FA can list (invoices.view); no token -> 401", (await c.get("/purchase-orders", headers=FA)).status_code == 200 and (await c.get("/purchase-orders")).status_code == 401)
        check("static routes not shadowed by /{id}", (await c.get("/purchase-orders/reviews", headers=ADM)).status_code == 200 and (await c.get("/purchase-orders/mismatch-reviews", headers=ADM)).status_code == 200)

        # ===================================================== detail
        d = (await c.get(f"/purchase-orders/{by['PO-L1']['purchase_order_id']}", headers=ADM)).json()
        lines = {x["description"]: x for x in d["lines"]}
        check("detail: Laptop ordered 10, invoiced 10 (INV-L1 6 + INV-L3 4), remaining 0", D(lines["Laptop"]["quantity_ordered"]) == 10 and D(lines["Laptop"]["quantity_invoiced"]) == 10 and D(lines["Laptop"]["quantity_remaining"]) == 0, str(lines["Laptop"]))
        check("detail: Monitor ordered 5, invoiced 5 (INV-L3), remaining 0, with its unit price field present", D(lines["Monitor"]["quantity_remaining"]) == 0 and D(lines["Monitor"]["quantity_invoiced"]) == 5 and "unit_price" in lines["Monitor"])
        inv_by = {x["invoice_number"]: x for x in d["invoices"]}
        check("detail: three linked invoices, all po_first, in link order", [x["invoice_number"] for x in d["invoices"]] == ["INV-L1", "INV-L2", "INV-L3"] and all(x["match_direction"] == "po_first" for x in d["invoices"]), str([x["invoice_number"] for x in d["invoices"]]))
        check("detail: INV-L2 on hold and counted 0; INV-L1 counted 6; INV-L3 counted 9", inv_by["INV-L2"]["po_hold"] is True and D(inv_by["INV-L2"]["quantity_counted"]) == 0 and D(inv_by["INV-L1"]["quantity_counted"]) == 6 and D(inv_by["INV-L3"]["quantity_counted"]) == 9, str({k: v["quantity_counted"] for k, v in inv_by.items()}))
        check("detail: customer name, source file name, same list fields", d["customer_name"] == ZEN["customer_name"] and d["source_file_name"] == "po1.docx" and d["fulfillment"] == "Fully invoiced")
        check("unknown PO -> 404; not a uuid -> 422; no token -> 401", (await c.get("/purchase-orders/00000000-0000-0000-0000-000000000009", headers=ADM)).status_code == 404 and (await c.get("/purchase-orders/xyz", headers=ADM)).status_code == 422 and (await c.get(f"/purchase-orders/{d['purchase_order_id']}")).status_code == 401)
        d2 = (await c.get(f"/purchase-orders/{by['PO-L2']['purchase_order_id']}", headers=ADM)).json()
        check("detail of an untouched PO: no invoices, remaining = ordered", d2["invoices"] == [] and D(d2["lines"][0]["quantity_remaining"]) == 2)

        # ===================================================== an override pushes it over
        mm = (await c.get("/purchase-orders/mismatch-reviews", headers=ADM)).json()
        rid = next(x["review_id"] for x in mm if x["invoice_number"] == "INV-L2")
        r = await c.post(f"/purchase-orders/mismatch-reviews/{rid}/override", headers=ADM, json={"reason": "Customer accepted the extra laptops"})
        check("override INV-L2 -> 200", r.status_code == 200, r.text[:120])
        d = (await c.get(f"/purchase-orders/{by['PO-L1']['purchase_order_id']}", headers=ADM)).json()
        lines = {x["description"]: x for x in d["lines"]}
        check("after the override: Laptop invoiced 16, remaining -6, fulfillment Over-invoiced", D(lines["Laptop"]["quantity_invoiced"]) == 16 and D(lines["Laptop"]["quantity_remaining"]) == -6 and d["fulfillment"] == "Over-invoiced" and d["invoices_on_hold"] == 0, f"{lines['Laptop']['quantity_remaining']} {d['fulfillment']}")
        check("... and the list agrees", {x["po_number"]: x["fulfillment"] for x in (await c.get("/purchase-orders", headers=ADM)).json()}["PO-L1"] == "Over-invoiced")
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
