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
from data.clients.db import SessionLocal, engine
from data.models.postgres import Invoice, InvoiceLineItem

s = get_settings()
assert "ar_po2" in s.database_url, "refusing to run against a non-test database"
OURS = "33AAHCG2210A1Z7"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:300])


def make_po(path, *, number="PO-1001", title="PURCHASE ORDER", buyer="Zenith Softlabs Pvt Ltd",
            gstin="33AABCZ1234F1Z5", lines=(("Laptop", "10", "50000.00"), ("Monitor", "5", "12000.00")),
            total="560000.00"):
    d = Document()
    d.add_heading(title, 1)
    d.add_paragraph(f"{buyer}\nBengaluru" + (f"\nGSTIN: {gstin}" if gstin else ""))
    if number:
        d.add_paragraph(f"PO No: {number}")
    d.add_paragraph("PO Date: 02/10/2026")
    d.add_paragraph(f"Supplier: Genworx Technologies Pvt Ltd, GSTIN {OURS}")
    t = d.add_table(rows=1 + len(lines), cols=3)
    t.style = "Table Grid"
    for i, h in enumerate(("Description", "Qty", "Unit price (INR)")):
        t.rows[0].cells[i].text = h
    for r, row in enumerate(lines, start=1):
        for i, v in enumerate(row):
            t.rows[r].cells[i].text = v
    d.add_paragraph(f"Order total INR {total}")
    d.save(path)


async def seed_invoice(customer_id, number, po_number, lines):
    """An invoice created before its PO exists (own commit per invoice so created_at differs)."""
    async with SessionLocal() as db:
        inv = Invoice(
            customer_id=customer_id, invoice_number=number, invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 31), invoice_currency="INR", po_number=po_number,
            amount_inr=Decimal("1000"), collectible_total=Decimal("900"),
        )
        db.add(inv)
        await db.flush()
        db.add_all(InvoiceLineItem(invoice_id=inv.id, description=d, quantity=Decimal(q)) for d, q in lines)
        await db.commit()
        return str(inv.id)


