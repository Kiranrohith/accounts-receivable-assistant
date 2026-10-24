"""Customer administration: the list figures, one customer's detail, editing, confirmed aliases, and the TDS recompute with
its decision. Real database, no model, no mail. Proves: the figures (open, overdue, outstanding, unapplied), who may do what,
uniqueness on edit, alias rules, and that a TDS recompute updates an Open invoice as a new version, hands a part-paid or
disputed one to its asker, leaves Paid and debit notes alone, skips what it must, is safe to repeat, and re-checks on approval."""

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, InvoiceLineItem, Payment
from data.models.postgres.enums import InvoiceStatus, InvoiceType, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_custadmin" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
TODAY = date.today()
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def customer(label, *, tds=False):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="Anita", email=f"ap@{label.lower()}-{RUN}.example.com",
                     tax_id_type="GSTIN", tax_id=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=tds, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


async def invoice(cid, number, total, *, due, status=InvoiceStatus.OPEN, disputed=False, kind=InvoiceType.INVOICE, paid="0", tds="0"):
    tds_amount = D(tds)
    async with SessionLocal() as db:
        inv = Invoice(customer_id=cid, invoice_number=number, type=kind, invoice_date=due - timedelta(days=30), due_date=due,
                      invoice_currency="INR", amount_inr=D(total), tds_amount=tds_amount, collectible_total=D(total) - tds_amount,
                      amount_paid=D(paid), status=status, is_disputed=disputed)
        db.add(inv)
        await db.flush()
        db.add(InvoiceLineItem(invoice_id=inv.id, version_no=1, description="Service", quantity=D(1), unit_price=D(total), line_total=D(total)))
        await db.commit()
        return inv.id


async def unapplied_payment(cid, amount):
    async with SessionLocal() as db:
        db.add(Payment(customer_id=cid, reference_number=f"UTR-{RUN}-{amount}", amount_received_inr=D(amount), unapplied_amount=D(amount),
                       state=PaymentState.UNAPPLIED, matched_layer=MatchedLayer.L4, payment_currency="INR", payment_date=TODAY))
        await db.commit()


