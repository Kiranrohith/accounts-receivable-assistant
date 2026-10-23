import asyncio
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from docx import Document
from sqlalchemy import text

import core.services.documents.intake as intake
import core.services.invoices.fx as invoice_fx
import core.services.invoices.processing as proc
import core.services.purchase_orders.processing as po_proc
import testsupport
from api.rest.app import app
from config.settings import get_settings
from core.services.documents.classification import ClassificationOutcome
from core.services.fx.ecb import FxRate
from core.services.invoices.extraction import ExtractionOutcome
from core.services.purchase_orders.extraction import PurchaseOrderOutcome
from data.clients.db import SessionLocal, engine
from data.repositories.invoices import documents_repo
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder, POFieldConfidences, POLineItem

s = get_settings()
assert "ar_vr" in s.database_url, "refusing to run against a non-test database"
results = []
D = Decimal
ZEN = dict(customer_name="Zenith Softlabs Pvt Ltd", customer_gstin="33AABCZ1234F1Z5")
MER = dict(customer_name="Meridian Tech Solutions Pvt Ltd", customer_gstin="29AABCM5678K1Z3")
UNK = dict(customer_name="Unknown Widgets Ltd", customer_gstin=None)


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


def inv(number, total="10000", lines=(("Laptop", "2"),), who=ZEN, **kw):
    base = dict(
        invoice_number=number, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="INR",
        total_amount=D(total), line_items=[LineItem(description=d, quantity=D(q)) for d, q in lines],
        confidence=FieldConfidences(**{f: 95 for f in FieldConfidences.model_fields}),
    )
    base.update(who)
    base.update(kw)
    return ExtractedInvoice(**base)


def po(number, lines):
    return ExtractedPurchaseOrder(
        po_number=number, currency="INR", total_amount=D("1000"),
        line_items=[POLineItem(description=d, quantity=D(q)) for d, q in lines],
        confidence=POFieldConfidences(**{f: 95 for f in POFieldConfidences.model_fields}), **ZEN,
    )


CASES = {
    # A: an open invoice, a changed version, an identical re-upload
    "a1": inv("V-A", "10000", (("Laptop", "2"),)),
    "a2": inv("V-A", "12000", (("Laptop", "3"),)),
    "a3": inv("V-A", "12000", (("Laptop", "3"),)),  # same values as a2, other file
    # B: customer changes (review), simultaneous approvals, reject
    "b1": inv("V-B"), "b2": inv("V-B", who=MER),
    "b3": inv("V-B2"), "b4": inv("V-B2", who=MER),
    "b5": inv("V-B3"), "b6": inv("V-B3", who=MER),
    # C: partly paid
    "c1": inv("V-C"), "c2": inv("V-C", "12000"), "c3": inv("V-C", "3000"), "c4": inv("V-C", "12000", due_date=date(2026, 12, 1)),
    # D: paid (closed)
    "d1": inv("V-D"), "d2": inv("V-D", "12000"), "d3": inv("V-D"),
    # E: disputed
    "e1": inv("V-E"), "e2": inv("V-E", "11000"),
    # F: linked to a purchase order
    "po1": po("PO-F", (("Laptop", "10"),)),
    "f1": inv("V-F", "6000", (("Laptop", "6"),), po_number="PO-F"),
    "f2": inv("V-F", "8000", (("Laptop", "8"),), po_number="PO-F"),
    "f3": inv("V-F", "12000", (("Laptop", "12"),), po_number="PO-F"),
    "f4": inv("V-F", "5000", (("Laptop", "5"),), po_number="PO-F"),
    # G: currency / date
    "g1": inv("V-G"), "g2": inv("V-G", currency="USD", total_amount=D("100")),
    "g3": inv("V-G2", "100", currency="USD"), "g4": inv("V-G2", "100", currency="USD", invoice_date=date(2026, 10, 7)),
    "g5": inv("V-G3"), "g6": inv("V-G3", invoice_date=date(2026, 10, 7)),
    # H: a doubtful document with an already-saved number, approved in the extraction review
    "h1": inv("V-H"), "h2": inv("V-H", "13000", who=UNK), "h3": inv("V-H", "13000", who=UNK),
}


def find(markdown):
    return next(k for k in sorted(CASES, key=len, reverse=True) if f"CASE:{k}\n" in markdown + "\n")


async def classify(markdown):
    kind = DocumentClass.PO if find(markdown).startswith("po") else DocumentClass.INVOICE
    return ClassificationOutcome(DocClassification(doc_class=kind, confidence=95, reason="test"), "fake", 1)


