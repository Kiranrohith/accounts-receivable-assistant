"""D3 with the REAL reader: complaint documents uploaded through POST /documents/upload.
Real worker, real LLM (classify + extract), real database. Throwaway DB only."""

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

from api.rest.app import app
from config.settings import get_settings
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice

D = Decimal
s = get_settings()
assert "ar_dis3l" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


def letter(path, lines, title=None, table=None):
    d = Document()
    if title:
        d.add_heading(title, 1)
    for ln in lines:
        d.add_paragraph(ln)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        t.style = "Table Grid"
        for r, row in enumerate(table):
            for i, v in enumerate(row):
                t.rows[r].cells[i].text = v
    d.save(path)


async def db_rows(query, **params):
    async with SessionLocal() as db:
        return (await db.execute(text(query), params)).all()


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())
    async with SessionLocal() as db:
        zen = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="A", email="ap@zenith.com", gstin="33AABCZ1234F1Z5",
                       deducts_tds=False, billing_currency="INR", country="India")
        mer = Customer(customer_name="Meridian Tech Solutions Pvt Ltd", contact="B", email="ap@meridian.com", gstin="29AABCM5678K1Z3",
                       deducts_tds=False, billing_currency="INR", country="India")
        db.add_all([zen, mer])
        await db.flush()
        for cust, nums in ((zen, ["ZEN-INV-201", "ZEN-INV-202", "ZEN-INV-203", "ZEN-INV-204"]), (mer, ["MER-INV-301"])):
            for n in nums:
                db.add(Invoice(customer_id=cust.id, invoice_number=n, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                               invoice_currency="INR", amount_inr=D("50000"), collectible_total=D("50000")))
        await db.commit()
        ZEN, MER = zen.id, mer.id

    letter(tmp / "damaged.docx", [
        "From: Priya Nair, Accounts, Zenith Softlabs Pvt Ltd <accounts@zenith.com>",
        "To: Genworx Technologies Pvt Ltd",
        "Subject: Damaged goods against invoice ZEN-INV-201",
        "Hello, the 2 laptops delivered against your invoice ZEN-INV-201 arrived with cracked screens. "
        "We are raising a formal complaint and would like a replacement or a credit note. Please confirm how you will proceed.",
    ])
    letter(tmp / "withhold.docx", [
        "Zenith Softlabs Pvt Ltd, Bengaluru, GSTIN 33AABCZ1234F1Z5",
        "To: Genworx Technologies Pvt Ltd",
        "Subject: Objection to invoice ZEN-INV-202 - payment on hold",
        "Your invoice ZEN-INV-202 bills the support service at INR 25,000 per month, but our signed agreement says INR 22,000. "
        "We will not release payment for this invoice until you send a corrected invoice.",
    ])
    letter(tmp / "two_invoices.docx", [
        "From: accounts@zenith.com (Zenith Softlabs Pvt Ltd)",
        "Subject: Wrong GST on invoices ZEN-INV-203 and ZEN-INV-204",
        "Both invoices ZEN-INV-203 and ZEN-INV-204 charge GST at 18%, but the services are exempt under our agreement. Please reissue both.",
    ])
    letter(tmp / "stranger.docx", [
        "From: Rahul <rahul@brightstar-foods.in>",
        "Subject: Complaint about late delivery",
        "Your delivery was four days late and our production line stopped. We expect compensation. Bright Star Foods Pvt Ltd.",
    ])
    letter(tmp / "short_pay.docx", [
        "Meridian Tech Solutions Pvt Ltd",
        "To: Genworx Technologies Pvt Ltd",
        "Payment date: 15/10/2026     UTR: HDFC20261015771",
    ], title="PAYMENT ADVICE", table=[("Invoice No", "Invoice amount", "Amount paid"), ("MER-INV-301", "50000.00", "45000.00")])
    d = Document()
    d.add_heading("PAYMENT ADVICE", 1)
    d.add_paragraph("From: Meridian Tech Solutions Pvt Ltd <ap@meridian.com>\nTo: Genworx Technologies Pvt Ltd")
    d.add_paragraph("Payment date: 15/10/2026   UTR: HDFC20261015771")
    d.add_paragraph("Invoice MER-INV-301 total INR 50,000. We are paying INR 45,000 only. INR 5,000 is withheld because "
                    "part of the goods were of poor quality; we will release it after you reply to our complaint.")
    d.save(tmp / "short_pay.docx")
    letter(tmp / "request.docx", ["Hi, can you please send me a copy of invoice ZEN-INV-201? Thanks, Priya (Zenith accounts)"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=900) as c:
        testsupport.install(c)
        H = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=H, json={"name": "Fa A", "email": "fa.a@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        ids = {}

        async def up(name):
            r = await c.post("/documents/upload", headers=H, files={"file": (name, (tmp / name).read_bytes())})
            assert r.status_code == 202, (name, r.text)
            ids[name] = r.json()["source_document_id"]
            return (await c.get(f"/documents/uploads/{ids[name]}", headers=H)).json()

        async def disputes_of(name):
            async with SessionLocal() as db:
                return (await db.execute(text(
                    "select d.id, d.status::text, d.category::text, d.customer_id, d.payment_related, i.invoice_number, d.classification_confidence "
                    "from disputes d left join invoices i on i.id = d.invoice_id where d.source_document_id=:s order by i.invoice_number nulls first"),
                    {"s": ids[name]})).all()

        u = await up("damaged.docx")
        ds = await disputes_of("damaged.docx")
        check("damaged: read as a complaint, one dispute", u["document_kind"] == "dispute" and len(ds) == 1, f"{u['document_kind']} {u['status']} {u.get('error')} {len(ds)}")
        check("damaged: customer Zenith, invoice ZEN-INV-201, Quality, not payment related", ds and ds[0][3] == ZEN and ds[0][5] == "ZEN-INV-201" and ds[0][2] == "Quality" and ds[0][4] is False, str(ds))
        check("damaged: confident -> Assigned to the FA", ds and ds[0][1] == "Assigned" and u["status"] == "Dispute created", f"{ds and ds[0][1]} {u['status']} conf={ds and ds[0][6]}")

        # the first clarification is drafted by the real model from our records (and sent when it is safe)
        for _ in range(240):
            jobs = (await db_rows("select state::text from jobs where entity_id=:d and job_type='dispute_clarification'", d=str(ds[0][0]))) if ds else []
            if jobs and all(j[0] in ("Succeeded", "Failed", "DeadLettered") for j in jobs):
                break
            await asyncio.sleep(0.5)
        out = await db_rows("select kind::text, body from dispute_messages where dispute_id=:d and kind in ('draft','sent')", d=str(ds[0][0]))
        check("damaged: a clarification was written by the real model and mentions the invoice", len(out) == 1 and "ZEN-INV-201" in out[0][1], str(out)[:200])
        print("CLARIFICATION", out[0][0] if out else None, (out[0][1][:400] if out else ""))

        u = await up("withhold.docx")
        ds = await disputes_of("withhold.docx")
        check("withhold: Zenith, ZEN-INV-202, Pricing, payment related", ds and ds[0][3] == ZEN and ds[0][5] == "ZEN-INV-202" and ds[0][2] == "Pricing" and ds[0][4] is True, str(ds))

        u = await up("two_invoices.docx")
        ds = await disputes_of("two_invoices.docx")
        check("two invoices: one dispute per invoice, TaxDiscrepancy", [d[5] for d in ds] == ["ZEN-INV-203", "ZEN-INV-204"] and all(d[2] == "TaxDiscrepancy" for d in ds), str([(d[5], d[2]) for d in ds]))

        u = await up("stranger.docx")
        ds = await disputes_of("stranger.docx")
        check("stranger: an unknown company -> a customer-less dispute waiting in the queue", len(ds) == 1 and ds[0][3] is None and ds[0][1] == "Raised", str(ds))
        check("stranger: the reader suggested Delivery", ds and ds[0][2] == "Delivery", str(ds and ds[0][2]))

        u = await up("short_pay.docx")
        ds = await disputes_of("short_pay.docx")
        async with SessionLocal() as db:
            p = (await db.execute(text("select state::text, created_at, customer_id from payments where source_document_id=:d"), {"d": ids["short_pay.docx"]})).first()
        check("short pay: a payment AND a dispute from one document", p is not None and len(ds) == 1 and u["document_kind"] == "payment", f"{p} {ds} {u['document_kind']}")
        check("short pay: the dispute is for MER-INV-301, Quality, payment related", ds and ds[0][5] == "MER-INV-301" and ds[0][2] == "Quality" and ds[0][4] is True, str(ds))
        check("short pay: dispute step ran before the payment steps", [x["step_name"] for x in (await c.get(f"/documents/uploads/{ids['short_pay.docx']}", headers=H)).json()["steps"]][:5] == ["parse", "classify", "dispute_extract", "dispute_identify", "dispute_route"])

        u = await up("request.docx")
        check("a plain request for a copy is not a dispute (rejected as other)", u["disposition"] == "rejected" and not await disputes_of("request.docx"), f"{u['status']} {u['disposition']}")

        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("the money checker holds", found == [], str(found))

    await worker.stop()
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
