"""GET /documents/my-uploads: the person's OWN uploads, newest first, a page at a time with the total, each with its one
status label and, where a document was rejected or needs review, the reasons. The reader is a script: no model."""

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
import core.services.invoices.fx as invoice_fx
import core.services.invoices.processing as proc
from api.rest.app import app
from config.settings import get_settings
from core.services.documents.classification import ClassificationOutcome
from core.services.fx.ecb import FxRate
from core.services.invoices.extraction import ExtractionOutcome
from data.clients.db import engine
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem

s = get_settings()
assert "ar_myup" in s.database_url, "refusing to run against a non-test database"
results = []
ALL = {f: 95 for f in FieldConfidences.model_fields}


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


def invoice(number, customer, gstin):
    return ExtractedInvoice(
        invoice_number=number, customer_name=customer, customer_gstin=gstin, invoice_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31), currency="INR", total_amount=Decimal("10000"), confidence=FieldConfidences(**ALL),
        line_items=[LineItem(description="Consulting", quantity=Decimal("1"))],
    )


async def fake_extract(markdown):
    if "CASE:review" in markdown:  # a customer we do not have: a person must look
        return ExtractionOutcome(invoice("INV-MU-REV", "Unknown Corp Ltd", None), "fake-model", 1)
    n = markdown.split("CASE:ok")[1][:1]
    return ExtractionOutcome(invoice(f"INV-MU-{n}", "Zenith Softlabs Pvt Ltd", "33AABCZ1234F1Z5"), "fake-model", 1)


async def fake_classify(markdown):
    kind = DocumentClass.OTHER if "CASE:other" in markdown else DocumentClass.INVOICE
    return ClassificationOutcome(DocClassification(doc_class=kind, confidence=95, reason="a menu, not an invoice"), "fake-model", 1)


async def fake_fx(currency, on_date):
    return FxRate(currency, on_date, on_date, Decimal("96.5"), Decimal("108"), Decimal("1.1"))


intake.classify_document = fake_classify
proc.extract_invoice = fake_extract
invoice_fx.fetch_rate_to_inr = fake_fx


