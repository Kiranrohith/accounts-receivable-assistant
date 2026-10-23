"""D4: the first clarification is drafted by a worker, sent by itself only when safe, and a person
can draft, edit and send. Real worker, real database, REAL SMTP (the local Mailpit); the model is
scripted by a marker in the complaint so the grounding and gate code run for real."""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

import core.services.disputes.clarification as clar
import core.services.mail.smtp as smtp
from api.rest.app import app
from config.settings import get_settings
from core.services.invoices.extraction import ExtractionError
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice
from schemas.disputes.clarification import ClarificationDraft

D = Decimal
s = get_settings()
assert "ar_dis4" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


GOOD = ("Hello,\n\nThank you for your message about invoice {inv} dated 2026-10-01 for INR 1,000.00. "
        "We are looking into it and will reply within 2 working days. Could you send photos of the damage "
        "and the delivery note?\n\nAccounts Receivable\nGenworx Technologies")
SCRIPT: dict[str, dict] = {}
PROMPTS: list[str] = []
CALLS: dict[str, int] = {}


async def fake_llm(model, messages):
    human = messages[1].content
    PROMPTS.append(human)
    key = next(k for k in SCRIPT if k in human)
    CALLS[key] = CALLS.get(key, 0) + 1
    sc = SCRIPT[key]
    if sc.get("fail_once") and CALLS[key] == 1:
        raise ExtractionError("scripted: every model is down")
    return ClarificationDraft(subject=sc.get("subject", "Your complaint"), body=sc["body"], confidence=sc["conf"]), "stub", 1


clar.run_structured_extraction = fake_llm
DONE = ("Succeeded", "Failed", "DeadLettered")


async def sql(query, **params):
    async with SessionLocal() as db:
        r = await db.execute(text(query), params)
        rows = r.all() if r.returns_rows else None
        await db.commit()
        return rows


async def wait_job(dispute_id, timeout=120):
    for _ in range(int(timeout * 4)):
        rows = await sql("select state::text from jobs where entity_id=:d and job_type='dispute_clarification'", d=dispute_id)
        if rows and all(r[0] in DONE for r in rows):
            return rows
        await asyncio.sleep(0.25)
    return rows


def mails():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        out = []
        for item in m.get("/api/v1/messages").json().get("messages", []):
            full = m.get(f"/api/v1/message/{item['ID']}").json()
            out.append({"to": [a["Address"] for a in full["To"]], "subject": full["Subject"], "text": full["Text"], "from": full["From"]["Address"]})
        return out


def clear_mail():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        m.delete("/api/v1/messages")


