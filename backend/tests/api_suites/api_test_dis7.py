"""The resolution e-mail: resolving a dispute leaves a DRAFT for its owner (never sent by itself), who
edits it and sends it. Real database, REAL SMTP (the local Mailpit). Throwaway DB only."""

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

import core.services.mail.smtp as smtp
from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice

D = Decimal
s = get_settings()
assert "ar_dis7" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def sql(query, **params):
    async with SessionLocal() as db:
        r = await db.execute(text(query), params)
        rows = r.all() if r.returns_rows else None
        await db.commit()
        return rows


def mails():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        out = []
        for item in m.get("/api/v1/messages").json().get("messages", []):
            full = m.get(f"/api/v1/message/{item['ID']}").json()
            out.append({"to": [a["Address"] for a in full["To"]], "subject": full["Subject"], "text": full["Text"]})
        return out


def clear_mail():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        m.delete("/api/v1/messages")


async def main():
    clear_mail()
    smtp.retry_delays_seconds = (0.05, 0.05, 0.05)
    async with SessionLocal() as db:
        cust = Customer(customer_name="D7 Check Ltd", contact="Priya", email="ap@d7check.test", gstin="27ABCDE1234F1Z5",
                        deducts_tds=False, billing_currency="INR", country="India")
        db.add(cust)
        await db.flush()
        for n in range(1, 12):
            db.add(Invoice(customer_id=cust.id, invoice_number=f"D7-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()
        CID = cust.id
        INV = {r[0]: str(r[1]) for r in (await db.execute(text("select invoice_number, id from invoices"))).all()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        H = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        for name, email, role in [("Fa A", "fa.a@example.com", "FA"), ("Fa B", "fa.b@example.com", "FA"), ("Ftm A", "ftm.a@example.com", "FTM")]:
            assert (await c.post("/admin/users", headers=H, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
            await asyncio.sleep(0.03)
        tok = {}
        for e in ("fa.a", "fa.b", "ftm.a"):
            tok[e] = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': f'{e}@example.com', 'password': 'pass12345'})).json()['access_token']}"}
        FA, FB, TA = tok["fa.a"], tok["fa.b"], tok["ftm.a"]
        uid = {r[0]: str(r[1]) for r in await sql("select email, id from users")}
        BY_ID = {uid["fa.a@example.com"]: FA, uid["fa.b@example.com"]: FB}

        async def new(inv, *, frm="priya@d7check.test"):
            r = await c.post("/disputes", headers=H, json={"invoice_id": INV[inv], "summary": "goods arrived damaged",
                                                           "category": "Quality", "from_email": frm})
            assert r.status_code == 201, r.text
            return r.json()["dispute_id"]

        async def detail(did):
            return (await c.get(f"/disputes/{did}", headers=H)).json()

        NARR = "Internal: agreed a 5,000 credit with the account manager (ref CN-77); customer escalated twice."

        # ============================================ A: resolving leaves a draft, sends nothing
        a = await new("D7-1")
        own = BY_ID[(await detail(a))["assigned_to"]]
        other = FB if own is FA else FA
        r = await c.post(f"/disputes/{a}/resolve", headers=own, json={"narrative": NARR})
        j = r.json()
        draft = j.get("resolution_draft") or {}
        check("A: resolve -> 200 and the response carries a resolution draft", r.status_code == 200 and j["status"] == "Resolved" and draft.get("message_id"), r.text[:200])
        ref = "DSP-" + a[:8].upper()
        check("A: the subject carries our reference and the fixed wording", draft.get("subject") == f"Resolution — your query on {ref}", str(draft.get("subject")))
        body = draft.get("body", "")
        check("A: the body greets the contact, has the narrative slot filled, the fixed closing and the footer",
              "Dear Priya," in body and NARR in body and f"regarding {ref}" in body
              and "please reply" in body and "Accounts Receivable Team" in body, body[:200])
        check("A: nothing was sent and no first response was stamped", mails() == [] and (await detail(a))["first_response_at"] is None)
        dd = await detail(a)
        check("A: the dispute lists it as a draft message", [m["kind"] for m in dd["messages"]].count("draft") == 1)
        check("A: a second resolve -> 409", (await c.post(f"/disputes/{a}/resolve", headers=own, json={"narrative": NARR})).status_code == 409)
        r = await c.post(f"/disputes/{a}/drafts", headers=own, json={})
        check("A: no NEW clarification draft on a resolved dispute -> 409", r.status_code == 409, r.text[:100])

        # ============================================ B: the owner edits and sends it
        mid = draft["message_id"]
        r = await c.patch(f"/disputes/{a}/messages/{mid}", headers=other, json={"body": "x"})
        check("B: another FA cannot edit it -> 403", r.status_code == 403, r.text[:100])
        r = await c.post(f"/disputes/{a}/messages/{mid}/send", headers=TA)
        check("B: an FTM who does not own it cannot send it -> 403", r.status_code == 403, r.text[:100])
        SAFE = "Dear Priya,\n\nWe have issued you a credit of INR 5,000 against this invoice. Thank you for your patience.\n\nAccounts Receivable"
        r = await c.patch(f"/disputes/{a}/messages/{mid}", headers=own, json={"body": SAFE})
        check("B: the owner edits it into customer-safe wording", r.status_code == 200 and "Internal" not in r.json()["body"], r.text[:150])
        r = await c.post(f"/disputes/{a}/messages/{mid}/send", headers=own)
        check("B: the owner sends it -> 200, status stays Resolved", r.status_code == 200 and r.json()["status"] == "Resolved" and r.json()["sent_to"] == "priya@d7check.test", r.text[:150])
        got = mails()
        check("B: exactly one mail went out, to the complaint's address, subject intact (em dash too), edited text only",
              len(got) == 1 and got[0]["to"] == ["priya@d7check.test"] and got[0]["subject"] == f"Resolution — your query on {ref}"
              and "credit of INR 5,000" in got[0]["text"] and "Internal" not in got[0]["text"], str(got)[:260])
        dd = await detail(a)
        check("B: the message is now 'sent' and the first response is still NOT stamped by it", [m["kind"] for m in dd["messages"]].count("sent") == 1 and dd["first_response_at"] is None and dd["status"] == "Resolved")
        log = await sql("select reason from state_log where entity_type='dispute' and entity_id=:d order by occurred_at", d=a)
        check("B: the sending is logged on the dispute", any("Resolution e-mail sent" in x[0] for x in log), str(log[-2:]))
        r = await c.post(f"/disputes/{a}/messages/{mid}/send", headers=own)
        check("B: sending it again -> 409, no second mail", r.status_code == 409 and len(mails()) == 1)
        r = await c.patch(f"/disputes/{a}/messages/{mid}", headers=own, json={"body": "again"})
        check("B: editing it after it was sent -> 409", r.status_code == 409)

        # ============================================ C: an admin resolves; the owner (and the admin) can still send
        clear_mail()
        cc = await new("D7-2")
        owner_c = BY_ID[(await detail(cc))["assigned_to"]]
        r = await c.post(f"/disputes/{cc}/resolve", headers=H, json={"narrative": "Settled by phone."})
        mid_c = r.json()["resolution_draft"]["message_id"]
        check("C: an admin resolves someone else's dispute -> 200 with a draft", r.status_code == 200 and mid_c)
        stranger = FB if owner_c is FA else FA
        check("C: another FA still cannot send it -> 403", (await c.post(f"/disputes/{cc}/messages/{mid_c}/send", headers=stranger)).status_code == 403)
        r = await c.post(f"/disputes/{cc}/messages/{mid_c}/send", headers=owner_c)
        check("C: the dispute's owner sends it -> 200", r.status_code == 200 and len(mails()) == 1, r.text[:120])

        # ============================================ D: an escalated dispute is resolved by a team member
        clear_mail()
        dsp = await new("D7-3")
        await sql("update disputes set status='Escalated', assigned_to=:u, escalated_at=now() where id=:d", u=uid["ftm.a@example.com"], d=dsp)
        r = await c.post(f"/disputes/{dsp}/resolve", headers=TA, json={"narrative": "Customer accepted a corrected invoice."})
        mid_d = r.json().get("resolution_draft", {}).get("message_id")
        check("D: the FTM resolves an escalated dispute -> 200 with a draft", r.status_code == 200 and mid_d, r.text[:150])
        r = await c.patch(f"/disputes/{dsp}/messages/{mid_d}", headers=TA, json={"subject": "Your query is resolved"})
        check("D: the FTM (who lacks the normal-dispute permission) can still edit it", r.status_code == 200 and r.json()["subject"] == "Your query is resolved", r.text[:120])
        r = await c.post(f"/disputes/{dsp}/messages/{mid_d}/send", headers=TA)
        got = mails()
        check("D: and send it; our reference is added to the edited subject", r.status_code == 200 and len(got) == 1 and "DSP-" in got[0]["subject"] and got[0]["subject"].startswith("Your query is resolved"), str(got)[:200])
        check("D: the FA who lost the case cannot send it -> 403", (await c.post(f"/disputes/{dsp}/messages/{mid_d}/send", headers=FA)).status_code == 403)

        # ============================================ E: an unsent clarification draft is set aside
        e = await new("D7-4")
        await sql("insert into dispute_messages (id, dispute_id, kind, body, created_at) values (gen_random_uuid(), :d, 'draft', 'Subject: Old\n\nOld clarification text', now())", d=e)
        owner_e = BY_ID[(await detail(e))["assigned_to"]]
        r = await c.post(f"/disputes/{e}/resolve", headers=owner_e, json={"narrative": "Fixed."})
        kinds = await sql("select kind::text, body from dispute_messages where dispute_id=:d order by created_at", d=e)
        old = [k for k in kinds if "Old clarification" in k[1]]
        check("E: the stale clarification draft became an internal note, labelled", old and old[0][0] == "internal_note" and old[0][1].startswith("Unsent draft, set aside"), str(old))
        check("E: exactly one draft remains: the resolution e-mail", [k[0] for k in kinds].count("draft") == 1 and "Resolution" in [k[1] for k in kinds if k[0] == "draft"][0])

        # ============================================ F: the mail server is down, then up
        clear_mail()
        f = await new("D7-5")
        owner_f = BY_ID[(await detail(f))["assigned_to"]]
        mid_f = (await c.post(f"/disputes/{f}/resolve", headers=owner_f, json={"narrative": "Done."})).json()["resolution_draft"]["message_id"]
        real = s.smtp_port
        s.smtp_port = 1
        r = await c.post(f"/disputes/{f}/messages/{mid_f}/send", headers=owner_f)
        s.smtp_port = real
        notes = (await c.get("/notifications", headers=owner_f)).json()
        fail = [n for n in notes if n["type"] == "SendFailure" and n["entity_id"] == f]
        check("F: mail server down -> 502, the draft stays", r.status_code == 502 and [m["kind"] for m in (await detail(f))["messages"]].count("draft") == 1, r.text[:120])
        check("F: the owner is told, and the notice does not talk about a deadline", fail and "deadline" not in fail[0]["message"], str(fail))
        r = await c.post(f"/disputes/{f}/messages/{mid_f}/send", headers=owner_f)
        check("F: when it is back, the same draft sends", r.status_code == 200 and len(mails()) == 1)

        # ============================================ G: nobody to send to
        g = await new("D7-6", frm="")
        owner_g = BY_ID[(await detail(g))["assigned_to"]]
        mid_g = (await c.post(f"/disputes/{g}/resolve", headers=owner_g, json={"narrative": "Done."})).json()["resolution_draft"]["message_id"]
        await sql("update customers set email='not-an-address' where id=:c", c=CID)
        r = await c.post(f"/disputes/{g}/messages/{mid_g}/send", headers=owner_g)
        check("G: no valid address -> 422 and the draft stays", r.status_code == 422 and [m["kind"] for m in (await detail(g))["messages"]].count("draft") == 1, r.text[:120])
        await sql("update customers set email='ap@d7check.test' where id=:c", c=CID)
        r = await c.post(f"/disputes/{g}/messages/{mid_g}/send", headers=owner_g)
        check("G: once the customer has an address it goes to the customer's address", r.status_code == 200 and r.json()["sent_to"] == "ap@d7check.test", r.text[:120])

        # ============================================ H: the rest of the module is unaffected
        h = await new("D7-7")
        owner_h = BY_ID[(await detail(h))["assigned_to"]]
        check("H: an ordinary clarification draft on an OPEN dispute still can't be edited by a stranger", (await c.patch(f"/disputes/{h}/messages/{uuid.uuid4()}", headers=(FB if owner_h is FA else FA), json={"body": "x"})).status_code in (403, 404))
        raised = await c.post("/disputes", headers=H, json={"invoice_id": INV["D7-8"], "summary": "unsure what this is about"})
        rr = await c.post(f"/disputes/{raised.json()['dispute_id']}/resolve", headers=H, json={"narrative": "n/a n/a"})
        check("H: a dispute in the classify queue cannot be resolved -> 409 and no draft", rr.status_code == 409 and "draft" not in rr.text.lower())

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
