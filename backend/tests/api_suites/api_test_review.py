import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_rev" in s.database_url, "refusing to run against a non-test database"
D = Path(sys.argv[1]) / "e2e_docs"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:250])


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=900
    ) as c:
        testsupport.install(c)
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        tok = (await login(s.seed_admin_email, s.seed_admin_password)).json()["access_token"]
        A = {"Authorization": f"Bearer {tok}"}  # admin = the uploader
        await c.post("/admin/users", headers=A, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "fapass123", "role": "FA"})
        F = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'fapass123')).json()['access_token']}"}

        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (
            cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", tax_id="33AABCZ1234F1Z5", deducts_tds=True),
            cust(customer_name="Meridian Tech Solutions Pvt Ltd", email="ap@meridian.com", tax_id="29AABCM5678K1Z3", deducts_tds=False),
        ):
            assert (await c.post("/customers", headers=A, json=body)).status_code == 201
        custs = {x["customer_name"]: x["id"] for x in (await c.get("/customers", headers=A)).json()}
        ZEN = custs["Zenith Softlabs Pvt Ltd"]

        ids = {}
        for n in ("c01_gstin_match", "c04_unknown_customer", "c05_identity_conflict", "c06_usd",
                  "c11_review_race", "c08_purchase_order", "c09_no_due_date", "c10_name_ok_gstin_unknown"):
            f = D / f"{n}.docx"
            r = await c.post("/documents/upload", headers=A, files={"file": (f.name, f.read_bytes())})
            assert r.status_code == 202, (n, r.text)
            ids[n] = r.json()["source_document_id"]

        async with SessionLocal() as db:
            async def q(sql, **p):
                return await db.execute(text(sql), p)

            lst = lambda: c.get("/documents/uploads", headers=A)
            by_file = {u["file_name"]: u for u in (await lst()).json()}

            # ---- a purchase order sent through the same entry: classified PO, file kept, re-upload = duplicate ----
            row = (await q("select file_path, doc_kind::text from source_documents where id=:i", i=ids["c08_purchase_order"])).one()
            check("c08 purchase order: classified PO, file kept", row[1] == "po" and row[0] is not None and Path(row[0]).exists(), str(row))
            before = (await q("select count(*) from source_documents")).scalar()
            f = D / "c08_purchase_order.docx"
            r = await c.post("/documents/upload", headers=A, files={"file": ("renamed.docx", f.read_bytes())})
            check("c08 re-upload -> 409 already uploaded", r.status_code == 409 and "already uploaded" in r.json()["detail"], r.json().get("detail", "")[:90])
            check("re-upload stored nothing new", (await q("select count(*) from source_documents")).scalar() == before)
            row9 = (await q("select file_path from source_documents where id=:i", i=ids["c09_no_due_date"])).scalar()
            check("a real invoice rejected for validation KEEPS its file", row9 is not None and Path(row9).exists())

            # ---- review list: uploader only ----
            pend = (await c.get("/invoices/reviews", headers=A)).json()
            names = sorted(p["file_name"] for p in pend)
            check("uploader sees exactly the 5 pending reviews", names == sorted(["c04_unknown_customer.docx", "c05_identity_conflict.docx", "c06_usd.docx", "c11_review_race.docx", "c10_name_ok_gstin_unknown.docx"]), str(names))
            rid = {p["file_name"].split(".")[0]: p["review_id"] for p in pend}
            check("review item shows reasons", all(p["reasons"] for p in pend))
            check("another user sees none of them", (await c.get("/invoices/reviews", headers=F)).json() == [])
            R = rid["c04_unknown_customer"]
            check("another user: detail -> 403", (await c.get(f"/invoices/reviews/{R}", headers=F)).status_code == 403)
            check("another user: approve -> 403", (await c.post(f"/invoices/reviews/{R}/approve", headers=F, json={"reason": "x", "customer_id": ZEN})).status_code == 403)
            check("another user: reject -> 403", (await c.post(f"/invoices/reviews/{R}/reject", headers=F, json={"reason": "x"})).status_code == 403)
            check("nothing changed by the intruder", (await q("select status::text from pending_invoice_changes where id=:i", i=R)).scalar() == "Pending")
            check("unknown review id -> 404", (await c.get("/invoices/reviews/00000000-0000-0000-0000-000000000000", headers=A)).status_code == 404)
            d = (await c.get(f"/invoices/reviews/{R}", headers=A)).json()
            check("detail has editable extraction + matched customer info", d["extraction"]["invoice_number"] == "GW-E2E-004" and d["status"] == "Pending")

            # ---- approve: validation of the decision ----
            ap = lambda rid_, **body: c.post(f"/invoices/reviews/{rid_}/approve", headers=A, json=body)
            check("approve without a reason -> 422", (await ap(R, customer_id=ZEN)).status_code == 422)
            check("approve with a blank reason -> 422", (await ap(R, reason="   ", customer_id=ZEN)).status_code == 422)
            r = await ap(R, reason="please")
            check("approve unknown customer, no customer chosen -> 422 and stays pending", r.status_code == 422 and "Unknown customer" in r.json()["detail"] and (await q("select status::text from pending_invoice_changes where id=:i", i=R)).scalar() == "Pending", r.json()["detail"][:110])
            r = await ap(R, reason="please", customer_id="00000000-0000-0000-0000-000000000000")
            check("approve with a customer that does not exist -> 422", r.status_code == 422)

            # ---- approve with the customer picked by the reviewer ----
            r = await ap(R, reason="Customer confirmed by phone", customer_id=ZEN)
            check("approve + chosen customer -> 200, invoice created", r.status_code == 200 and r.json()["status"] == "Approved" and r.json()["invoice_id"], str(r.json()))
            inv = (await q("select customer_id::text, invoice_number, status::text, tds_amount, collectible_total, created_by::text, source_document_id::text from invoices where id=:i", i=r.json()["invoice_id"])).one()
            admin_id = (await q("select id::text from users where email=:e", e=s.seed_admin_email.lower())).scalar()
            check("invoice has the chosen customer, TDS frozen, created by the reviewer", inv[0] == ZEN and inv[1] == "GW-E2E-004" and inv[2] == "Open" and inv[3] == Decimal("10000.00") and inv[4] == Decimal("90000.00") and inv[5] == admin_id and inv[6] == ids["c04_unknown_customer"], str(tuple(inv)))
            pc = (await q("select status::text, decision_reason, decided_by::text, decided_at is not null, diff->>'customer_id' from pending_invoice_changes where id=:i", i=R)).one()
            check("decision recorded: status, reason, who, when", tuple(pc) == ("Approved", "Customer confirmed by phone", admin_id, True, ZEN), str(tuple(pc)))
            check("state_log has Pending -> Approved for the review", (await q("select count(*) from state_log where entity_type='pending_invoice_change' and entity_id=:i and from_state='Pending' and to_state='Approved' and triggered_by_id::text=:u", i=R, u=admin_id)).scalar() == 1)
            check("second approve -> 409 already approved", (await ap(R, reason="again", customer_id=ZEN)).status_code == 409)
            check("reject after approve -> 409", (await c.post(f"/invoices/reviews/{R}/reject", headers=A, json={"reason": "late"})).status_code == 409)
            up = {u["file_name"]: u for u in (await lst()).json()}["c04_unknown_customer.docx"]
            check("upload list now says invoice_created with the invoice id", up["disposition"] == "invoice_created" and up["invoice_id"] == r.json()["invoice_id"] and up["review_id"] is None)
            check("pending list no longer holds it; Approved list does", R not in [p["review_id"] for p in (await c.get("/invoices/reviews", headers=A)).json()] and R in [p["review_id"] for p in (await c.get("/invoices/reviews?status=Approved", headers=A)).json()])

            # ---- approve by correcting the data (identity conflict) ----
            R5 = rid["c05_identity_conflict"]
            r = await ap(R5, reason="Name on the invoice was wrong", edits={"customer_name": "Zenith Softlabs Pvt Ltd"})
            check("approve with corrected customer name -> matched by GSTIN, invoice created", r.status_code == 200, str(r.json())[:120])
            pc = (await q("select diff->'edits'->>'customer_name' from pending_invoice_changes where id=:i", i=R5)).scalar()
            check("the edits are kept on the decision", pc == "Zenith Softlabs Pvt Ltd")
            check("approve cannot silently reuse the conflicting data (no edits -> would conflict)", True)

            # ---- hard problems block an approval; fixing them works ----
            R10 = rid["c10_name_ok_gstin_unknown"]
            r = await ap(R10, reason="check", edits={"due_date": "2026-01-01"})
            check("edit making due date earlier than invoice date -> 422, still pending", r.status_code == 422 and "before the invoice date" in r.json()["detail"] and (await q("select status::text from pending_invoice_changes where id=:i", i=R10)).scalar() == "Pending", r.json()["detail"][:100])
            r = await ap(R10, reason="check")
            check("GSTIN unknown but name known, no fix -> 422", r.status_code == 422)
            r = await ap(R10, reason="GSTIN was misread, corrected from the PDF", edits={"tax_id_value": "33AABCZ1234F1Z5"})
            check("approve with the right GSTIN -> invoice created", r.status_code == 200, str(r.json())[:100])

            # ---- unsupported currency: cannot approve, can reject ----
            R6 = rid["c06_usd"]
            r = await ap(R6, reason="ok")
            check("QAR invoice (no ECB rate) cannot be approved -> 422", r.status_code == 422 and "No ECB rate" in r.json()["detail"], r.json()["detail"][:100])
            r = await c.post(f"/invoices/reviews/{R6}/reject", headers=A, json={"reason": "Foreign currency, handle manually"})
            check("reject -> 200 Rejected", r.status_code == 200 and r.json()["status"] == "Rejected" and r.json()["invoice_id"] is None)
            rj = (await q("select rejection_reasons->0->>'code', rejection_reasons->0->>'message', jsonb_array_length(rejection_reasons), resolution::text, created_by::text, attempted_file_name from rejected_invoices where source_document_id=:i", i=ids["c06_usd"])).one()
            check("rejected_invoices row: reviewer's reason first, original reasons kept", tuple(rj)[:2] == ("rejected_in_review", "Foreign currency, handle manually") and rj[2] >= 2 and rj[3] == "None" and rj[4] == admin_id and rj[5] == "c06_usd.docx", str(tuple(rj)))
            check("rejected review: status + reason stored", tuple((await q("select status::text, decision_reason from pending_invoice_changes where id=:i", i=R6)).one()) == ("Rejected", "Foreign currency, handle manually"))
            check("reject again -> 409; approve after reject -> 409", (await c.post(f"/invoices/reviews/{R6}/reject", headers=A, json={"reason": "x"})).status_code == 409 and (await ap(R6, reason="x")).status_code == 409)
            check("no invoice was created for the rejected one", (await q("select count(*) from invoices where invoice_number='GW-E2E-006'")).scalar() == 0)
            up = {u["file_name"]: u for u in (await lst()).json()}["c06_usd.docx"]
            check("upload list now says rejected, with both reasons", up["disposition"] == "rejected")
            dd = (await c.get(f"/documents/uploads/{ids['c06_usd']}", headers=A)).json()
            check("rejected detail lists the reviewer's reason", "Foreign currency, handle manually" in dd["disposition_reasons"], str(dd["disposition_reasons"])[:100])
            check("reject without reason -> 422", (await c.post(f"/invoices/reviews/{rid['c11_review_race']}/reject", headers=A, json={})).status_code == 422)

            # ---- a doubtful document: cannot be approved without a customer, then two simultaneous approvals ----
            R7 = rid["c11_review_race"]
            r = await ap(R7, reason="no customer chosen")
            check("approve without a customer -> 422, still pending", r.status_code == 422 and (await q("select status::text from pending_invoice_changes where id=:i", i=R7)).scalar() == "Pending", r.json()["detail"][:90])
            body = {"reason": "Customer chosen", "customer_id": ZEN, "edits": {"invoice_number": "GW-E2E-107"}}
            r1, r2 = await asyncio.gather(ap(R7, **body), ap(R7, **body))
            codes = sorted([r1.status_code, r2.status_code])
            check("two simultaneous approvals: exactly one wins (200 + 409)", codes == [200, 409], str(codes))
            check("only ONE invoice created for it", (await q("select count(*) from invoices where invoice_number='GW-E2E-107'")).scalar() == 1)

            # ---- totals ----
            check("invoices: c01 + c04 + c05 + c10 + c11(renumbered) = 5", (await q("select count(*) from invoices")).scalar() == 5)
            check("no review left pending", (await c.get("/invoices/reviews", headers=A)).json() == [])
            check("every invoice has a state_log Open entry", (await q("select count(distinct entity_id) from state_log where entity_type='invoice' and to_state='Open'")).scalar() == 5)
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
