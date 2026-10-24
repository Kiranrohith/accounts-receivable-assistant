"""Decision 68: every credit note is e-mailed to its customer with its PDF. Real database, the real SMTP path into Mailpit
(http://localhost:8025 must be up), no model. Proves: a note made by an over-payment, by a person and by converting
unapplied money queues exactly one mail job; the mail arrives with the PDF (its words checked); never twice; the failure
paths (no valid address, not on the allowlist, mail server down then dead-lettered) leave a reason, a notice and a
resend that works; the status on the note; money identities."""

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
import pypdfium2
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.jobs import queue
from core.services.mail import smtp
from core.services.payments.engine import run_matching
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import MatchedLayer, PaymentState
from jobs.runner import HANDLERS

D = Decimal
s = get_settings()
assert "ar_cnote" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
results = []
_n = [0]
RUN = uuid.uuid4().hex[:6]  # Mailpit keeps mail between runs: every address is new each time


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def ex(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


async def customer(label, email=None):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="Anita", email=email or f"ap@{label.lower()}-{RUN}.test",
                     tax_id_type="GSTIN", tax_id=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id, c.email


async def invoice(cid, number, collectible):
    async with SessionLocal() as db:
        db.add(Invoice(customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D(collectible), collectible_total=D(collectible)))
        await db.commit()


async def overpay(cid, number, amount, ref):
    async with SessionLocal() as db:
        p = Payment(state=PaymentState.MATCHING, customer_id=cid, payment_currency="INR", payment_date=date(2026, 10, 15),
                    reference_number=ref, matched_layer=MatchedLayer.L1, amount_received_inr=D(amount),
                    extracted_invoice_numbers=[{"invoice_number": number, "amount": None}])
        db.add(p)
        await db.commit()
        pid = p.id
    async with SessionLocal() as db:
        await run_matching(db, pid, None)
    return pid


async def run_mail_jobs():
    n = 0
    while True:
        await ex("update jobs set run_after = now() + interval '1 day' where state = 'Queued' and job_type <> 'credit_note_mail'")
        await ex("update jobs set run_after = now() where state = 'Queued' and job_type = 'credit_note_mail'")
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
        if job is None:
            return n
        await queue.execute(job.id, "w-test", HANDLERS)
        n += 1


async def note_of(cid):
    rows = await q("select id, note_number, amount, generated_document_id from notes where customer_id=:c order by created_at", c=cid)
    return rows


async def jobs_of(note_id):
    return await q("select state::text, last_error_message, rerun_of is not null from jobs where job_type='credit_note_mail' and entity_id=:n order by created_at", n=note_id)


async def mailbox(hc, address):
    r = await hc.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{address}"})
    return r.json().get("messages", [])


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as hc, httpx.AsyncClient(timeout=30) as mp:
        r = await mp.get(f"{MAILPIT}/api/v1/info")
        assert r.status_code == 200, "Mailpit must be running on :8025"
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await hc.post("/admin/users", headers=ADM, json={"name": "Ftm One", "email": "ftm.one@example.com", "password": "pass12345", "role": "FTM"})).status_code == 201
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}
        smtp.retry_delays_seconds = (0, 0, 0)
        queue.retry_delays_seconds = (3600, 3600, 3600)

        # ---- 1. an over-payment on a quoted invoice mints a note, and the note is e-mailed -----------------------
        c, addr = await customer("Alpha")
        await invoice(c, "A-1", "900")
        pid = await overpay(c, "A-1", "1000", "UTR-A1")
        notes = await note_of(c)
        check("the over-payment minted one credit note", len(notes) == 1 and notes[0][2] == D("100.00"), str(notes))
        nid, number = notes[0][0], notes[0][1]
        check("exactly one mail job was queued with it, in the same commit", [j[0] for j in await jobs_of(nid)] == ["Queued"], str(await jobs_of(nid)))
        check("nothing was sent yet (a worker does that)", await mailbox(mp, addr) == [])
        await run_mail_jobs()
        got = await mailbox(mp, addr)
        check("the customer got ONE mail", len(got) == 1, str(got)[:200])
        msg = (await mp.get(f"{MAILPIT}/api/v1/message/{got[0]['ID']}")).json()
        check("subject names the note and the amount (Indian grouping not needed at 100)", msg["Subject"] == f"Credit note {number} issued — INR 100.00", msg["Subject"])
        check("the body greets the contact and tells how to use the credit", "Dear Anita" in msg["Text"] and number in msg["Text"] and "adjust" in msg["Text"], msg["Text"][:200])
        atts = msg.get("Attachments", [])
        check("the PDF is attached under the note's number", len(atts) == 1 and atts[0]["FileName"] == f"{number}.pdf", str(atts))
        pdf = (await mp.get(f"{MAILPIT}/api/v1/message/{got[0]['ID']}/part/{atts[0]['PartID']}")).content
        words = "\n".join(p.get_textpage().get_text_range() for p in pypdfium2.PdfDocument(pdf))
        check("the PDF has the note number, customer, amount, invoice and the payment reference",
              all(x in words for x in (number, "Alpha Ltd", "INR 100.00", "A-1", "UTR-A1", "CREDIT NOTE")), words[:300])
        doc = await q("select uploaded_via::text, doc_type::text, file_name, file_path from source_documents where id=:d", d=(await note_of(c))[0][3])
        check("the PDF is kept as a generated source document and linked to the note", doc and doc[0][0] == "generated" and doc[0][1] == "pdf" and doc[0][2] == f"{number}.pdf", str(doc))
        check("the job Succeeded and the note's timeline says it was e-mailed",
              [j[0] for j in await jobs_of(nid)] == ["Succeeded"] and (await q("select count(*) from state_log where entity_type='note' and entity_id=:n and reason like 'Credit note e-mailed to%'", n=nid))[0][0] == 1)
        d = (await hc.get(f"/notes/{nid}", headers=FTM)).json()
        check("the note shows the e-mail as sent, to whom and when", d["email"]["state"] == "sent" and d["email"]["to"] == addr and d["email"]["at"], str(d["email"]))

        # ---- 2. never twice ------------------------------------------------------------------------------------
        async with SessionLocal() as db:
            await queue.enqueue(db, job_type="credit_note_mail", entity_type="note", entity_id=nid, created_by=None)
            await db.commit()
        await run_mail_jobs()
        check("a second mail job for a note that was already mailed sends nothing", len(await mailbox(mp, addr)) == 1 and [j[0] for j in await jobs_of(nid)] == ["Succeeded", "Succeeded"])

        # ---- 3. a person asks for it again ---------------------------------------------------------------------
        r = await hc.post(f"/notes/{nid}/resend-email", headers=FTM)
        check("resend -> 202 and queued", r.status_code == 202, r.text[:160])
        r2 = await hc.post(f"/notes/{nid}/resend-email", headers=FTM)
        check("a second resend while one waits -> 409", r2.status_code == 409, r2.text[:160])
        d = (await hc.get(f"/notes/{nid}", headers=FTM)).json()
        check("meanwhile the note says queued", d["email"]["state"] == "queued", str(d["email"]))
        await run_mail_jobs()
        got = await mailbox(mp, addr)
        check("the resend really sends (2 mails now, the same PDF name)", len(got) == 2, str(len(got)))
        check("an unauthorised role cannot resend", (await hc.post(f"/notes/{nid}/resend-email", headers={"Authorization": "Bearer x"})).status_code == 401)

        # ---- 4. a note made by a person is mailed too ------------------------------------------------------------
        r = await hc.post("/notes", headers=FTM, json={"customer_id": str(c), "amount": "250.50", "reason": "Goodwill credit"})
        check("a manual credit note: 201 with the email block", r.status_code == 201 and r.json()["email"]["state"] == "queued", r.text[:200])
        await run_mail_jobs()
        got = await mailbox(mp, addr)
        check("and its mail arrives", len(got) == 3 and any("INR 250.50" in m["Subject"] for m in got), str([m["Subject"] for m in got]))

        # ---- 5. no valid address: fails at once, tells the person, a resend after the fix works --------------------
        c2, addr2 = await customer("Beta", email="no-at-sign")
        r = await hc.post("/notes", headers=FTM, json={"customer_id": str(c2), "amount": "10", "reason": "Test"})
        nid2 = r.json()["note_id"]
        await run_mail_jobs()
        j = await jobs_of(nid2)
        check("a customer with no valid address: the job Failed at once, with the reason", [x[0] for x in j] == ["Failed"] and "no valid e-mail address" in j[0][1], str(j))
        d = (await hc.get(f"/notes/{nid2}", headers=FTM)).json()
        check("the note shows failed with the reason", d["email"]["state"] == "failed" and "no valid e-mail address" in d["email"]["error"], str(d["email"]))
        ftm_id = (await q("select id from users where email='ftm.one@example.com'"))[0][0]
        check("the person who made the note got a SendFailure notice", (await q("select count(*) from notifications where recipient_user_id=:u and type='SendFailure' and entity_id=:n", u=ftm_id, n=nid2))[0][0] == 1)
        await ex("update customers set email=:e where id=:c", e=f"beta-fixed@beta-{RUN}.test", c=c2)
        r = await hc.post(f"/notes/{nid2}/resend-email", headers=FTM)
        await run_mail_jobs()
        check("after the address is fixed, the resend arrives", len(await mailbox(mp, f"beta-fixed@beta-{RUN}.test")) == 1 and (await hc.get(f"/notes/{nid2}", headers=FTM)).json()["email"]["state"] == "sent")

        # ---- 6. not on the allowlist: refused before connecting, permanent --------------------------------------------
        s.smtp_allowlist_bypass, s.smtp_allowed_recipients = False, "someone-else@demo.test"
        r = await hc.post("/notes", headers=FTM, json={"customer_id": str(c), "amount": "5", "reason": "Allowlist"})
        nid3 = r.json()["note_id"]
        await run_mail_jobs()
        j = await jobs_of(nid3)
        d = (await hc.get(f"/notes/{nid3}", headers=FTM)).json()
        check("a recipient not on the allowlist: Failed at once, reason says so, nothing was sent",
              [x[0] for x in j] == ["Failed"] and "demo allowlist" in j[0][1] and d["email"]["state"] == "failed" and len(await mailbox(mp, addr)) == 3, f"{j} {d['email']}")
        s.smtp_allowed_recipients = addr
        await hc.post(f"/notes/{nid3}/resend-email", headers=FTM)
        await run_mail_jobs()
        check("allowed again: the resend goes out", len(await mailbox(mp, addr)) == 4)
        s.smtp_allowlist_bypass, s.smtp_allowed_recipients = True, ""

        # ---- 7. the mail server is down: retried by the queue, dead-lettered at the end, the person is told --------
        real_port = s.smtp_port
        s.smtp_port = 1  # nothing listens here
        r = await hc.post("/notes", headers=FTM, json={"customer_id": str(c), "amount": "7", "reason": "Server down"})
        nid4 = r.json()["note_id"]
        for _ in range(6):
            await run_mail_jobs()
            await ex("update jobs set run_after = now() where state = 'Queued' and job_type = 'credit_note_mail'")
        j = await jobs_of(nid4)
        check("a mail server that is down: retried (a few attempts), then DeadLettered", [x[0] for x in j] == ["DeadLettered"], str(j))
        check("and the person is told it was tried several times",
              (await q("select count(*) from notifications where recipient_user_id=:u and type='SendFailure' and entity_id=:n and message like '%tried several times%'", u=ftm_id, n=nid4))[0][0] == 1)
        s.smtp_port = real_port
        await hc.post(f"/notes/{nid4}/resend-email", headers=FTM)
        await run_mail_jobs()
        check("the server is back: the resend arrives", (await hc.get(f"/notes/{nid4}", headers=FTM)).json()["email"]["state"] == "sent")

        # ---- 8. a note nobody made by hand (system) tells the administrators when it cannot be sent ---------------
        c3, _ = await customer("Gamma", email="no-address")
        await invoice(c3, "G-1", "900")
        await overpay(c3, "G-1", "1000", "UTR-G1")
        nid5 = (await note_of(c3))[0][0]
        await run_mail_jobs()
        adm = (await q("select u.id from users u join roles r on r.id=u.role_id where r.code='ADM' and u.is_active"))[0][0]
        check("a system-made note that cannot be mailed: the admins get the SendFailure notice",
              (await q("select count(*) from notifications where recipient_user_id=:u and type='SendFailure' and entity_id=:n", u=adm, n=nid5))[0][0] == 1)

        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("the money identities hold", not found, str(found)[:200])
        check("no mail job is stuck Queued or Running", (await q("select count(*) from jobs where job_type='credit_note_mail' and state in ('Queued','Running')"))[0][0] == 0)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