async def main():
    import testsupport

    await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())

    def doc(name, text):
        d = Document()
        d.add_paragraph(f"{text}\nsome document text for {name}")
        d.save(tmp / name)
        return name

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=600) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email in (("Fa Mine", "fa.mine@example.com"), ("Fa Other", "fa.other@example.com")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.mine@example.com', 'pass12345')).json()['access_token']}"}
        FB = {"Authorization": f"Bearer {(await login('fa.other@example.com', 'pass12345')).json()['access_token']}"}
        assert (await c.post("/customers", headers=ADM, json={"customer_name": "Zenith Softlabs Pvt Ltd", "email": "ap@zenith.com", "gstin": "33AABCZ1234F1Z5",
                                                              "contact": "C", "country": "India", "billing_currency": "INR", "deducts_tds": False})).status_code == 201

        async def up(H, name):
            return await c.post("/documents/upload", headers=H, files={"file": (name, (tmp / name).read_bytes())})

        ids = {}
        for label, H, name, text in (
            ("ok1", FA, doc("ok1.docx", "CASE:ok1"), None), ("ok2", FA, doc("ok2.docx", "CASE:ok2"), None),
            ("review", FA, doc("review.docx", "CASE:review"), None), ("other", FA, doc("other.docx", "CASE:other"), None),
            ("theirs", FB, doc("theirs.docx", "CASE:ok3"), None),
        ):
            r = await up(H, name)
            assert r.status_code == 202, (label, r.text)
            ids[label] = r.json()["source_document_id"]
            await asyncio.sleep(0.05)  # distinct created_at, so the order is not a coin toss

        # ============================================================ who sees what
        check("no token -> 401", (await c.get("/documents/my-uploads")).status_code == 401)
        r = await c.get("/documents/my-uploads", headers=FA)
        body = r.json()
        check("a page with the total: {items, total}", r.status_code == 200 and set(body) == {"items", "total"})
        check("FA sees exactly its own 4, not the other FA's", body["total"] == 4 and len(body["items"]) == 4 and ids["theirs"] not in [i["source_document_id"] for i in body["items"]])
        check("newest first", [i["source_document_id"] for i in body["items"]] == [ids["other"], ids["review"], ids["ok2"], ids["ok1"]])
        check("the other FA sees only theirs", [i["source_document_id"] for i in (await c.get("/documents/my-uploads", headers=FB)).json()["items"]] == [ids["theirs"]])
        r = await c.get("/documents/my-uploads", headers=ADM)
        check("the admin, who uploaded nothing, sees an empty page with total 0", r.json() == {"items": [], "total": 0})
        check("the old list still shows everyone's", len((await c.get("/documents/uploads", headers=ADM)).json()) == 5)

        # ============================================================ the label and the reasons
        by_id = {i["source_document_id"]: i for i in body["items"]}
        check("a clean invoice: status 'Invoice created', kind invoice, its number and the matched customer name",
              by_id[ids["ok1"]]["status"] == "Invoice created" and by_id[ids["ok1"]]["document_kind"] == "invoice"
              and by_id[ids["ok1"]]["invoice_number"] == "INV-MU-1" and by_id[ids["ok1"]]["invoice_id"], str(by_id[ids["ok1"]]["status"]))
        rv = by_id[ids["review"]]
        check("an unknown customer: 'Needs review' with the review id and kind, so the screen can open it", rv["status"] == "Needs review" and rv["review_id"] and rv["review_kind"] == "extraction", str((rv["status"], rv["review_kind"])))
        check("and says why, in words", rv["disposition_reasons"] and all(isinstance(x, str) and x for x in rv["disposition_reasons"]), str(rv["disposition_reasons"]))
        ot = by_id[ids["other"]]
        check("a document that is neither: 'Rejected' with the reason in the list row itself", ot["status"] == "Rejected" and len(ot["disposition_reasons"]) >= 1, str(ot["disposition_reasons"]))
        check("a clean invoice has no reasons", by_id[ids["ok2"]]["disposition_reasons"] == [])

        # ============================================================ paging
        r = await c.get("/documents/my-uploads", headers=FA, params={"limit": 2, "offset": 0})
        first = r.json()
        r = await c.get("/documents/my-uploads", headers=FA, params={"limit": 2, "offset": 2})
        second = r.json()
        check("paging: 2 + 2, total 4 each time, no overlap",
              len(first["items"]) == 2 and len(second["items"]) == 2 and first["total"] == second["total"] == 4
              and not ({i["source_document_id"] for i in first["items"]} & {i["source_document_id"] for i in second["items"]}))
        check("past the end: empty items, total unchanged", (await c.get("/documents/my-uploads", headers=FA, params={"offset": 40})).json() == {"items": [], "total": 4})
        check("limit above 100 -> 422", (await c.get("/documents/my-uploads", headers=FA, params={"limit": 101})).status_code == 422)
        check("limit 0 -> 422", (await c.get("/documents/my-uploads", headers=FA, params={"limit": 0})).status_code == 422)

        # ============================================================ the upload errors the screen will show
        r = await up(FA, "ok1.docx")
        check("the same file again -> 409 naming the first upload", r.status_code == 409 and ids["ok1"] in r.json()["detail"], r.text[:120])
        r = await c.post("/documents/upload", headers=FA, files={"file": ("empty.pdf", b"")})
        check("an empty file -> 422 with a plain reason", r.status_code == 422 and r.json()["detail"], r.text[:120])
        r = await c.post("/documents/upload", headers=FA, files={"file": ("notes.txt", b"hello")})
        check("an unsupported type -> 422 listing what is supported", r.status_code == 422 and "supported" in r.json()["detail"], r.text[:140])
        check("none of those rejected files appear in my uploads", (await c.get("/documents/my-uploads", headers=FA)).json()["total"] == 4)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
