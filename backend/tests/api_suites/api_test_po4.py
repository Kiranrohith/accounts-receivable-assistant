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
assert "ar_po4" in s.database_url, "refusing to run against a non-test database"
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


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    tmp = Path(tempfile.mkdtemp())
    make_po(tmp / "po_ok.docx")
    make_po(tmp / "po_dup.docx", total="999.00", lines=(("Laptop", "99", "1.00"),))
    make_po(tmp / "po_unknown.docx", number="PO-2000", buyer="Unknown Widgets Ltd", gstin="07AAACU9999A1Z1")
    make_po(tmp / "po_noqty.docx", number="PO-3000", lines=(("Desk", "", "100.00"),), total="200.00")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=900) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa Two", "fa.two@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        assert (await c.post("/customers", headers=ADM, json=cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", gstin="33AABCZ1234F1Z5", deducts_tds=True))).status_code == 201
        async with SessionLocal() as db:
            ZEN = (await db.execute(text("select id from customers"))).scalar()

        async def wait(H, ids):
            for _ in range(300):
                lst = {u["source_document_id"]: u for u in (await c.get("/documents/uploads", headers=H)).json()}
                if all(lst.get(i, {}).get("processing_status") in ("Succeeded", "Failed") for i in ids):
                    return lst
                await asyncio.sleep(1)

        async def up(H, name):
            r = await c.post("/documents/upload", headers=H, files={"file": (name, (tmp / name).read_bytes())})
            assert r.status_code == 202, r.text
            sid = r.json()["source_document_id"]
            return (await wait(H, [sid]))[sid]

        # ------------------------------------------------ mismatch review
        await seed_invoice(ZEN, "INV-A", "PO-1001", [("Laptop", "4"), ("Monitor", "2")])
        await seed_invoice(ZEN, "INV-B", "PO-1001", [("Laptop", "4")])
        await seed_invoice(ZEN, "INV-C", "PO-1001", [("Laptop", "4")])
        await seed_invoice(ZEN, "INV-D", "PO-1001", [("Keyboard", "1")])
        await seed_invoice(ZEN, "INV-H", "PO-1001", [("Monitor", "5")])
        await seed_invoice(ZEN, "INV-J", "PO-2000", [("Laptop", "1")])
        row = await up(ADM, "po_ok.docx")
        check("PO-1001 created; retro-link run", row["disposition"] == "po_created", str(row["disposition"]))

        lst = (await c.get("/purchase-orders/mismatch-reviews", headers=FA)).json()
        by = {m["invoice_number"]: m for m in lst}
        check("FA sees the 3 held invoices C, D, H (not only the uploader)", set(by) == {"INV-C", "INV-D", "INV-H"}, str(sorted(by)))
        dC = (await c.get(f"/purchase-orders/mismatch-reviews/{by['INV-C']['review_id']}", headers=FA)).json()
        check("detail: per-line comparison with ordered/already/this/remaining", dC["po_hold"] is True and dC["lines"][0]["ordered"].startswith("10") and dC["lines"][0]["already_invoiced"].startswith("8") and dC["lines"][0]["remaining_after"].startswith("-2"), str(dC["lines"][0]))
        C, D, H = by["INV-C"]["review_id"], by["INV-D"]["review_id"], by["INV-H"]["review_id"]

        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/override", headers=FA, json={"reason": "ok"})
        check("override as FA -> 403 (no payments.po_override)", r.status_code == 403, r.text[:80])
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/override", headers=FTM, json={"reason": "   "})
        check("override with blank reason -> 422", r.status_code == 422)
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/reject", headers=FTM, json={"reason": "no"})
        check("there is no reject endpoint (404/405)", r.status_code in (404, 405), str(r.status_code))

        # re-check that still fails
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/recheck", headers=FA, json={"lines": [{"description": "Laptop", "quantity": "3"}]})
        j = r.json()
        check("recheck with Laptop 3 (still 1 over) -> stays Pending, hold kept", r.status_code == 200 and j["status"] == "Pending" and j["po_hold"] is True and j["lines"][0]["remaining_after"].startswith("-1"), r.text[:200])
        # re-check that passes
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/recheck", headers=FA, json={"lines": [{"description": "laptop", "quantity": "2"}], "note": "Quantity was misread"})
        j = r.json()
        check("recheck with Laptop 2 -> Approved, hold released", r.status_code == 200 and j["status"] == "Approved" and j["po_hold"] is False, r.text[:200])
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/recheck", headers=FA, json={"lines": [{"description": "Laptop", "quantity": "1"}]})
        check("decided review can't be decided again -> 409", r.status_code == 409)
        r = await c.post(f"/purchase-orders/mismatch-reviews/{C}/override", headers=FTM, json={"reason": "again"})
        check("... nor overridden -> 409", r.status_code == 409)

        # override into negative
        r = await c.post(f"/purchase-orders/mismatch-reviews/{H}/override", headers=FTM, json={"reason": "Customer accepted the extra monitors"})
        j = r.json()
        check("override of H (5 monitors, 3 left) -> Approved, remaining shown negative", r.status_code == 200 and j["status"] == "Approved" and j["lines"][0]["remaining_after"].startswith("-2"), r.text[:220])

        # simultaneous overrides on D: one wins
        a, b = await asyncio.gather(
            c.post(f"/purchase-orders/mismatch-reviews/{D}/override", headers=FTM, json={"reason": "one"}),
            c.post(f"/purchase-orders/mismatch-reviews/{D}/override", headers=ADM, json={"reason": "two"}),
        )
        check("two simultaneous overrides -> exactly one 200 and one 409", sorted([a.status_code, b.status_code]) == [200, 409], f"{a.status_code},{b.status_code}")

        async with SessionLocal() as db:
            q = lambda sql, **p: db.execute(text(sql), p)
            ledger = {r[0]: r[1] for r in (await q("select l.normalized_description, sum(f.quantity_invoiced) from po_fulfillments f join purchase_order_line_items l on l.id=f.po_line_item_id group by 1")).all()}
            check("ledger: laptop 10 (A4+B4+C2), monitor 7 (A2+H5: over the order, recorded)", ledger == {"laptop": Decimal("10.0000"), "monitor": Decimal("7.0000")}, str(ledger))
            check("no po_hold left on any invoice", (await q("select count(*) from invoices where po_hold")).scalar() == 0)
            vers = [(r[0], r[1]) for r in (await q("select version_no, quantity from invoice_line_items li join invoices i on i.id=li.invoice_id where i.invoice_number='INV-C' order by 1")).all()]
            check("INV-C keeps all 3 line-item versions; pointer on 3", vers == [(1, Decimal("4.0000")), (2, Decimal("3.0000")), (3, Decimal("2.0000"))] and (await q("select line_items_version from invoices where invoice_number='INV-C'")).scalar() == 3, str(vers))
            dec = {r[0]: (r[1], r[2], r[3]) for r in (await q("select i.invoice_number, p.status::text, p.decision_reason, p.diff->>'decision' from pending_invoice_changes p join invoices i on i.id=p.invoice_id where p.reason='po_mismatch'")).all()}
            check("decisions recorded: C recheck (with note), H and D override", dec["INV-C"] == ("Approved", "Quantity was misread", "recheck") and dec["INV-H"][2] == "override" and dec["INV-D"][2] == "override", str(dec))
            check("release is on the invoice timeline", (await q("select count(*) from state_log where entity_type='invoice' and reason like 'PO hold released%'")).scalar() == 3)
        done = (await c.get("/purchase-orders/mismatch-reviews?status=Approved", headers=FA)).json()
        check("Approved list has the 3", len(done) == 3)
        check("nothing left Pending", (await c.get("/purchase-orders/mismatch-reviews", headers=FA)).json() == [])

        # ------------------------------------------------ PO extraction review (uploader only)
        row = await up(FA, "po_unknown.docx")
        rid = row["review_id"]
        check("unknown-customer PO -> review (uploaded by FA)", row["disposition"] == "review" and rid)
        check("admin (not the uploader) cannot see it: 403", (await c.get(f"/purchase-orders/reviews/{rid}", headers=ADM)).status_code == 403)
        check("admin's list does not include it", all(x["review_id"] != rid for x in (await c.get("/purchase-orders/reviews", headers=ADM)).json()))
        mine = (await c.get("/purchase-orders/reviews", headers=FA)).json()
        check("FA's list has it", [x["review_id"] for x in mine] == [rid])
        r = await c.post(f"/invoices/reviews/{rid}/approve", headers=FA, json={"reason": "x"})
        check("invoice review endpoint can't touch a PO review: 404", r.status_code == 404)
        r = await c.post(f"/purchase-orders/reviews/{rid}/approve", headers=FA, json={"reason": "Confirmed with the customer"})
        check("approve without choosing a customer -> 422", r.status_code == 422 and "customer" in r.text.lower(), r.text[:120])
        r = await c.post(f"/purchase-orders/reviews/{rid}/approve", headers=FA, json={"reason": "Confirmed with the customer", "customer_id": str(ZEN)})
        j = r.json()
        check("approve with customer_id -> PO created, retro-links INV-J which passes", r.status_code == 200 and j["purchase_order_id"] and [(x["invoice_number"], x["passed"]) for x in j["linked_invoices"]] == [("INV-J", True)], r.text[:200])
        r = await c.post(f"/purchase-orders/reviews/{rid}/approve", headers=FA, json={"reason": "again", "customer_id": str(ZEN)})
        check("approve twice -> 409", r.status_code == 409)

        row = await up(FA, "po_noqty.docx")
        rid2 = row["review_id"]
        check("PO with a line missing its quantity -> review", row["disposition"] == "review" and rid2, str(row["disposition"]))
        r = await c.post(f"/purchase-orders/reviews/{rid2}/approve", headers=FA, json={
            "reason": "It really is a PO, fields typed in from the paper copy",
            "edits": {"po_number": "PO-3000", "customer_name": "Zenith Softlabs Pvt Ltd", "customer_gstin": "33AABCZ1234F1Z5",
                      "currency": "inr", "line_items": [{"description": "Desk", "quantity": "2"}]}})
        check("approve with edits -> PO-3000 created with the typed line", r.status_code == 200 and r.json()["purchase_order_id"], r.text[:160])

        row = await up(FA, "po_dup.docx")
        rid3 = row["review_id"]
        check("duplicate PO number -> review", row["disposition"] == "review" and rid3)
        r = await c.post(f"/purchase-orders/reviews/{rid3}/approve", headers=FA, json={"reason": "try", "customer_id": str(ZEN)})
        check("approving a duplicate number -> 409, still pending", r.status_code == 409 and (await c.get(f"/purchase-orders/reviews/{rid3}", headers=FA)).json()["status"] == "Pending", r.text[:120])
        r = await c.post(f"/purchase-orders/reviews/{rid3}/reject", headers=FA, json={"reason": "Same PO sent twice"})
        check("reject -> Rejected", r.status_code == 200 and r.json()["status"] == "Rejected")

        async with SessionLocal() as db:
            q = lambda sql, **p: db.execute(text(sql), p)
            pos = sorted(r[0] for r in (await q("select po_number from purchase_orders")).all())
            check("purchase orders: PO-1001, PO-2000 (Zenith, by choice), PO-3000", pos == ["PO-1001", "PO-2000", "PO-3000"], str(pos))
            rj = (await q("select rejection_reasons->0->>'message', extracted_snapshot->>'kind' from rejected_invoices")).all()
            check("rejected PO recorded with the reviewer's reason first, tagged purchase_order", rj == [("Same PO sent twice", "purchase_order")], str(rj))
            check("no file was deleted", (await q("select count(*) from source_documents where file_path is null")).scalar() == 0)
            led = (await q("select sum(quantity_invoiced) from po_fulfillments f join invoices i on i.id=f.invoice_id where i.invoice_number='INV-J'")).scalar()
            check("INV-J's ledger row written on retro-link", led == Decimal("1.0000"), str(led))
        row = next(u for u in (await c.get("/documents/uploads", headers=FA)).json() if u["review_id"] is None and u["file_name"] == "po_unknown.docx")
        check("upload status turns po_created after the approval", row["disposition"] == "po_created", str(row["disposition"]))
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
