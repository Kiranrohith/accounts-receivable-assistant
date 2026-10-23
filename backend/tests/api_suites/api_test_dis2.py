"""D2: the dispute workflow over HTTP on a throwaway database (no LLM)."""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.disputes import calendar
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice

D = Decimal
s = get_settings()
assert "ar_dis2" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def sql(query: str, **params):
    async with SessionLocal() as db:
        result = await db.execute(text(query), params)
        rows = result.all() if result.returns_rows else None
        await db.commit()
        return rows


async def scalar(query: str, **params):
    rows = await sql(query, **params)
    return rows[0][0] if rows else None


MONDAY = datetime(2027, 3, 1, 6, 0, tzinfo=UTC)  # a Monday in the future, so these disputes are never overdue whatever day the suite runs


async def main():
    async with SessionLocal() as db:
        c = Customer(customer_name="D2 Check Ltd", contact="A", email="d2@check.test", gstin="27ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        c2 = Customer(customer_name="D2 Other Ltd", contact="B", email="d2b@check.test", gstin="29ABCDE1234F1Z5",
                      deducts_tds=False, billing_currency="INR", country="India")
        db.add_all([c, c2])
        await db.flush()
        invs = []
        for n in range(1, 11):
            inv = Invoice(customer_id=c.id, invoice_number=f"D2-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                          invoice_currency="INR", amount_inr=D("100"), collectible_total=D("100"), po_number="PO-77" if n == 1 else None)
            invs.append(inv)
        other_inv = Invoice(customer_id=c2.id, invoice_number="D2-X", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                            invoice_currency="INR", amount_inr=D("100"), collectible_total=D("100"))
        db.add_all(invs + [other_inv])
        await db.commit()
        CID, CID2 = c.id, c2.id
        I = [str(i.id) for i in invs]
        IX = str(other_inv.id)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in [("Fa A", "fa.a@example.com", "FA"), ("Fa B", "fa.b@example.com", "FA"), ("Ftm A", "ftm.a@example.com", "FTM"), ("Ftm B", "ftm.b@example.com", "FTM")]:
            r = await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})
            assert r.status_code == 201, r.text
            await asyncio.sleep(0.02)
        tok = {}
        for e in ("fa.a", "fa.b", "ftm.a", "ftm.b"):
            tok[e] = {"Authorization": f"Bearer {(await login(f'{e}@example.com', 'pass12345')).json()['access_token']}"}
        FA, FB, TA, TB = tok["fa.a"], tok["fa.b"], tok["ftm.a"], tok["ftm.b"]
        ids = {r[0]: str(r[1]) for r in await sql("select email, id from users")}
        ADM_ID = ids[s.seed_admin_email.strip().lower()]
        FA_ID, FB_ID, TA_ID, TB_ID = (ids[f"{e}@example.com"] for e in ("fa.a", "fa.b", "ftm.a", "ftm.b"))
        post = hc.post
        get = hc.get

        # ============================================================ entering a dispute
        body = {"invoice_id": I[0], "summary": "Goods arrived damaged", "category": "Quality", "received_at": MONDAY.isoformat(), "gmail_thread_id": "th-1"}
        check("no token -> 401", (await post("/disputes", json=body)).status_code == 401)
        check("FTM cannot enter a dispute (needs disputes.work.normal) -> 403", (await post("/disputes", headers=TA, json=body)).status_code == 403)
        check("neither invoice nor customer -> 422", (await post("/disputes", headers=ADM, json={"summary": "x y z"})).status_code == 422)
        check("an invoice that does not exist -> 404", (await post("/disputes", headers=ADM, json={**body, "invoice_id": str(uuid.uuid4())})).status_code == 404)
        check("an invoice of another customer -> 422", (await post("/disputes", headers=ADM, json={**body, "invoice_id": IX, "customer_id": str(CID)})).status_code == 422)
        check("an unknown category -> 422", (await post("/disputes", headers=ADM, json={**body, "category": "Rude"})).status_code == 422)

        r = await post("/disputes", headers=ADM, json=body)
        d1 = r.json()
        check("a classified complaint is Assigned straight away", r.status_code == 201 and d1["status"] == "Assigned" and d1["assigned_to"] == FA_ID and not d1["appended_to_existing"], str(d1))
        det = (await get(f"/disputes/{d1['dispute_id']}", headers=FA)).json()
        check("the customer follows from the invoice; classified by the person", det["customer_id"] == str(CID) and det["classified_by"] == ADM_ID and det["customer_name"] == "D2 Check Ltd")
        check("deadlines: 2 and 10 working days from the next working day (a Monday)",
              det["first_response_due"] == "2027-03-03" and det["resolution_due"] == calendar.resolution_due(MONDAY).isoformat(), f"{det['first_response_due']} {det['resolution_due']}")
        check("the invoice is flagged disputed", await scalar("select is_disputed from invoices where id=:i", i=I[0]) is True)
        check("timeline: received, categorised, assigned", [t["to_state"] for t in det["timeline"]] == ["Raised", "Categorised", "Assigned"], str([t["to_state"] for t in det["timeline"]]))
        check("the owner got an Assignment notice naming the invoice", (await get("/notifications", headers=FA)).json()[0]["type"] == "Assignment" and "D2-1" in (await get("/notifications", headers=FA)).json()[0]["message"])

        # load balancing: FA A has 1, FA B has 0 -> B; then tie -> the longest-serving (A)
        d2 = (await post("/disputes", headers=ADM, json={"invoice_id": I[1], "summary": "Wrong price", "category": "Pricing", "gmail_thread_id": "th-2"})).json()
        d3 = (await post("/disputes", headers=ADM, json={"invoice_id": I[2], "summary": "Wrong tax", "category": "TaxDiscrepancy"})).json()
        check("the least-loaded FA gets the next one, ties go to the longest-serving", d2["assigned_to"] == FB_ID and d3["assigned_to"] == FA_ID, f"{d2['assigned_to']} {d3['assigned_to']}")

        # a dispute with no category waits in the queue
        r = await post("/disputes", headers=FA, json={"invoice_id": I[3], "summary": "Something is wrong with this one"})
        d4 = r.json()
        check("no category -> Raised, unassigned, in the queue", r.status_code == 201 and d4["status"] == "Raised" and d4["assigned_to"] is None)

        # E2: another complaint on an invoice with an open dispute becomes a message
        r = await post("/disputes", headers=FA, json={"invoice_id": I[0], "summary": "Also the box was crushed", "category": "Delivery", "gmail_thread_id": "th-1"})
        check("E2: a reply in the thread of an open dispute is appended, not duplicated", r.status_code == 201 and r.json()["appended_to_existing"] and r.json()["dispute_id"] == d1["dispute_id"])
        check("E2: still one dispute on that invoice", await scalar("select count(*) from disputes where invoice_id=:i", i=I[0]) == 1)
        det = (await get(f"/disputes/{d1['dispute_id']}", headers=FA)).json()
        check("E2: the message is on the dispute", [m["kind"] for m in det["messages"]] == ["inbound_email"] and "crushed" in det["messages"][0]["body"] and det["messages"][0]["author_name"] == "Fa A")

        follow = lambda uid, did: sql("select count(*) from notifications where recipient_user_id=:u and type='DisputeFollowUp' and entity_id=:d", u=uid, d=did)
        check("E2: the owner who typed the follow-up is not notified of their own entry", (await follow(FA_ID, d1["dispute_id"]))[0][0] == 0)
        r = await post("/disputes", headers=ADM, json={"invoice_id": I[0], "summary": "Customer called again", "category": "Quality", "gmail_thread_id": "th-1"})
        n = (await follow(FA_ID, d1["dispute_id"]))[0][0]
        check("E2: a follow-up entered by someone else notifies the owner (DisputeFollowUp)", r.json()["appended_to_existing"] and n == 1 and (await follow(FB_ID, d1["dispute_id"]))[0][0] == 0)
        note = [x for x in (await get("/notifications", headers=FA)).json() if x["type"] == "DisputeFollowUp"][0]
        check("E2: the notice points at the dispute and names the invoice", note["entity_id"] == d1["dispute_id"] and "D2-1" in note["message"])
        det = (await get(f"/disputes/{d1['dispute_id']}", headers=FA)).json()
        check("E2: both follow-ups stay visible in the dispute detail", [m["kind"] for m in det["messages"]] == ["inbound_email", "inbound_email"])

        # E1: a complaint about the account
        r = await post("/disputes", headers=ADM, json={"customer_id": str(CID), "summary": "Statement does not match our books", "category": "Other", "gmail_thread_id": "th-acct"})
        e1 = r.json()
        det = (await get(f"/disputes/{e1['dispute_id']}", headers=ADM)).json()
        check("E1: a customer-level dispute has no invoice", r.status_code == 201 and det["invoice_id"] is None and det["invoice"] is None and det["customer_id"] == str(CID))
        r = await post("/disputes", headers=ADM, json={"customer_id": str(CID), "summary": "And the statement total too", "category": "Other", "gmail_thread_id": "th-acct"})
        check("E1: a reply in the thread of the account dispute is appended to it", r.json()["appended_to_existing"] and r.json()["dispute_id"] == e1["dispute_id"])
        check("a customer that does not exist -> 404", (await post("/disputes", headers=ADM, json={"customer_id": str(uuid.uuid4()), "summary": "abc"})).status_code == 404)

        # ============================================================ the classify queue
        check("FTM cannot see the classify queue -> 403", (await get("/disputes/classify-queue", headers=TA)).status_code == 403)
        q = (await get("/disputes/classify-queue", headers=FA)).json()
        check("the queue holds the Raised dispute", [x["dispute_id"] for x in q] == [d4["dispute_id"]] and q[0]["status"] == "Raised")

        # a dispute that arrived with no customer or invoice (as an uploaded complaint can)
        orphan = str(uuid.uuid4())
        await sql("insert into disputes (id, status, payment_related, received_at, summary, first_response_due, resolution_due) values (:i, 'Raised', false, :t, 'unknown sender', :a, :b)",
                  i=orphan, t=datetime.now(UTC) - timedelta(hours=1), a=date(2027, 3, 2), b=date(2027, 3, 16))
        q = (await get("/disputes/classify-queue", headers=FA)).json()
        check("oldest first, and a dispute can be in the queue with no customer", [x["dispute_id"] for x in q] == [orphan, d4["dispute_id"]] and q[0]["customer_id"] is None)

        r = await post(f"/disputes/{orphan}/classify", headers=FB, json={"category": "Pricing", "payment_related": True, "invoice_id": I[3]})
        check("classify attaches an invoice that already has an open dispute (independent, not merged), sets the customer, assigns", r.status_code == 200 and r.json()["status"] == "Assigned" and r.json()["assigned_to"] is not None, r.text[:150])
        det = (await get(f"/disputes/{orphan}", headers=FB)).json()
        check("customer and invoice were filled in; payment_related kept; classified_by is the person",
              det["customer_id"] == str(CID) and det["invoice_number"] == "D2-4" and det["payment_related"] is True and det["classified_by"] == FB_ID)
        check("the attached invoice is flagged disputed", await scalar("select is_disputed from invoices where id=:i", i=I[3]) is True)
        check("it did not touch the other dispute on that invoice", (await get(f"/disputes/{d4['dispute_id']}", headers=FA)).json()["status"] == "Raised")
        check("classify again -> 409", (await post(f"/disputes/{orphan}/classify", headers=FB, json={"category": "Quality"})).status_code == 409)
        check("classify of an unknown dispute -> 404", (await post(f"/disputes/{uuid.uuid4()}/classify", headers=FB, json={"category": "Quality"})).status_code == 404)
        check("a customer given for a dispute that has one -> 422", (await post(f"/disputes/{d4['dispute_id']}/classify", headers=FB, json={"category": "Quality", "customer_id": str(CID2)})).status_code == 422)

        # two people classify the same one at once: one wins
        rr = await asyncio.gather(
            post(f"/disputes/{d4['dispute_id']}/classify", headers=FA, json={"category": "Quality"}),
            post(f"/disputes/{d4['dispute_id']}/classify", headers=FB, json={"category": "Delivery"}),
        )
        check("two classify at once -> one 200, one 409", sorted(x.status_code for x in rr) == [200, 409], str([x.status_code for x in rr]))
        check("the queue is empty again", (await get("/disputes/classify-queue", headers=FA)).json() == [])

        # ============================================================ notes
        notes_on = d2["dispute_id"]  # owned by FA B
        r = await post(f"/disputes/{notes_on}/notes", headers=FB, json={"body": "Called the customer"})
        check("the owner adds a note -> 201", r.status_code == 201 and "message_id" in r.json())
        check("another FA may add a note too", (await post(f"/disputes/{notes_on}/notes", headers=FA, json={"body": "FYI price list v3"})).status_code == 201)
        check("FTM cannot note a dispute that is not escalated -> 403", (await post(f"/disputes/{notes_on}/notes", headers=TA, json={"body": "x"})).status_code == 403)
        check("an empty note -> 422", (await post(f"/disputes/{notes_on}/notes", headers=FB, json={"body": ""})).status_code == 422)
        check("a note on an unknown dispute -> 404", (await post(f"/disputes/{uuid.uuid4()}/notes", headers=FB, json={"body": "x"})).status_code == 404)

        # ============================================================ resolve
        r = await post(f"/disputes/{d1['dispute_id']}/resolve", headers=FB, json={"narrative": "settled with a credit"})
        check("someone else's dispute cannot be resolved by an FA -> 403", r.status_code == 403, r.text[:100])
        r = await post(f"/disputes/{d4['dispute_id']}/resolve", headers=FA, json={"narrative": "n"})
        check("a narrative that is too short -> 422", r.status_code == 422)
        # a Raised dispute cannot be resolved
        rs = await post("/disputes", headers=FA, json={"invoice_id": I[5], "summary": "Raised one for resolve test", "gmail_thread_id": "th-5"})
        raised = rs.json()["dispute_id"]
        check("a Raised dispute cannot be resolved -> 409", (await post(f"/disputes/{raised}/resolve", headers=ADM, json={"narrative": "nope nope"})).status_code == 409)
        r = await post(f"/disputes/{d1['dispute_id']}/resolve", headers=FA, json={"narrative": "Credit note promised and agreed"})
        check("the owner resolves -> 200 Resolved", r.status_code == 200 and r.json()["status"] == "Resolved", r.text[:120])
        det = (await get(f"/disputes/{d1['dispute_id']}", headers=FA)).json()
        check("resolution is recorded", det["resolution_narrative"] == "Credit note promised and agreed" and det["resolved_by"] == FA_ID and det["resolved_at"])
        check("the invoice is no longer flagged disputed", await scalar("select is_disputed from invoices where id=:i", i=I[0]) is False)
        check("resolving again -> 409", (await post(f"/disputes/{d1['dispute_id']}/resolve", headers=FA, json={"narrative": "again again"})).status_code == 409)
        check("notes on a resolved dispute -> 409", (await post(f"/disputes/{d1['dispute_id']}/notes", headers=FA, json={"body": "late"})).status_code == 409)

        # E3: a new complaint on the resolved invoice -> a NEW dispute, linked
        r = await post("/disputes", headers=ADM, json={"invoice_id": I[0], "summary": "Same problem again", "category": "Quality", "gmail_thread_id": "th-1"})
        e3 = r.json()
        check("E3: a reply in the thread of a resolved dispute opens a new one, linked to it", r.status_code == 201 and not e3["appended_to_existing"] and e3["linked_dispute_id"] == d1["dispute_id"] and e3["dispute_id"] != d1["dispute_id"], str(e3))
        old = (await get(f"/disputes/{d1['dispute_id']}", headers=FA)).json()
        check("E3: the old one shows the later dispute, the new one the earlier", [x["dispute_id"] for x in old["later_disputes"]] == [e3["dispute_id"]] and (await get(f"/disputes/{e3['dispute_id']}", headers=FA)).json()["earlier_dispute"]["dispute_id"] == d1["dispute_id"])
        check("E3: the invoice is disputed again", await scalar("select is_disputed from invoices where id=:i", i=I[0]) is True)

        # ---- decision 62 (E10): a new thread about the same invoice is its OWN dispute, shown as related
        def note_line(did):
            return sql("select message from notifications where entity_id=:d and type='Assignment' order by created_at", d=did)
        ra = (await post("/disputes", headers=ADM, json={"invoice_id": I[8], "summary": "Invoice total is wrong", "category": "Pricing", "received_at": MONDAY.isoformat(), "gmail_thread_id": "th-10a"})).json()
        rb = (await post("/disputes", headers=ADM, json={"invoice_id": I[8], "summary": "And the goods never came", "category": "Delivery", "received_at": "2027-03-02T06:00:00Z", "gmail_thread_id": "th-10b"})).json()
        check("E10: two complaints in two new threads are two disputes, none merged or linked",
              ra["dispute_id"] != rb["dispute_id"] and not ra["appended_to_existing"] and not rb["appended_to_existing"] and ra["linked_dispute_id"] is None and rb["linked_dispute_id"] is None)
        da = (await get(f"/disputes/{ra['dispute_id']}", headers=ADM)).json()
        db_ = (await get(f"/disputes/{rb['dispute_id']}", headers=ADM)).json()
        check("E10: their deadlines are independent (each from its own receipt)",
              da["first_response_due"] == "2027-03-03" and db_["first_response_due"] == "2027-03-04" and da["resolution_due"] != db_["resolution_due"], f"{da['first_response_due']} {db_['first_response_due']}")
        check("E10: each shows the other in its possibly-related panel, with status, category, assignee and a link",
              [x["dispute_id"] for x in da["possibly_related"]] == [rb["dispute_id"]] and [x["dispute_id"] for x in db_["possibly_related"]] == [ra["dispute_id"]]
              and db_["possibly_related"][0]["category"] == "Pricing" and db_["possibly_related"][0]["status"] == "Assigned"
              and db_["possibly_related"][0]["assigned_to_name"] and db_["possibly_related"][0]["link"] == f"/disputes/{ra['dispute_id']}"
              and db_["possibly_related"][0]["resolved_at"] is None and db_["possibly_related"][0]["in_same_chain"] is False)
        check("E10: nothing was written to link them (linked_dispute_id stays empty)", da["earlier_dispute"] is None and da["later_disputes"] == [] and db_["earlier_dispute"] is None)
        check("E10: the first assignment notice has no extra line; the second says the invoice has 1 open / 0 resolved",
              "Note:" not in (await note_line(ra["dispute_id"]))[0][0] and (await note_line(rb["dispute_id"]))[0][0].endswith("\nNote: this invoice has 1 open / 0 resolved dispute(s) that may be related."), str(await note_line(rb["dispute_id"])))
        check("E10: a dispute with no invoice has an empty panel", (await get(f"/disputes/{e1['dispute_id']}", headers=ADM)).json()["possibly_related"] == [])
        await post(f"/disputes/{ra['dispute_id']}/resolve", headers=ADM, json={"narrative": "Corrected invoice sent"})
        check("E10: resolving one leaves the invoice flagged while the other is open", await scalar("select is_disputed from invoices where id=:i", i=I[8]) is True)
        db_ = (await get(f"/disputes/{rb['dispute_id']}", headers=ADM)).json()
        check("E10: the open one now shows the resolved one with its resolution date", db_["possibly_related"][0]["status"] == "Resolved" and db_["possibly_related"][0]["resolved_at"])
        rc = (await post("/disputes", headers=ADM, json={"invoice_id": I[8], "summary": "A third email, no thread", "category": "Other"})).json()
        check("E10: a third, thread-less complaint is yet another dispute; its notice counts 1 open / 1 resolved",
              rc["dispute_id"] not in (ra["dispute_id"], rb["dispute_id"]) and (await note_line(rc["dispute_id"]))[0][0].endswith("1 open / 1 resolved dispute(s) that may be related."), str(await note_line(rc["dispute_id"])))
        check("E10: each deadline clock is its own (the third started now, not from the others)", (await get(f"/disputes/{rc['dispute_id']}", headers=ADM)).json()["received_at"][:10] == calendar.today_ist().isoformat())
        await post(f"/disputes/{rb['dispute_id']}/resolve", headers=ADM, json={"narrative": "Delivered late, apologised"})
        check("E10: still flagged while the third is open", await scalar("select is_disputed from invoices where id=:i", i=I[8]) is True)
        await post(f"/disputes/{rc['dispute_id']}/resolve", headers=ADM, json={"narrative": "Duplicate of the others"})
        check("E10: the flag clears when the last one on the invoice is resolved", await scalar("select is_disputed from invoices where id=:i", i=I[8]) is False)

        # two people resolve the same one at once: one wins (ADM can resolve any; d3 is FA A's)
        rr = await asyncio.gather(
            post(f"/disputes/{d3['dispute_id']}/resolve", headers=FA, json={"narrative": "resolved by owner"}),
            post(f"/disputes/{d3['dispute_id']}/resolve", headers=ADM, json={"narrative": "resolved by admin"}),
        )
        check("two resolve at once -> one 200, one 409", sorted(x.status_code for x in rr) == [200, 409], str([x.status_code for x in rr]))

        # ============================================================ reassign
        target = e1["dispute_id"]  # customer-level, owned by FA ... find the owner
        owner = (await get(f"/disputes/{target}", headers=ADM)).json()["assigned_to"]
        other = FB_ID if owner == FA_ID else FA_ID
        check("an FA cannot reassign -> 403", (await post(f"/disputes/{target}/reassign", headers=FA, json={"assigned_to": other})).status_code == 403)
        r = await post(f"/disputes/{target}/reassign", headers=ADM, json={"assigned_to": other, "reason": "holiday cover"})
        check("admin reassigns -> 200", r.status_code == 200 and r.json()["assigned_to"] == other, r.text[:120])
        check("the new owner is notified", any(n["type"] == "Assignment" and n["entity_id"] == target for n in (await get("/notifications", headers=FA if other == FA_ID else FB)).json()))
        det = (await get(f"/disputes/{target}", headers=ADM)).json()
        check("the timeline records the reassignment and why", "holiday cover" in (det["timeline"][-1]["reason"] or "") and det["timeline"][-1]["triggered_by_id"] == ADM_ID)
        check("reassigning to the same person -> 422", (await post(f"/disputes/{target}/reassign", headers=ADM, json={"assigned_to": other})).status_code == 422)
        check("to an FTM on a normal dispute -> 422", (await post(f"/disputes/{target}/reassign", headers=ADM, json={"assigned_to": TA_ID})).status_code == 422)
        check("to a user that does not exist -> 422", (await post(f"/disputes/{target}/reassign", headers=ADM, json={"assigned_to": str(uuid.uuid4())})).status_code == 422)
        check("a Raised dispute cannot be reassigned -> 409", (await post(f"/disputes/{raised}/reassign", headers=ADM, json={"assigned_to": FA_ID})).status_code == 409)
        check("a Resolved dispute cannot be reassigned -> 409", (await post(f"/disputes/{d1['dispute_id']}/reassign", headers=ADM, json={"assigned_to": FA_ID})).status_code == 409)

        # ---- an escalated dispute (the scheduler does this in D5; here we set it directly)
        esc = d2["dispute_id"]
        await sql("update disputes set status='Escalated', escalated_at=now(), assigned_to=:u where id=:i", u=TA_ID, i=esc)
        check("escalated: an FA cannot note it -> 403", (await post(f"/disputes/{esc}/notes", headers=FB, json={"body": "x"})).status_code == 403)
        check("escalated: the FTM can note it", (await post(f"/disputes/{esc}/notes", headers=TA, json={"body": "Taking this"})).status_code == 201)
        check("escalated: reassign to an FA -> 422", (await post(f"/disputes/{esc}/reassign", headers=ADM, json={"assigned_to": FA_ID})).status_code == 422)
        check("escalated: another FTM cannot resolve it (not theirs) -> 403", (await post(f"/disputes/{esc}/resolve", headers=TB, json={"narrative": "mine now"})).status_code == 403)
        check("escalated: an FA cannot resolve it -> 403", (await post(f"/disputes/{esc}/resolve", headers=FB, json={"narrative": "mine now"})).status_code == 403)
        check("escalated: reassign to another FTM works", (await post(f"/disputes/{esc}/reassign", headers=ADM, json={"assigned_to": TB_ID})).status_code == 200)
        # a follow-up on the escalated dispute goes to the FTM who has it now, not to the FA who lost it
        r = await post("/disputes", headers=ADM, json={"invoice_id": I[1], "summary": "Still waiting for the corrected invoice", "category": "Pricing", "gmail_thread_id": "th-2"})
        check("escalated follow-up: appended to the escalated dispute", r.json()["appended_to_existing"] and r.json()["dispute_id"] == esc)
        check("escalated follow-up: the FTM who owns it is notified; the FA who lost it and the first FTM are not",
              (await follow(TB_ID, esc))[0][0] == 1 and (await follow(FB_ID, esc))[0][0] == 0 and (await follow(TA_ID, esc))[0][0] == 0)
        # a dispute still in the classify queue has no owner: no notice
        r = await post("/disputes", headers=ADM, json={"invoice_id": I[5], "summary": "Chasing this one", "category": "Other", "gmail_thread_id": "th-5"})
        check("queued dispute: a follow-up is appended and nobody is notified",
              r.json()["appended_to_existing"] and r.json()["dispute_id"] == raised and await scalar("select count(*) from notifications where entity_id=:d and type='DisputeFollowUp'", d=raised) == 0)
        r = await post(f"/disputes/{esc}/resolve", headers=TB, json={"narrative": "Escalated and settled"})
        check("escalated: its owner (FTM) resolves it", r.status_code == 200 and r.json()["status"] == "Resolved", r.text[:100])

        # ============================================================ list and detail
        # make one clearly overdue dispute
        late = (await post("/disputes", headers=ADM, json={"invoice_id": I[6], "summary": "Old complaint", "category": "Delivery", "received_at": "2026-01-05T06:00:00Z"})).json()
        everything = (await get("/disputes?limit=200", headers=FA)).json()
        n_all = await scalar("select count(*) from disputes")
        check("list returns all (FA may view)", len(everything) == n_all, f"{len(everything)} vs {n_all}")
        check("list needs a token -> 401", (await get("/disputes")).status_code == 401)
        by_status = lambda st: (hc.get(f"/disputes?status={st}", headers=FA))
        check("filter by status", all(x["status"] == "Resolved" for x in (await by_status("Resolved")).json()) and len((await by_status("Resolved")).json()) == await scalar("select count(*) from disputes where status='Resolved'"))
        check("filter by several statuses", len((await get("/disputes?status=Resolved&status=Raised", headers=FA)).json()) == await scalar("select count(*) from disputes where status in ('Resolved','Raised')"))
        check("open_only hides resolved", all(x["status"] != "Resolved" for x in (await get("/disputes?open_only=true", headers=FA)).json()))
        check("filter by category", all(x["category"] == "Pricing" for x in (await get("/disputes?category=Pricing", headers=FA)).json()) and len((await get("/disputes?category=Pricing", headers=FA)).json()) >= 1)
        check("filter by invoice", [x["invoice_number"] for x in (await get(f"/disputes?invoice_id={I[0]}", headers=FA)).json()].count("D2-1") == 2)
        check("filter by customer", len((await get(f"/disputes?customer_id={CID2}", headers=FA)).json()) == 0)
        check("filter by owner", all(x["assigned_to"] == FA_ID for x in (await get(f"/disputes?assigned_to={FA_ID}", headers=FA)).json()))
        check("filter unassigned", [x["dispute_id"] for x in (await get("/disputes?unassigned=true", headers=FA)).json()] == [raised])
        ov = (await get("/disputes?overdue=true", headers=FA)).json()
        check("overdue: the old complaint, and not the fresh ones", [x["dispute_id"] for x in ov] == [late["dispute_id"]] and ov[0]["first_response_overdue"] and ov[0]["resolution_overdue"], str([x["dispute_id"] for x in ov]))
        check("age_days counts from receipt", ov[0]["age_days"] == (calendar.today_ist() - date(2026, 1, 5)).days, str(ov[0]["age_days"]))
        win = (await get("/disputes?received_from=2026-01-01T00:00:00Z&received_to=2026-02-01T00:00:00Z", headers=FA)).json()
        check("received window", [x["dispute_id"] for x in win] == [late["dispute_id"]])
        first = (await get("/disputes?sort=received_at&order=asc&limit=1", headers=FA)).json()[0]["dispute_id"]
        check("sort ascending by received_at", first == late["dispute_id"])
        check("limit and offset", len((await get("/disputes?limit=2&offset=1", headers=FA)).json()) == 2)
        check("an invalid sort -> 422", (await get("/disputes?sort=colour", headers=FA)).status_code == 422)

        det = (await get(f"/disputes/{e3['dispute_id']}", headers=FA)).json()
        check("detail: the invoice block with its PO number", det["invoice"]["invoice_number"] == "D2-1" and det["invoice"]["po_number"] == "PO-77" and det["invoice"]["collectible_balance"] == "100.00")
        check("detail: nothing paid yet, nothing held", det["payments"] == [] and det["held_payments"] == [])
        check("detail: an unknown dispute -> 404", (await get(f"/disputes/{uuid.uuid4()}", headers=FA)).status_code == 404)
        det = (await get(f"/disputes/{notes_on}", headers=ADM)).json()
        check("detail: notes with their authors, oldest first", [m["author_name"] for m in det["messages"] if m["kind"] == "internal_note"] == ["Fa B", "Fa A", "Ftm A"], str([m["author_name"] for m in det["messages"]]))

        # ============================================================ dashboard
        dash = (await get("/disputes/dashboard", headers=FA)).json()
        n_open = await scalar("select count(*) from disputes where status <> 'Resolved'")
        n_res = await scalar("select count(*) from disputes where status = 'Resolved'")
        check("dashboard: open and resolved", dash["open"] == n_open and dash["resolved"] == n_res and dash["unclassified"] == 1 and dash["escalated"] == 0, str((dash["open"], dash["resolved"], n_open, n_res)))
        check("dashboard: by status adds up", sum(x["total"] for x in dash["by_status"]) == n_all and sum(x["open"] for x in dash["by_status"]) == n_open)
        check("dashboard: by reason adds up (uncategorised shown)", sum(x["total"] for x in dash["by_category"]) == n_all and any(x["key"] == "Uncategorised" and x["open"] == 1 for x in dash["by_category"]))
        check("dashboard: open by age adds up and the old one is in 'over 10 days'", sum(x["open"] for x in dash["open_by_age"]) == n_open and [x["open"] for x in dash["open_by_age"] if x["key"] == "over 10 days"] == [1])
        check("dashboard: overdue counts", dash["first_response_overdue"] == 1 and dash["resolution_overdue"] == 1)
        window_start = datetime.combine(calendar.today_ist() - timedelta(days=29), datetime.min.time(), tzinfo=calendar.IST)
        check("dashboard: 30 days of volume ending today, covering the new disputes", len(dash["volume"]) == 30 and dash["volume"][-1]["day"] == calendar.today_ist().isoformat() and sum(x["raised"] for x in dash["volume"]) == await scalar("select count(*) from disputes where received_at >= :t and received_at < :u", t=window_start, u=window_start + timedelta(days=30)), str(dash["volume"][-3:]))
        check("dashboard: resolved today counted", dash["volume"][-1]["resolved"] == n_res)
        check("dashboard: an average time to resolve", dash["average_days_to_resolve"] is not None)
        check("dashboard: owners with what they carry", sum(o["open"] for o in dash["owners"]) == await scalar("select count(*) from disputes where status <> 'Resolved' and assigned_to is not null") and any(o["overdue"] == 1 for o in dash["owners"]))
        check("dashboard: days=7 -> 7 entries", len((await get("/disputes/dashboard?days=7", headers=FA)).json()["volume"]) == 7 and (await get("/disputes/dashboard?days=0", headers=FA)).status_code == 422)
        check("dashboard needs a token", (await get("/disputes/dashboard")).status_code == 401)

        # ============================================================ no FA available -> an admin owns it
        await sql("update users set is_active=false where email in ('fa.a@example.com','fa.b@example.com')")
        r = await post("/disputes", headers=ADM, json={"invoice_id": I[7], "summary": "Nobody to take this", "category": "Other"})
        check("with no active FA the admin owns it", r.json()["assigned_to"] == ADM_ID, str(r.json()))

        # ============================================================ housekeeping
        bad = await sql("select count(*) from disputes where status <> 'Resolved' and invoice_id is not null and invoice_id in (select id from invoices where not is_disputed)")
        check("every open dispute's invoice is flagged disputed", bad[0][0] == 0)
        bad = await sql("select count(*) from invoices i where is_disputed and not exists (select 1 from disputes d where d.invoice_id=i.id and d.status <> 'Resolved')")
        check("no invoice is flagged disputed without an open dispute", bad[0][0] == 0)
        check("state log rows exist for disputes", await scalar("select count(*) from state_log where entity_type='dispute'") > 10)
        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("the money checker still holds", found == [], str(found))

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