async def inv_row(number):
    r = await q("select tds_amount, collectible_total, version, status::text from invoices where invoice_number=:n", n=number)
    return r[0]


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa One", "fa.ca@example.com", "FA"), ("Ftm One", "ftm1.ca@example.com", "FTM"), ("Ftm Two", "ftm2.ca@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        tok = lambda e: {"Authorization": f"Bearer {e}"}
        FA = tok((await login("fa.ca@example.com", "pass12345")).json()["access_token"])
        FTM1 = tok((await login("ftm1.ca@example.com", "pass12345")).json()["access_token"])
        FTM2 = tok((await login("ftm2.ca@example.com", "pass12345")).json()["access_token"])

        # ======================================== A. the list figures
        acme = await customer("Acme")
        await invoice(acme, "AC-1", "1000", due=TODAY - timedelta(days=5))  # overdue
        await invoice(acme, "AC-2", "2000", due=TODAY + timedelta(days=5))  # not yet due
        await invoice(acme, "AC-3", "500", due=TODAY - timedelta(days=9), status=InvoiceStatus.PARTLY_PAID, paid="200")  # owes 300, overdue
        await invoice(acme, "AC-P", "700", due=TODAY - timedelta(days=40), status=InvoiceStatus.PAID, paid="700")  # paid: not counted
        await unapplied_payment(acme, "150")
        quiet = await customer("Quiet")

        check("no token -> 401", (await c.get("/customers")).status_code == 401)
        r = await c.get("/customers", headers=FA)
        by = {x["customer_name"]: x for x in r.json()}
        a = by["Acme Ltd"]
        check("the list carries the figures: 3 open (Open + PartlyPaid), 2 overdue, 3,300 outstanding, 150 unapplied",
              r.status_code == 200 and (a["open_invoices"], a["overdue_invoices"], D(a["outstanding"]), D(a["unapplied_cash"])) == (3, 2, D("3300"), D("150")), str(a))
        z = by["Quiet Ltd"]
        check("a customer with nothing owed shows zeros, not nothing", (z["open_invoices"], z["overdue_invoices"], D(z["outstanding"]), D(z["unapplied_cash"])) == (0, 0, D("0"), D("0")), str(z))
        check("the old fields are still there for the pickers", {"id", "customer_name", "tax_id", "deducts_tds", "is_active"} <= set(a))

        # ======================================== B. one customer
        r = await c.get(f"/customers/{acme}", headers=FA)
        d = r.json()
        check("detail has the same figures, no aliases yet, and nothing out of date", r.status_code == 200 and d["open_invoices"] == 3 and d["aliases"] == [] and d["tds_out_of_date"] == 0, str(d))
        check("unknown customer -> 404", (await c.get(f"/customers/{uuid.uuid4()}", headers=FA)).status_code == 404)
        check("a malformed id -> 422", (await c.get("/customers/not-an-id", headers=FA)).status_code == 422)

        # ======================================== C. editing
        body = {"contact": "Ravi"}
        check("FA cannot edit (no customers.manage) -> 403", (await c.patch(f"/customers/{quiet}", headers=FA, json=body)).status_code == 403)
        r = await c.patch(f"/customers/{quiet}", headers=FTM1, json={"contact": "Ravi", "country": "Nepal", "billing_currency": "npr"})
        check("FTM edits some fields; the rest stay; the currency is upper-cased", r.status_code == 200 and r.json()["contact"] == "Ravi" and r.json()["country"] == "Nepal"
              and r.json()["billing_currency"] == "NPR" and r.json()["customer_name"] == "Quiet Ltd", r.text[:200])
        check("an empty body -> 422", (await c.patch(f"/customers/{quiet}", headers=FTM1, json={})).status_code == 422)
        check("a bad tax ID -> 422", (await c.patch(f"/customers/{quiet}", headers=FTM1, json={"tax_id": "123"})).status_code == 422)
        check("a bad currency -> 422", (await c.patch(f"/customers/{quiet}", headers=FTM1, json={"billing_currency": "RUPEE"})).status_code == 422)
        acme_gstin = (await c.get(f"/customers/{acme}", headers=FA)).json()["tax_id"]
        r = await c.patch(f"/customers/{quiet}", headers=FTM1, json={"tax_id": acme_gstin})
        check("another customer's tax ID -> 409 naming what clashes", r.status_code == 409 and "tax ID" in r.json()["detail"], r.text[:200])
        r = await c.patch(f"/customers/{quiet}", headers=FTM1, json={"customer_name": "  acme   LTD "})
        check("another customer's name (any case, any spacing) -> 409", r.status_code == 409 and "name" in r.json()["detail"], r.text[:200])
        r = await c.patch(f"/customers/{quiet}", headers=FTM1, json={"email": f"AP@ACME-{RUN}.example.com"})
        check("another customer's e-mail (any case) -> 409", r.status_code == 409 and "email" in r.json()["detail"], r.text[:200])
        own = (await c.get(f"/customers/{quiet}", headers=FA)).json()
        r = await c.patch(f"/customers/{quiet}", headers=FTM1, json={"tax_id": own["tax_id"], "customer_name": own["customer_name"], "email": own["email"]})
        check("sending its OWN tax ID, name and e-mail back is not a clash", r.status_code == 200, r.text[:200])
        check("404 for an unknown customer on edit", (await c.patch(f"/customers/{uuid.uuid4()}", headers=FTM1, json=body)).status_code == 404)
        audit = await q("select updated_by from customers where id=:i", i=quiet)
        check("the editor is recorded", audit[0][0] is not None)

        # ======================================== D. aliases
        r = await c.post(f"/customers/{acme}/aliases", headers=FA, json={"alias_type": "name", "alias_value": "ACME Corporation"})
        check("FA cannot confirm aliases -> 403", r.status_code == 403)
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "name", "alias_value": "  ACME   Corporation "})
        al = r.json().get("aliases", [])
        check("FTM adds a name alias (spaces tidied); it shows who confirmed it and where it came from",
              r.status_code == 201 and len(al) == 1 and al[0]["alias_value"] == "ACME Corporation" and al[0]["confirmed_by"] == "Ftm One" and al[0]["source"] == "human_review", r.text[:300])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "email", "alias_value": "Accounts@Acme-Corp.test"})
        check("an e-mail alias is stored in lower case", r.status_code == 201 and "accounts@acme-corp.test" in [x["alias_value"] for x in r.json()["aliases"]], r.text[:300])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "name", "alias_value": "acme corporation"})
        check("the same alias again (any case) -> 409", r.status_code == 409, r.text[:200])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "name", "alias_value": "Acme Ltd"})
        check("the customer's own name is not an alias -> 409", r.status_code == 409 and "own name" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "email", "alias_value": f"ap@acme-{RUN}.example.com"})
        check("the customer's own address is not an alias -> 409", r.status_code == 409 and "own e-mail" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "email", "alias_value": own["email"]})
        check("another customer's own address cannot be taken as an alias -> 409", r.status_code == 409 and "Quiet Ltd" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/customers/{acme}/aliases", headers=FTM1, json={"alias_type": "email", "alias_value": "not an address"})
        check("not an address -> 409 with a plain message", r.status_code == 409, r.text[:200])
        check("alias on an unknown customer -> 404", (await c.post(f"/customers/{uuid.uuid4()}/aliases", headers=FTM1, json={"alias_type": "name", "alias_value": "X"})).status_code == 404)
        check("the detail lists both aliases", len((await c.get(f"/customers/{acme}", headers=FA)).json()["aliases"]) == 2)
        resolved = await q("select count(*) from customer_aliases where customer_id=:i", i=acme)
        check("both are stored", resolved[0][0] == 2)

        # ======================================== E. TDS recompute
        tds = await customer("Tdsco", tds=False)
        await invoice(tds, "T-OPEN", "10000", due=TODAY + timedelta(days=10))  # Open: applied at once
        await invoice(tds, "T-PART", "20000", due=TODAY - timedelta(days=3), status=InvoiceStatus.PARTLY_PAID, paid="5000")  # review
        await invoice(tds, "T-DISP", "30000", due=TODAY + timedelta(days=3), disputed=True)  # review
        await invoice(tds, "T-PAID", "4000", due=TODAY - timedelta(days=50), status=InvoiceStatus.PAID, paid="4000")  # untouched
        await invoice(tds, "T-DN", "6000", due=TODAY + timedelta(days=3), kind=InvoiceType.DEBIT_NOTE)  # never carries TDS
        await invoice(tds, "T-OVER", "10000", due=TODAY + timedelta(days=3), status=InvoiceStatus.PARTLY_PAID, paid="9500")  # paid > new total: skipped
        pending_inv = await invoice(tds, "T-WAIT", "8000", due=TODAY + timedelta(days=3))  # already has a decision waiting
        async with SessionLocal() as db:
            await db.execute(text("insert into pending_invoice_changes (id, invoice_id, reason, proposed_snapshot, status, created_at) "
                                  "values (gen_random_uuid(), :i, 'version_upload', '{}'::jsonb, 'Pending', now())"), {"i": pending_inv})
            await db.commit()

        r = await c.post(f"/customers/{tds}/recompute-tds", headers=FTM1)
        check("with TDS still off nothing differs: nothing changes", r.status_code == 200 and r.json()["applied"] == 0 and r.json()["sent_to_review"] == 0, r.text[:300])
        check("FA cannot flip or recompute -> 403", (await c.patch(f"/customers/{tds}", headers=FA, json={"deducts_tds": True})).status_code == 403
              and (await c.post(f"/customers/{tds}/recompute-tds", headers=FA)).status_code == 403)

        r = await c.patch(f"/customers/{tds}", headers=FTM1, json={"deducts_tds": True})
        check("flipping TDS on is prospective: no invoice changed, and the detail counts the 5 that are out of date (not Paid, not the note)",
              r.status_code == 200 and r.json()["deducts_tds"] is True and r.json()["tds_out_of_date"] == 5 and (await inv_row("T-OPEN"))[0] == D("0"), r.text[:300])

        r = await c.post(f"/customers/{tds}/recompute-tds", headers=FTM1)
        out = r.json()
        check("recompute: 1 applied now, 2 sent to the asker, 2 left alone with reasons",
              r.status_code == 200 and out["applied"] == 1 and out["sent_to_review"] == 2 and len(out["skipped"]) == 2, r.text[:500])
        reasons = {x["invoice_number"]: x["reason"] for x in out["skipped"]}
        check("the skips say why (paid more than the new total; a decision already waiting)",
              set(reasons) == {"T-OVER", "T-WAIT"} and "credit note" in reasons["T-OVER"] and "already waiting" in reasons["T-WAIT"], str(reasons))
        tds_amount, collectible, version, status = await inv_row("T-OPEN")
        check("the Open invoice took 10% TDS as version 2", (tds_amount, collectible, version) == (D("1000.00"), D("9000.00"), 2), str((tds_amount, collectible, version)))
        old = await q("select snapshot->>'tds_amount', snapshot->>'collectible_total', change_summary->>'reason' from invoice_versions v join invoices i on i.id=v.invoice_id "
                      "where i.invoice_number='T-OPEN' and v.version_no=1")
        check("its old state is kept as superseded version 1", old and D(old[0][0]) == D("0") and D(old[0][1]) == D("10000") and old[0][2] == "tds_recompute", str(old))
        check("part-paid, disputed, paid, debit note and the skipped ones are untouched",
              (await inv_row("T-PART"))[0] == D("0") and (await inv_row("T-DISP"))[0] == D("0") and (await inv_row("T-PAID"))[0] == D("0")
              and (await inv_row("T-DN"))[0] == D("0") and (await inv_row("T-OVER"))[0] == D("0") and (await inv_row("T-WAIT"))[0] == D("0"))

        r = await c.post(f"/customers/{tds}/recompute-tds", headers=FTM1)
        check("repeating it changes nothing (the decisions already waiting are skipped, the applied one is correct)",
              r.json()["applied"] == 0 and r.json()["sent_to_review"] == 0 and r.json()["unchanged"] == 1, r.text[:400])

        # ---- the decisions
        queue = (await c.get("/queues/items", headers=FTM1, params={"queue": "tds_review"})).json()
        check("the asker sees the 2 decisions in the tds_review queue; the other FTM sees none",
              queue["total"] == 2 and (await c.get("/queues/items", headers=FTM2, params={"queue": "tds_review"})).json()["total"] == 0, str(queue)[:300])
        counts = {x["key"]: x["count"] for x in (await c.get("/queues", headers=FTM1)).json()}
        check("the catalog counts them for the asker only", counts.get("tds_review") == 2 and "tds_review" not in {x["key"] for x in (await c.get("/queues", headers=FA)).json()})
        items = {i["subject"]: i["item_id"] for i in queue["items"]}
        part_id, disp_id = items["T-PART"], items["T-DISP"]

        r = await c.get(f"/customers/tds-reviews/{part_id}", headers=FTM1)
        d = r.json()
        check("the decision shows old and new figures and why", r.status_code == 200 and (D(d["old_tds"]), D(d["new_tds"]), D(d["old_collectible"]), D(d["new_collectible"]), D(d["amount_paid"]))
              == (D("0"), D("2000.00"), D("20000"), D("18000.00"), D("5000")) and d["reasons"] and d["deducts_tds"] is True, r.text[:400])
        check("another person cannot see or decide it -> 403",
              (await c.get(f"/customers/tds-reviews/{part_id}", headers=FTM2)).status_code == 403
              and (await c.post(f"/customers/tds-reviews/{part_id}/approve", headers=FTM2, json={"reason": "x"})).status_code == 403)
        check("FA -> 403 (no customers.manage)", (await c.get(f"/customers/tds-reviews/{part_id}", headers=FA)).status_code == 403)
        check("an unknown decision -> 404", (await c.get(f"/customers/tds-reviews/{uuid.uuid4()}", headers=FTM1)).status_code == 404)
        check("a reason is required", (await c.post(f"/customers/tds-reviews/{part_id}/approve", headers=FTM1, json={"reason": " "})).status_code == 422)

        r = await c.post(f"/customers/tds-reviews/{part_id}/approve", headers=FTM1, json={"reason": "Customer confirmed they deduct TDS"})
        check("approving applies the TDS as version 2", r.status_code == 200 and r.json()["status"] == "Approved" and r.json()["version"] == 2, r.text[:300])
        tds_amount, collectible, version, status = await inv_row("T-PART")
        check("the part-paid invoice now has TDS 2,000, to collect 18,000, still PartlyPaid", (tds_amount, collectible, version, status) == (D("2000.00"), D("18000.00"), 2, "PartlyPaid"), str((tds_amount, collectible, version, status)))
        check("approving twice -> 409", (await c.post(f"/customers/tds-reviews/{part_id}/approve", headers=FTM1, json={"reason": "again"})).status_code == 409)

        r = await c.post(f"/customers/tds-reviews/{disp_id}/reject", headers=FTM1, json={"reason": "It is in dispute; leave it"})
        check("rejecting leaves the disputed invoice as it was", r.status_code == 200 and r.json()["status"] == "Rejected" and (await inv_row("T-DISP"))[0] == D("0") and (await inv_row("T-DISP"))[2] == 1)
        r = await c.get("/queues/items", headers=FTM1, params={"queue": "tds_review"})
        check("both decisions have left the queue", r.json()["total"] == 0)

        # ---- stale decisions are refused
        r = await c.post(f"/customers/{tds}/recompute-tds", headers=FTM1)
        check("a second recompute proposes the rejected disputed invoice again", r.json()["sent_to_review"] == 1, r.text[:300])
        stale = (await c.get("/queues/items", headers=FTM1, params={"queue": "tds_review"})).json()["items"][0]["item_id"]
        await c.patch(f"/customers/{tds}", headers=FTM1, json={"deducts_tds": False})
        r = await c.post(f"/customers/tds-reviews/{stale}/approve", headers=FTM1, json={"reason": "go"})
        check("approving after the customer's setting changed again -> 409", r.status_code == 409 and "setting changed" in r.json()["detail"], r.text[:300])
        await c.patch(f"/customers/{tds}", headers=FTM1, json={"deducts_tds": True})
        async with SessionLocal() as db:
            await db.execute(text("update invoices set version = version + 1 where invoice_number='T-DISP'"))
            await db.commit()
        r = await c.post(f"/customers/tds-reviews/{stale}/approve", headers=FTM1, json={"reason": "go"})
        check("approving after the invoice itself changed -> 409", r.status_code == 409 and "invoice changed" in r.json()["detail"], r.text[:300])
        async with SessionLocal() as db:
            await db.execute(text("update invoices set version = version - 1 where invoice_number='T-DISP'"))
            await db.execute(text("update invoices set status='Paid', amount_paid=collectible_total where invoice_number='T-DISP'"))
            await db.commit()
        r = await c.post(f"/customers/tds-reviews/{stale}/approve", headers=FTM1, json={"reason": "go"})
        check("approving after the invoice was paid -> 409", r.status_code == 409 and "already paid" in r.json()["detail"], r.text[:300])
        r = await c.post(f"/customers/tds-reviews/{stale}/reject", headers=FTM1, json={"reason": "stale"})
        check("a stale decision can still be rejected", r.status_code == 200)

        # ---- turning TDS off again brings the applied invoice back
        await c.patch(f"/customers/{tds}", headers=FTM1, json={"deducts_tds": False})
        r = await c.post(f"/customers/{tds}/recompute-tds", headers=FTM1)
        check("turning TDS off and recomputing restores the Open invoice as version 3", (await inv_row("T-OPEN"))[:3] == (D("0.00"), D("10000.00"), 3), str(await inv_row("T-OPEN")))
        check("recompute on an unknown customer -> 404", (await c.post(f"/customers/{uuid.uuid4()}/recompute-tds", headers=FTM1)).status_code == 404)

        # ======================================== F. the whole thing leaves the money identities alone
        bad = await q("select count(*) from invoices where collectible_total + coalesce(tds_amount,0) <> amount_inr and type='INVOICE' and invoice_number like 'T-%' and tds_amount is not null")
        check("every invoice still has total = TDS + collectible", bad[0][0] == 0, str(bad))

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