async def extract_invoice(markdown):
    return ExtractionOutcome(CASES[find(markdown)], "fake", 1)


async def extract_po(markdown):
    return PurchaseOrderOutcome(CASES[find(markdown)], "fake", 1)


async def fake_fx(currency, on_date):
    return FxRate(currency, on_date, on_date, D("96.5") if on_date.day < 6 else D("97.25"), D("108"), D("1.1"))


intake.classify_document = classify
proc.extract_invoice = extract_invoice
po_proc.extract_purchase_order = extract_po
invoice_fx.fetch_rate_to_inr = fake_fx


async def main():
    tmp = Path(tempfile.mkdtemp())
    for k in CASES:
        d = Document()
        d.add_paragraph(f"CASE:{k}")
        d.save(tmp / f"{k}.docx")
    worker = await testsupport.start_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        await c.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (cust(customer_name=ZEN["customer_name"], email="ap@zenith.com", gstin=ZEN["customer_gstin"], deducts_tds=True),
                     cust(customer_name=MER["customer_name"], email="ap@meridian.com", gstin=MER["customer_gstin"], deducts_tds=False)):
            assert (await c.post("/customers", headers=ADM, json=body)).status_code == 201
        zen_id = next(x["id"] for x in (await c.get("/customers", headers=ADM)).json() if x["customer_name"] == ZEN["customer_name"])
        mer_id = next(x["id"] for x in (await c.get("/customers", headers=ADM)).json() if x["customer_name"] == MER["customer_name"])

        async def q(sql, **p):
            async with SessionLocal() as db:
                return (await db.execute(text(sql), p)).all()

        async def one(sql, **p):
            rows = await q(sql, **p)
            return rows[0][0] if rows else None

        async def exec_(sql, **p):
            async with SessionLocal() as db:
                await db.execute(text(sql), p)
                await db.commit()

        ids: dict[str, str] = {}

        async def up(key, H=ADM):
            r = await c.post("/documents/upload", headers=H, files={"file": (f"{key}.docx", (tmp / f"{key}.docx").read_bytes())})
            assert r.status_code == 202, (key, r.text)
            ids[key] = r.json()["source_document_id"]
            return (await c.get(f"/documents/uploads/{ids[key]}", headers=ADM)).json()

        inv_row = lambda number: q("select id::text, version, status::text, customer_id::text, amount_inr, tds_amount, collectible_total, amount_foreign, fx_rate_invoice, due_date::text, line_items_version, source_document_id::text, po_hold from invoices where invoice_number=:n", n=number)

        # ===================================================== A. open invoice: changed version applies
        st = await up("a1")
        check("v1 created", st["status"] == "Invoice created" and st["invoice_version"] == 1, st["status"])
        invoice_id = st["invoice_id"]
        st = await up("a2")
        check("changed version of an Open invoice -> 'New version applied' automatically", st["status"] == "New version applied" and st["disposition"] == "version_applied" and st["invoice_version"] == 2 and st["invoice_id"] == invoice_id, f"{st['status']} {st['disposition_reasons']}")
        r = (await inv_row("V-A"))[0]
        check("invoice updated: version 2, amount 12000, TDS 1200, collectible 10800", r[1] == 2 and r[4] == D("12000.00") and r[5] == D("1200.00") and r[6] == D("10800.00"), str(r))
        d = (await c.get(f"/invoices/{invoice_id}", headers=ADM)).json()
        check("invoice detail shows the NEW lines (qty 3) and the new document", [(x["description"], D(x["quantity"])) for x in d["line_items"]] == [("Laptop", D("3"))] and d["version"] == 2, str(d["line_items"]))
        snap = await q("select version_no, snapshot, change_summary, source_document_id::text from invoice_versions where invoice_id=:i", i=invoice_id)
        check("the old state is saved in invoice_versions (version 1: total 10000, 2 laptops)", len(snap) == 1 and snap[0][0] == 1 and D(snap[0][1]["amount_inr"]) == D("10000.00") and snap[0][1]["line_items"][0]["quantity"] in ("2", "2.0000"), str(snap[0][1] if snap else None)[:200])
        check("...with what changed, and the document it came from", {c_["field"] for c_ in snap[0][2]["changes"]} == {"total_amount", "line_items"} and snap[0][3] == ids["a1"], str(snap[0][2])[:150])
        check("old line rows are kept (version 1 and 2 both exist)", (await one("select count(*) from invoice_line_items where invoice_id=:i", i=invoice_id)) == 2)
        check("invoice timeline records the new version", (await one("select count(*) from state_log where entity_id=:i and reason like 'Version 2 applied%'", i=invoice_id)) == 1)
        old = await up.__wrapped__("a1") if False else (await c.get(f"/documents/uploads/{ids['a1']}", headers=ADM)).json()
        check("the first document still reads 'Invoice created' (version 1)", old["status"] == "Invoice created" and old["invoice_version"] == 1, f"{old['status']} {old['invoice_version']}")
        new_doc = (await c.get(f"/documents/uploads/{ids['a2']}", headers=ADM)).json()
        check("the new document reads 'New version applied' (version 2)", new_doc["status"] == "New version applied" and new_doc["invoice_version"] == 2)

        # identical values, different file
        st = await up("a3")
        check("same values from another file -> rejected as a duplicate, nothing changes", st["status"] == "Rejected" and "Identical" in " ".join(st["disposition_reasons"]), str(st["disposition_reasons"]))
        rj = (await c.get("/invoices/rejected", headers=ADM)).json()
        dup = next(x for x in rj if x["source_document_id"] == ids["a3"])
        check("...pointing at the saved invoice's document, reason code duplicate_invoice", dup["duplicate_of_source_document_id"] == ids["a2"] and dup["reason_codes"] == ["duplicate_invoice"], str(dup["duplicate_of_source_document_id"]))
        check("...and no new version was made", (await inv_row("V-A"))[0][1] == 2 and (await one("select count(*) from invoice_versions where invoice_id=:i", i=invoice_id)) == 1)

        # ===================================================== B. customer changed -> review -> approve / reject
        await up("b1")
        st = await up("b2")
        check("customer changed -> 'Needs review' (version review), invoice untouched", st["status"] == "Needs review" and st["review_kind"] == "version" and (await inv_row("V-B"))[0][1] == 1, f"{st['status']} {st['review_kind']}")
        rid = st["review_id"]
        lst = (await c.get("/invoices/version-reviews", headers=ADM)).json()
        item = next(x for x in lst if x["review_id"] == rid)
        check("the review lists the reason and the differences", item["can_approve"] and any("customer changed" in r_ for r_ in item["reasons"]) and any(c_["field"] == "customer" and c_["new"] == MER["customer_name"] for c_ in item["changes"]), str(item["reasons"]))
        det = (await c.get(f"/invoices/version-reviews/{rid}", headers=ADM)).json()
        check("detail shows the saved invoice and the proposed values", det["current"]["customer_name"] == ZEN["customer_name"] and det["proposed"]["customer_name"] == MER["customer_name"] and det["base_version"] == 1)
        check("not in the extraction-review list", rid not in [x["review_id"] for x in (await c.get("/invoices/reviews", headers=ADM)).json()])
        check("another user: 403 on view, approve and reject", (await c.get(f"/invoices/version-reviews/{rid}", headers=FA)).status_code == 403 and (await c.post(f"/invoices/version-reviews/{rid}/approve", headers=FA, json={"reason": "x"})).status_code == 403 and (await c.post(f"/invoices/version-reviews/{rid}/reject", headers=FA, json={"reason": "x"})).status_code == 403)
        check("approve needs a reason (422)", (await c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={})).status_code == 422)
        r = await c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={"reason": "Customer was billed wrongly"})
        row = (await inv_row("V-B"))[0]
        check("approve -> applied: customer is now Meridian, version 2, TDS recomputed for Meridian (none)", r.status_code == 200 and r.json()["version"] == 2 and row[3] == mer_id and row[5] == D("0.00") and row[6] == D("10000.00"), f"{r.text[:100]} {row[3:7]}")
        check("deciding again -> 409; status reads New version applied", (await c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={"reason": "again"})).status_code == 409 and (await (c.get(f"/documents/uploads/{ids['b2']}", headers=ADM))).json()["status"] == "New version applied")

        await up("b3")
        st = await up("b4")
        rid = st["review_id"]
        rs = await asyncio.gather(
            c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={"reason": "one"}),
            c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={"reason": "two"}),
        )
        check("two simultaneous approvals: one 200, one 409", sorted(x.status_code for x in rs) == [200, 409], str([x.status_code for x in rs]))
        check("...and exactly one new version", (await inv_row("V-B2"))[0][1] == 2)

        await up("b5")
        st = await up("b6")
        rid = st["review_id"]
        r = await c.post(f"/invoices/version-reviews/{rid}/reject", headers=ADM, json={})
        check("reject needs a reason (422)", r.status_code == 422)
        r = await c.post(f"/invoices/version-reviews/{rid}/reject", headers=ADM, json={"reason": "Keep the original customer"})
        st = (await c.get(f"/documents/uploads/{ids['b6']}", headers=ADM)).json()
        check("reject -> Rejected with the reviewer's reason; the saved invoice is untouched", r.status_code == 200 and st["status"] == "Rejected" and "Keep the original customer" in " ".join(st["disposition_reasons"]) and (await inv_row("V-B3"))[0][1] == 1, str(st["disposition_reasons"]))
        done = (await c.get("/invoices/version-reviews?status=Approved", headers=ADM)).json()
        check("Approved filter lists the decided ones", {ids["b2"], ids["b4"]} <= {x["source_document_id"] for x in done})

        # ===================================================== C. partly paid
        await up("c1")
        await exec_("update invoices set status='PartlyPaid', amount_paid=4000 where invoice_number='V-C'")
        st = await up("c2")
        check("PartlyPaid + changed amount -> review ('payments were already received')", st["status"] == "Needs review" and st["review_kind"] == "version" and any("already received" in r_ for r_ in st["disposition_reasons"] + (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()["reasons"]), st["status"])
        rid = st["review_id"]
        r = await c.post(f"/invoices/version-reviews/{rid}/approve", headers=ADM, json={"reason": "Customer agreed the new amount"})
        row = (await inv_row("V-C"))[0]
        check("approving it applies the new amount; paid amount and status are kept", r.status_code == 200 and row[4] == D("12000.00") and row[6] == D("10800.00") and row[2] == "PartlyPaid" and (await one("select amount_paid from invoices where invoice_number='V-C'")) == D("4000.00"), str(row))
        st = await up("c3")
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "try"})
        check("a new amount BELOW what was already paid cannot be approved (422), stays pending", r.status_code == 422 and "already paid" in r.text and (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()["status"] == "Pending", r.text[:140])
        await c.post(f"/invoices/version-reviews/{st['review_id']}/reject", headers=ADM, json={"reason": "Wrong amount"})
        st = await up("c4")
        check("PartlyPaid + only the due date changed -> applied automatically", st["status"] == "New version applied" and (await inv_row("V-C"))[0][9] == "2026-12-01", st["status"])

        # ===================================================== D. paid (closed)
        await up("d1")
        await exec_("update invoices set status='Paid', amount_paid=collectible_total where invoice_number='V-D'")
        st = await up("d2")
        det = (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()
        check("Paid invoice + changed document -> review saying it is already closed", st["status"] == "Needs review" and det["can_approve"] is False and "already paid (closed)" in det["reasons"][0], str(det["reasons"]))
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "force it"})
        check("approving it is refused (422); the invoice is untouched", r.status_code == 422 and "closed" in r.text and (await inv_row("V-D"))[0][1] == 1, r.text[:120])
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/reject", headers=ADM, json={"reason": "Already settled"})
        check("rejecting it works", r.status_code == 200)
        st = await up("d3")
        check("Paid invoice + identical document -> plain duplicate rejection", st["status"] == "Rejected" and "Identical" in " ".join(st["disposition_reasons"]))

        # ===================================================== E. disputed
        await up("e1")
        await exec_("update invoices set is_disputed=true where invoice_number='V-E'")
        st = await up("e2")
        det = (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()
        check("a disputed invoice's change goes to review", st["status"] == "Needs review" and any("under dispute" in r_ for r_ in det["reasons"]))

        # ===================================================== F. linked to a purchase order
        await up("po1")
        st = await up("f1")
        po_id = (await c.get("/purchase-orders", headers=ADM)).json()[0]["purchase_order_id"]
        pod = lambda: c.get(f"/purchase-orders/{po_id}", headers=ADM)
        check("setup: invoice passes the PO check (6 of 10 consumed)", st["status"] == "Invoice created" and D((await pod()).json()["lines"][0]["quantity_invoiced"]) == 6)
        st = await up("f2")
        det = (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()
        check("a changed version of a PO-linked invoice goes to review", st["status"] == "Needs review" and any("purchase order PO-F" in r_ for r_ in det["reasons"]))
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "Customer ordered more"})
        check("approve -> applied; the old consumption is reversed and the new lines re-checked: 8 consumed, no hold", r.status_code == 200 and r.json()["po_hold"] is False and D((await pod()).json()["lines"][0]["quantity_invoiced"]) == 8, r.text[:140])
        st = await up("f3")
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "Even more"})
        check("approve a version that exceeds the order -> applied but ON HOLD with a PO-mismatch review", r.status_code == 200 and r.json()["po_hold"] is True and (await inv_row("V-F"))[0][12] is True, r.text[:160])
        d = (await pod()).json()
        check("the held invoice consumes nothing on the PO (ledger net zero)", D(d["lines"][0]["quantity_invoiced"]) == 0 and d["invoices_on_hold"] == 1, str(d["lines"][0]))
        st = await up("f4")
        check("while a decision is pending on the invoice, a new version is blocked and rejected", st["status"] == "Rejected" and "already waiting" in " ".join(st["disposition_reasons"]) and (await inv_row("V-F"))[0][1] == 3, str(st["disposition_reasons"]))

        # ===================================================== G. currency / date
        await up("g1")
        st = await up("g2")
        check("currency changed -> review", st["status"] == "Needs review" and any("currency changed" in r_ for r_ in (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()["reasons"]))
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "It is a USD invoice"})
        row = (await inv_row("V-G"))[0]
        check("approving converts it at the ECB rate for the invoice date (96.5)", r.status_code == 200 and row[7] == D("100.00") and row[8] == D("96.50000000") and row[4] == D("9650.00"), str(row[4:9]))
        await up("g3")
        await exec_("update invoices set invoice_currency='USD', amount_foreign=100, fx_rate_invoice=96.5, amount_inr=9650, tds_amount=965, collectible_total=8685 where invoice_number='V-G2'")
        st = await up("g4")
        check("a FOREIGN invoice's date changed -> review (the frozen rate may not fit)", st["status"] == "Needs review" and any("exchange rate" in r_ for r_ in (await c.get(f"/invoices/version-reviews/{st['review_id']}", headers=ADM)).json()["reasons"]))
        r = await c.post(f"/invoices/version-reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "Date corrected"})
        check("...approving fetches the rate for the NEW date (97.25)", r.status_code == 200 and (await inv_row("V-G2"))[0][8] == D("97.25000000"), r.text[:100])
        await up("g5")
        st = await up("g6")
        check("an INR invoice's date change needs no rate -> applied automatically", st["status"] == "New version applied")

        # ===================================================== H. doubtful document, same number, extraction review
        await up("h1")
        st = await up("h2")
        check("a doubtful document (unknown customer) with a saved number -> extraction review that explains it", st["status"] == "Needs review" and st["review_kind"] == "extraction" and any("handled as a new version" in r_ for r_ in st["disposition_reasons"]), str(st["disposition_reasons"]))
        r = await c.post(f"/invoices/reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "It is Zenith's", "customer_id": zen_id})
        check("approving it goes through the same version check -> applied as version 2 (never a raw 409)", r.status_code == 200 and r.json()["version"] == 2 and r.json()["invoice_id"] and (await inv_row("V-H"))[0][4] == D("13000.00"), r.text[:140])
        st = await up("h3")
        r = await c.post(f"/invoices/reviews/{st['review_id']}/approve", headers=ADM, json={"reason": "again", "customer_id": zen_id})
        check("approving a document identical to the saved invoice -> 409 (reject it instead)", r.status_code == 409 and "Identical" in r.text, r.text[:140])

        # ===================================================== I. identical FILE (same hash)
        before = len((await c.get("/invoices/rejected?kind=duplicate", headers=ADM)).json())
        r = await c.post("/documents/upload", headers=ADM, files={"file": ("renamed.docx", (tmp / "a2.docx").read_bytes())})
        check("the same file again -> 409", r.status_code == 409, r.text[:100])
        dl = (await c.get("/invoices/rejected?kind=duplicate", headers=ADM)).json()
        mine = next((x for x in dl if x["file_name"] == "renamed.docx"), None)
        check("...and the attempt is on the rejected list as a duplicate of the original", len(dl) == before + 1 and mine and mine["duplicate_of_source_document_id"] == ids["a2"] and mine["source_document_id"] is None and mine["kind"] == "duplicate" and mine["reason_codes"] == ["duplicate_upload"], str(mine))
        check("default list hides it; kind=all shows it", all(x["kind"] != "duplicate" for x in (await c.get("/invoices/rejected", headers=ADM)).json()) and any(x["kind"] == "duplicate" for x in (await c.get("/invoices/rejected?kind=all", headers=ADM)).json()))
        check("nothing new stored for the duplicate", (await one("select count(*) from source_documents where file_name='renamed.docx'")) == 0)

        # ===================================================== J. a re-run does not repeat a version apply
        async with SessionLocal() as db:
            seen = await documents_repo.has_outcome_since(db, __import__("uuid").UUID(ids["a2"]), datetime.now(UTC) - timedelta(hours=1))
            not_seen = await documents_repo.has_outcome_since(db, __import__("uuid").UUID(ids["a2"]), datetime.now(UTC) + timedelta(hours=1))
        check("the re-run guard recognises a document that produced a new version", seen is True and not_seen is False)
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