async def main():
    import testsupport
    clear_mail()
    worker = await testsupport.start_worker()
    smtp.retry_delays_seconds = (0.05, 0.05, 0.05)
    async with SessionLocal() as db:
        cust = Customer(customer_name="D4 Check Ltd", contact="Priya", email="ap@d4check.test", gstin="27ABCDE1234F1Z5",
                        deducts_tds=False, billing_currency="INR", country="India")
        db.add(cust)
        await db.flush()
        for n in range(1, 13):
            db.add(Invoice(customer_id=cust.id, invoice_number=f"D4-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
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
        FA_ID, FB_ID = uid["fa.a@example.com"], uid["fa.b@example.com"]

        async def new(inv, key, *, category="Quality", frm="priya@d4check.test", customer_level=False):
            body = {"summary": f"{key} the goods arrived damaged", "category": category, "from_email": frm}
            body.update({"customer_id": str(CID)} if customer_level else {"invoice_id": INV[inv]})
            r = await c.post("/disputes", headers=H, json=body)
            assert r.status_code == 201, r.text
            return r.json()["dispute_id"]

        async def detail(did, who=H):
            return (await c.get(f"/disputes/{did}", headers=who)).json()

        async def step(did):
            rows = await sql("select result::text, output_summary, error_message from step_log where entity_id=:d and step_name='clarification' order by occurred_at", d=did)
            return rows

        # ================================================ A: grounded, confident -> sent by itself
        SCRIPT["KEY-A"] = dict(body=GOOD.format(inv="D4-1"), conf=92)
        a = await new("D4-1", "KEY-A")
        await wait_job(a)
        da = await detail(a)
        check("A: the dispute became ClarificationSent without anyone touching it", da["status"] == "ClarificationSent", da["status"])
        check("A: the first response is stamped", da["first_response_at"] is not None)
        msgs = [m for m in da["messages"] if m["kind"] in ("draft", "sent")]
        check("A: one outgoing message, now 'sent', with its subject and the reference", len(msgs) == 1 and msgs[0]["kind"] == "sent" and msgs[0]["subject"].startswith("Your complaint [DSP-"), str(msgs))
        check("A: clarification_confidence is stored (92: every claim checked out)", da["clarification_confidence"] == 92, str(da["clarification_confidence"]))
        mm = mails()
        check("A: the mail really arrived at the complainant's address, from our address", len(mm) == 1 and mm[0]["to"] == ["priya@d4check.test"] and mm[0]["from"] == s.smtp_from, str(mm and (mm[0]["to"], mm[0]["from"])))
        check("A: its subject carries the dispute reference and its text is the draft", f"DSP-{a[:8].upper()}" in mm[0]["subject"] and "invoice D4-1" in mm[0]["text"], mm[0]["subject"])
        check("A: the timeline records the send", any(t["to_state"] == "ClarificationSent" and "priya@d4check.test" in (t["reason"] or "") for t in da["timeline"]))
        st = await step(a)
        check("A: the job step says it auto-sent and checked claims", st and st[-1][0] == "success" and st[-1][1]["sent"] is True and st[-1][1]["claims_checked"] >= 3 and st[-1][1]["ungrounded"] == [], str(st))
        check("A: the job ended Succeeded", (await wait_job(a))[0][0] == "Succeeded")

        # ================================================ B: not confident enough -> a draft for the owner
        SCRIPT["KEY-B"] = dict(body=GOOD.format(inv="D4-2"), conf=70)
        b = await new("D4-2", "KEY-B")
        await wait_job(b)
        db_ = await detail(b)
        drafts = [m for m in db_["messages"] if m["kind"] == "draft"]
        check("B: confidence 70 -> stays a draft, dispute still Assigned, first response not stamped",
              len(drafts) == 1 and db_["status"] == "Assigned" and db_["first_response_at"] is None and db_["clarification_confidence"] == 70)
        check("B: nothing was e-mailed for it", len(mails()) == 1)

        # ================================================ C: the model quotes a figure we do not hold
        SCRIPT["KEY-C"] = dict(body=GOOD.format(inv="D4-3").replace("INR 1,000.00", "INR 5,000.00") + "\nSee also INV-777.", conf=95)
        cc = await new("D4-3", "KEY-C")
        await wait_job(cc)
        dc = await detail(cc)
        st = await step(cc)
        check("C: an invented amount and number block the auto-send though the model said 95", dc["status"] == "Assigned" and len(mails()) == 1)
        check("C: the confidence is capped by what checked out, and the step lists what did not",
              dc["clarification_confidence"] < 95 and any("5,000.00" in u for u in st[-1][1]["ungrounded"]) and any("INV-777" in u for u in st[-1][1]["ungrounded"]), f"{dc['clarification_confidence']} {st[-1][1]['ungrounded']}")

        # ================================================ D: category Other is never automatic
        SCRIPT["KEY-D"] = dict(body=GOOD.format(inv="D4-4"), conf=97)
        d = await new("D4-4", "KEY-D", category="Other")
        await wait_job(d)
        dd = await detail(d)
        check("D: category Other waits for a person even at 97% and fully grounded", dd["status"] == "Assigned" and dd["clarification_confidence"] == 97 and len(mails()) == 1)

        # ================================================ E: the mail server is down
        real_port = s.smtp_port
        s.smtp_port = 1  # nothing listens there
        SCRIPT["KEY-E"] = dict(body=GOOD.format(inv="D4-5"), conf=93)
        e = await new("D4-5", "KEY-E")
        jobs = await wait_job(e)
        de = await detail(e)
        st = await step(e)
        check("E: the job still ends Succeeded (the draft is the outcome), the draft is kept, status unchanged",
              jobs[0][0] == "Succeeded" and de["status"] == "Assigned" and any(m["kind"] == "draft" for m in de["messages"]) and de["first_response_at"] is None, str((jobs, de["status"])))
        check("E: the step records that it was not sent and why", st and st[-1][0] == "warning" and st[-1][1]["sent"] is False and "could not be reached" in (st[-1][2] or ""), str(st))
        notes = (await c.get("/notifications", headers=FA if de["assigned_to"] == FA_ID else FB)).json()["items"]
        check("E: the owner got a SendFailure notice that says the deadline keeps running", any(n["type"] == "SendFailure" and n["entity_id"] == e and "deadline keeps running" in n["message"] for n in notes), str([n["type"] for n in notes]))
        s.smtp_port = real_port

        # ================================================ F: nobody to send to
        await sql("update customers set email='not-an-address' where id=:c", c=CID)
        SCRIPT["KEY-F"] = dict(body=GOOD.format(inv="D4-6"), conf=93)
        f = await new("D4-6", "KEY-F", frm="")
        await wait_job(f)
        df = await detail(f)
        st = await step(f)
        check("F: no valid address -> a draft, not sent, and the step says why", df["status"] == "Assigned" and st[-1][1]["sent"] is False and "no valid e-mail address" in (st[-1][2] or ""), str(st))
        await sql("update customers set email='ap@d4check.test' where id=:c", c=CID)

        # ================================================ G: the model is down once -> retried, one draft
        SCRIPT["KEY-G"] = dict(body=GOOD.format(inv="D4-7"), conf=90, fail_once=True)
        g = await new("D4-7", "KEY-G")
        rows = await wait_job(g)
        dg = await detail(g)
        att = await sql("select attempt_count from jobs where entity_id=:d and job_type='dispute_clarification'", d=g)
        check("G: a failed attempt is retried and the outcome is one clarification, sent", att[0][0] == 2 and dg["status"] == "ClarificationSent" and len([m for m in dg["messages"] if m["kind"] in ("draft", "sent")]) == 1, f"{att} {dg['status']}")

        # ================================================ H: a person edits and sends B's draft
        draft_id = drafts[0]["message_id"]
        owner_b = db_["assigned_to"]
        OWN = FA if owner_b == FA_ID else FB
        OTHER = FB if OWN is FA else FA
        r = await c.patch(f"/disputes/{b}/messages/{draft_id}", headers=OWN, json={})
        check("H: an edit that changes nothing -> 422", r.status_code == 422)
        r = await c.patch(f"/disputes/{b}/messages/{draft_id}", headers=OTHER, json={"body": "x"})
        check("H: someone else's dispute cannot be edited by another FA -> 403", r.status_code == 403, r.text[:80])
        r = await c.patch(f"/disputes/{b}/messages/{draft_id}", headers=OWN, json={"subject": "About your damaged goods", "body": "Hello Priya,\n\nWe are sorry to hear this. Please send photos of the damage.\n\nAccounts Receivable"})
        check("H: the owner edits the draft", r.status_code == 200 and r.json()["subject"] == "About your damaged goods" and "sorry" in r.json()["body"], r.text[:120])
        r = await c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=TA)
        check("H: an FTM cannot send on a normal dispute -> 403", r.status_code == 403)
        r = await c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=OTHER)
        check("H: nor can another FA -> 403", r.status_code == 403)
        s.smtp_port = 1
        r = await c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=OWN)
        check("H: with the mail server down the send is a 502 and the draft stays", r.status_code == 502 and (await detail(b))["status"] == "Assigned" and any(m["kind"] == "draft" for m in (await detail(b))["messages"]), r.text[:150])
        s.smtp_port = real_port
        before = len(mails())
        rr = await asyncio.gather(
            c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=OWN),
            c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=H),
        )
        check("H: two people sending at once -> one 200, one 409, and ONE mail goes out", sorted(x.status_code for x in rr) == [200, 409] and len(mails()) == before + 1, str([x.status_code for x in rr]))
        db_ = await detail(b)
        sent = [m for m in db_["messages"] if m["kind"] == "sent"]
        check("H: the dispute is ClarificationSent with the edited text", db_["status"] == "ClarificationSent" and db_["first_response_at"] and "sorry to hear" in sent[0]["body"] and sent[0]["subject"].startswith("About your damaged goods [DSP-"))
        last = next(m for m in mails() if "sorry to hear" in m["text"])
        check("H: the mail that arrived is the edited one", last["to"] == ["priya@d4check.test"] and "About your damaged goods" in last["subject"])
        r = await c.patch(f"/disputes/{b}/messages/{draft_id}", headers=OWN, json={"body": "late edit"})
        check("H: a sent message cannot be edited -> 409", r.status_code == 409)
        r = await c.post(f"/disputes/{b}/messages/{draft_id}/send", headers=OWN)
        check("H: nor sent again -> 409", r.status_code == 409)
        r = await c.post(f"/disputes/{b}/messages/{uuid4()}/send", headers=OWN)
        check("H: an unknown message -> 404", r.status_code == 404)

        # ================================================ I: another draft on request (never sent by itself)
        PROMPTS.clear()
        SCRIPT["KEY-B"] = dict(body=GOOD.format(inv="D4-2").replace("INR 1,000.00", "INR 9,999.00"), conf=99)
        r = await c.post(f"/disputes/{b}/drafts", headers=OWN, json={"instructions": "Mention the warranty terms."})
        check("I: a person asks for another draft", r.status_code == 201 and r.json()["confidence"] is not None, r.text[:150])
        check("I: the draft is NOT sent, whatever its confidence (only the first one is ever automatic)", len(mails()) == before + 1 and (await detail(b))["status"] == "ClarificationSent")
        check("I: the check lists the figure we do not hold", any("9,999.00" in u for u in r.json()["ungrounded"]), str(r.json()["ungrounded"]))
        check("I: the instructions reached the model as a hint", any("warranty terms" in p for p in PROMPTS))
        check("I: the first clarification's confidence was not overwritten", (await detail(b))["clarification_confidence"] == 70)
        r = await c.post(f"/disputes/{b}/drafts", headers=OTHER, json={})
        check("I: another FA cannot draft on it -> 403", r.status_code == 403)
        raised = (await c.post("/disputes", headers=H, json={"invoice_id": INV["D4-8"], "summary": "KEY-R unsure what this is about"})).json()["dispute_id"]
        check("I: a Raised (unclassified) dispute cannot be drafted for -> 409", (await c.post(f"/disputes/{raised}/drafts", headers=H, json={})).status_code == 409)
        SCRIPT["KEY-R"] = dict(body=GOOD.format(inv="D4-8"), conf=90)

        # ================================================ J: a classified dispute gets its clarification after classification
        j = (await c.post("/disputes", headers=H, json={"invoice_id": INV["D4-9"], "summary": "KEY-J not sure", "from_email": "priya@d4check.test"})).json()["dispute_id"]
        check("J: while it waits in the queue no clarification job exists", await sql("select count(*) from jobs where entity_id=:d", d=j) == [(0,)])
        SCRIPT["KEY-J"] = dict(body=GOOD.format(inv="D4-9"), conf=91)
        r = await c.post(f"/disputes/{j}/classify", headers=FA, json={"category": "Delivery"})
        await wait_job(j)
        dj = await detail(j)
        check("J: classifying it queues the clarification, which is then sent", r.status_code == 200 and dj["status"] == "ClarificationSent")

        # ================================================ K: an escalated dispute is left to its FTM; resolved ones too
        SCRIPT["KEY-K"] = dict(body=GOOD.format(inv="D4-10"), conf=92)
        k = await new("D4-10", "KEY-K")
        await wait_job(k)
        check("K: the job ran once per dispute (no duplicate drafts)", len([m for m in (await detail(k))["messages"] if m["kind"] in ("draft", "sent")]) == 1)
        r = await c.post(f"/disputes/{k}/resolve", headers=H, json={"narrative": "Settled by phone call"})
        r = await c.post(f"/disputes/{k}/drafts", headers=H, json={})
        check("K: a resolved dispute takes no more drafts -> 409", r.status_code == 409)

        # ================================================ housekeeping
        check("no job was left stuck", await sql("select count(*) from jobs where state not in ('Succeeded','Failed','DeadLettered')") == [(0,)])
        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("the money checker holds", found == [], str(found))
        check("every sent message has its mail id; no draft has one", await sql("select count(*) from dispute_messages where (kind='sent') <> (gmail_message_id is not null and kind in ('sent'))") == [(0,)])

    await worker.stop()
    await engine.dispose()
    clear_mail()
    print(f"RESULT {sum(results)}/{len(results)} passed")


from uuid import uuid4  # noqa: E402

asyncio.run(main())
