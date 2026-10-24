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
assert "ar_e2e" in s.database_url, "refusing to run against a non-test database"
D = Path(sys.argv[1]) / "e2e_docs"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:240])


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=900
    ) as c:
        testsupport.install(c)
        tok = (
            await c.post(
                "/auth/login",
                json={"email": s.seed_admin_email, "password": s.seed_admin_password},
            )
        ).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        for body in (
            cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", gstin="33AABCZ1234F1Z5", deducts_tds=True),
            cust(customer_name="Meridian Tech Solutions Pvt Ltd", email="ap@meridian.com", gstin="29AABCM5678K1Z3", deducts_tds=False),
        ):
            assert (await c.post("/customers", headers=H, json=body)).status_code == 201
        ids = {}
        for f in sorted(x for x in D.glob("*.docx") if not x.name.startswith("c11")):  # c11 belongs to the review test
            r = await c.post("/documents/upload", headers=H, files={"file": (f.name, f.read_bytes())})
            assert r.status_code == 202, (f.name, r.text)
            ids[f.name] = r.json()["source_document_id"]
        lst = {u["file_name"]: u for u in (await c.get("/documents/uploads", headers=H)).json()}
        po_lst = {u["file_name"]: u for u in (await c.get("/documents/uploads", headers=H)).json()}
        lst.update(po_lst)  # a document the classifier took for a purchase order is listed there
        det = lambda n: c.get(f"/documents/uploads/{ids[n]}", headers=H)
        print("RESULT   " + " | ".join(f"{n[:3]}={lst[n]['disposition']}" for n in sorted(lst)))

        async with SessionLocal() as db:
            async def q(sql, **p):
                return await db.execute(text(sql), p)

            cid = {r[0]: str(r[1]) for r in (await q("select customer_name, id from customers")).all()}
            ZEN, MER = cid["Zenith Softlabs Pvt Ltd"], cid["Meridian Tech Solutions Pvt Ltd"]

            u = lst["c01_gstin_match.docx"]
            ex = (await det("c01_gstin_match.docx")).json()["extraction"]
            check("c01 extracted CLIENT gstin, not ours", ex["customer_gstin"] == "33AABCZ1234F1Z5", str(ex["customer_gstin"]))
            check("c01 -> invoice created for Zenith", u["disposition"] == "invoice_created" and u["customer_id"] == ZEN and u["invoice_id"])
            row = (await q(
                "select customer_id::text, status::text, type::text, invoice_currency, amount_inr, fx_rate_invoice, "
                "tds_amount, collectible_total, amount_paid, collectible_balance, version, is_disputed, "
                "source_document_id::text from invoices where invoice_number='GW-E2E-001'")).one()
            check(
                "c01 invoice row (c07 re-used its number, so it is now version 2): Open, TDS on the new amount, balance computed",
                row[0] == ZEN and row[1] == "Open" and row[2] == "INVOICE" and row[3] == "INR"
                and row[4] == Decimal("777.00") and row[5] == Decimal("1") and row[6] == Decimal("77.70")
                and row[7] == Decimal("699.30") and row[8] == 0 and row[9] == Decimal("699.30")
                and row[10] == 2 and row[11] is False and row[12] == ids["c07_duplicate_number.docx"],
                str(row),
            )
            check("c01 line items stored: one set per version", (await q("select count(*) from invoice_line_items li join invoices i on i.id=li.invoice_id where i.invoice_number='GW-E2E-001'")).scalar() == 2)
            check("c01 state_log: invoice -> Open", (await q("select count(*) from state_log where entity_type='invoice' and to_state='Open'")).scalar() >= 1)

            u = lst["c02_name_match.docx"]
            check("c02 -> matched by NAME to Meridian", u["disposition"] == "invoice_created" and u["customer_id"] == MER)
            row = (await q("select tds_amount, collectible_total from invoices where invoice_number='GW-E2E-002'")).one()
            check("c02 customer without TDS: collectible = full", tuple(row) == (Decimal("0.00"), Decimal("25000.00")), str(tuple(row)))
            ms = [x for x in (await det("c02_name_match.docx")).json()["steps"] if x["step_name"] == "customer_match"]
            check("c02 customer_match step records method=name", ms and ms[0]["output_summary"]["method"] == "name")

            row = (await q("select type::text, tds_amount, collectible_total from invoices where invoice_number='GW-E2E-003'")).one_or_none()
            check("c03 debit note: type DEBIT_NOTE, no TDS", row is not None and tuple(row) == ("DEBIT_NOTE", Decimal("0.00"), Decimal("5000.00")), str(row))

            for n, num, needle in (
                ("c04_unknown_customer.docx", "GW-E2E-004", "Unknown customer"),
                ("c05_identity_conflict.docx", "GW-E2E-005", "Identity conflict"),
                ("c06_usd.docx", "GW-E2E-006", "QAR"),
                ("c10_name_ok_gstin_unknown.docx", "GW-E2E-010", "not in the customer master"),
            ):
                u = lst[n]
                d = (await det(n)).json()
                check(
                    f"{n[:3]} -> REVIEW, reason mentions '{needle}', no invoice created",
                    u["disposition"] == "review" and u["invoice_id"] is None
                    and any(needle in r for r in d["disposition_reasons"])
                    and (await q("select count(*) from invoices where invoice_number=:n", n=num)).scalar() == 0,
                    str(d["disposition_reasons"])[:120],
                )
                check(
                    f"{n[:3]} pending_invoice_changes row (extraction_review, Pending)",
                    (await q("select count(*) from pending_invoice_changes where source_document_id=:i and reason='extraction_review' and status='Pending' and invoice_id is null", i=ids[n])).scalar() == 1,
                    str((await q("select reason::text, status::text, invoice_id is null from pending_invoice_changes where source_document_id=:i", i=ids[n])).all()),
                )

            u = lst["c07_duplicate_number.docx"]
            check("c07 re-uses c01's number with changed values -> applied as a NEW VERSION of the Open invoice", u["disposition"] == "version_applied" and u["invoice_version"] == 2, str(u["disposition"]))
            v1 = (await q("select version_no, (snapshot->>'amount_inr')::numeric from invoice_versions v join invoices i on i.id=v.invoice_id where i.invoice_number='GW-E2E-001'")).all()
            check("c07 the old state (100000) is saved as version 1", [tuple(x) for x in v1] == [(1, Decimal("100000.00"))], str(v1))

            u = lst["c08_purchase_order.docx"]
            d8 = (await c.get(f"/documents/uploads/{ids['c08_purchase_order.docx']}", headers=H)).json()
            cl = [x for x in d8["steps"] if x["step_name"] == "classify"]
            check("c08 purchase order -> classified PO (not rejected as 'not an invoice'), goes to the PO pipeline", cl and cl[0]["output_summary"]["doc_class"] == "po" and u["status"] in ("Purchase order created", "Needs review", "Rejected"), f"{u['status']} {d8['disposition_reasons']}"[:150])
            check("c08 file kept, no invoice created from it", (await q("select file_path is not null from source_documents where id=:i", i=ids["c08_purchase_order.docx"])).scalar() and (await q("select count(*) from invoices where source_document_id=:i", i=ids["c08_purchase_order.docx"])).scalar() == 0)

            u = lst["c09_no_due_date.docx"]
            d = (await det("c09_no_due_date.docx")).json()
            check("c09 missing due date -> REJECTED", u["disposition"] == "rejected" and any("Due date is missing" in r for r in d["disposition_reasons"]), str(d["disposition_reasons"])[:100])
            check("c09 no invoice created for rejected docs", (await q("select count(*) from invoices where invoice_number='GW-E2E-009'")).scalar() == 0)

            check("exactly 3 invoices created (c01,c02,c03)", (await q("select count(*) from invoices")).scalar() == 3)
            check("every document ended in exactly one place", len(lst) == 10 and all(lst[n]["disposition"] in ("invoice_created", "version_applied", "po_created", "review", "rejected") for n in lst))
            check("all 10 jobs Succeeded (verdicts are business outcomes, not job failures)", (await q("select count(*) from jobs where state='Succeeded'")).scalar() == 10)
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