async def wait_done(c, H, ids, timeout=300):
    for _ in range(timeout):
        lst = {u["source_document_id"]: u for u in (await c.get("/documents/uploads", headers=H)).json()}
        if all(lst.get(i, {}).get("processing_status") in ("Succeeded", "Failed") for i in ids):
            return lst
        await asyncio.sleep(1)
    return lst


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())
    make_po(tmp / "po_ok.docx")
    make_po(tmp / "po_dup_number.docx", total="999999.00", lines=(("Laptop", "99", "1.00"),))
    make_po(tmp / "po_nonumber.docx", number=None)
    make_po(tmp / "po_unknown.docx", number="PO-2000", buyer="Unknown Widgets Ltd", gstin="07AAACU9999A1Z1")
    make_po(tmp / "po_9999.docx", number="PO-9999", lines=(("Monitor", "5", "12000.00"),), total="60000.00")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=900) as c:
        testsupport.install(c)
        tok = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (
            cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", gstin="33AABCZ1234F1Z5", deducts_tds=True),
            cust(customer_name="Meridian Tech Solutions Pvt Ltd", email="ap@meridian.com", gstin="29AABCM5678K1Z3", deducts_tds=False),
        ):
            assert (await c.post("/customers", headers=H, json=body)).status_code == 201
        async with SessionLocal() as db:
            cid = {r[0]: r[1] for r in (await db.execute(text("select customer_name, id from customers"))).all()}
        ZEN, MER = cid["Zenith Softlabs Pvt Ltd"], cid["Meridian Tech Solutions Pvt Ltd"]

        inv = {}
        inv["A"] = await seed_invoice(ZEN, "INV-A", "PO-1001", [("Laptop", "4"), ("Monitor", "2")])
        inv["B"] = await seed_invoice(ZEN, "INV-B", "PO-1001", [("laptop", "4")])
        inv["C"] = await seed_invoice(ZEN, "INV-C", "PO-1001", [("Laptop", "4")])
        inv["D"] = await seed_invoice(ZEN, "INV-D", "PO-1001", [("Keyboard", "1")])
        inv["E"] = await seed_invoice(ZEN, "INV-E", "PO-9999", [("Monitor", "1")])
        inv["F"] = await seed_invoice(MER, "INV-F", "PO-1001", [("Laptop", "1")])
        inv["G"] = await seed_invoice(ZEN, "INV-G", "PO-1001", [])  # no lines at all

        ids = {}
        async def up(name):
            r = await c.post("/documents/upload", headers=H, files={"file": (name, (tmp / name).read_bytes())})
            assert r.status_code == 202, (name, r.text)
            ids[name] = r.json()["source_document_id"]
            return await wait_done(c, H, [ids[name]])

        lst = await up("po_ok.docx")
        u = lst[ids["po_ok.docx"]]
        check("po_ok -> po_created", u["disposition"] == "po_created" and u["validation_outcome"] == "PASS" and u["purchase_order_id"], str(u["disposition"]))
        det = (await c.get(f"/documents/uploads/{ids['po_ok.docx']}", headers=H)).json()
        linked = {x["invoice_number"]: x for x in det["linked_invoices"]}
        check("retro-link reported for A,B,C,D,G only", set(linked) == {"INV-A", "INV-B", "INV-C", "INV-D", "INV-G"}, str(sorted(linked)))
        check("A and B pass; C, D, G fail", [linked[k]["passed"] for k in ("INV-A", "INV-B", "INV-C", "INV-D", "INV-G")] == [True, True, False, False, False])
        check("C reason names the quantities", "over the ordered quantity" in " ".join(linked["INV-C"]["reasons"]), str(linked["INV-C"]["reasons"]))
        check("D reason: item not on the PO", "not on the purchase order" in " ".join(linked["INV-D"]["reasons"]))
        check("G reason: no line items", "no line items" in " ".join(linked["INV-G"]["reasons"]))
        check("steps: parse, extract, validate, customer_match, route", [x["step_name"] for x in det["steps"]] == ["parse", "classify", "extract", "validate", "customer_match", "route"], str([x["step_name"] for x in det["steps"]]))

        async with SessionLocal() as db:
            q = lambda sql, **p: db.execute(text(sql), p)
            ledger = {r[0]: r[1] for r in (await q("select l.normalized_description, sum(f.quantity_invoiced) from po_fulfillments f join purchase_order_line_items l on l.id=f.po_line_item_id group by 1")).all()}
            check("ledger: laptop 8 (A+B), monitor 2 (A); nothing for C, D, G", ledger == {"laptop": Decimal("8.0000"), "monitor": Decimal("2.0000")}, str(ledger))
            nrows = (await q("select count(*) from po_fulfillments f join invoices i on i.id=f.invoice_id where i.invoice_number in ('INV-C','INV-D','INV-G')")).scalar()
            check("no ledger rows for failing invoices", nrows == 0)
            holds = {r[0]: r[1] for r in (await q("select invoice_number, po_hold from invoices")).all()}
            check("po_hold true exactly for C, D, G", {k for k, v in holds.items() if v} == {"INV-C", "INV-D", "INV-G"}, str(holds))
            pend = (await q("select i.invoice_number, p.status::text, p.reason::text, p.proposed_snapshot->'lines'->0->>'remaining_after' from pending_invoice_changes p join invoices i on i.id=p.invoice_id where p.reason='po_mismatch' order by 1")).all()
            check("3 po_mismatch Pending rows with the comparison stored", [(r[0], r[1]) for r in pend] == [("INV-C", "Pending"), ("INV-D", "Pending"), ("INV-G", "Pending")], str(pend))
            dirs = {r[0]: r[1] for r in (await q("select i.invoice_number, m.match_direction from po_invoice_matches m join invoices i on i.id=m.invoice_id")).all()}
            check("5 links, all invoice_first; E and F unlinked", dirs == {k: "invoice_first" for k in ("INV-A", "INV-B", "INV-C", "INV-D", "INV-G")}, str(dirs))
            nl = (await q("select count(*) from purchase_order_line_items where normalized_description in ('laptop','monitor')")).scalar()
            check("PO lines stored with normalized description", nl == 2)

        lst = await up("po_dup_number.docx")
        u = lst[ids["po_dup_number.docx"]]
        check("same (customer, PO number) again -> review, no second PO", u["disposition"] == "review" and "already exists" in " ".join((await c.get(f"/documents/uploads/{ids['po_dup_number.docx']}", headers=H)).json()["disposition_reasons"]), str(u["disposition"]))

        lst = await up("po_nonumber.docx")
        u = lst[ids["po_nonumber.docx"]]
        check("no PO number (certain) -> rejected", u["disposition"] == "rejected" and u["validation_outcome"] == "FAIL", str(u["disposition"]))

        lst = await up("po_unknown.docx")
        u = lst[ids["po_unknown.docx"]]
        check("unknown customer -> review", u["disposition"] == "review" and "Unknown customer" in " ".join((await c.get(f"/documents/uploads/{ids['po_unknown.docx']}", headers=H)).json()["disposition_reasons"]))

        lst = await up("po_9999.docx")
        u = lst[ids["po_9999.docx"]]
        d9 = (await c.get(f"/documents/uploads/{ids['po_9999.docx']}", headers=H)).json()
        check("PO-9999 created and retro-links INV-E, which passes", u["disposition"] == "po_created" and [(x["invoice_number"], x["passed"]) for x in d9["linked_invoices"]] == [("INV-E", True)], str(d9["linked_invoices"]))

        # reviews: PO reviews must not leak into the invoice review endpoints
        inv_reviews = (await c.get("/invoices/reviews", headers=H)).json()
        po_review_ids = [lst2["review_id"] for lst2 in (await c.get("/documents/uploads", headers=H)).json() if lst2["review_id"]]
        check("2 PO reviews exist (duplicate number, unknown customer)", len(po_review_ids) == 2, str(len(po_review_ids)))
        check("invoice review list excludes PO reviews", all(r["review_id"] not in po_review_ids for r in inv_reviews))
        r = await c.get(f"/invoices/reviews/{po_review_ids[0]}", headers=H)
        check("PO review id on the invoice review endpoint -> 404", r.status_code == 404)

        async with SessionLocal() as db:
            q = lambda sql, **p: db.execute(text(sql), p)
            kept = (await q("select count(*) from source_documents where doc_kind='po' and file_path is null")).scalar()
            check("no PO file was deleted (even the wrong-button and rejected ones)", kept == 0, str(kept))
            check("only 2 purchase orders exist (PO-1001, PO-9999)", (await q("select count(*) from purchase_orders")).scalar() == 2)
            check("INV-F (other customer) untouched: unlinked, no hold", (await q("select count(*) from po_invoice_matches m join invoices i on i.id=m.invoice_id where i.invoice_number='INV-F'")).scalar() == 0)
            jobs = (await q("select state::text, count(*) from jobs group by 1")).all()
            check("all 5 jobs Succeeded", jobs == [("Succeeded", 5)], str(jobs))
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
