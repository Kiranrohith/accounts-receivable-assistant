"""Replies to our payment reminders (B7). Real database, the real send into Mailpit, fake Gmail, scripted classifier. Proves:
a reply is tied to the reminder we sent by our Message-ID, by the Gmail thread (once learned) or by the REM reference in the
subject; a stranger in that thread is not ignored; a payment / complaint reply leaves a note on the invoice's history and
tells the approver; a general reply is noted, never dropped; unrelated mail is still ignored; the queue shows the link."""

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from zoneinfo import ZoneInfo

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.jobs import queue
from core.services.mail import ingest, smtp
from core.services.mail.gmail import RawMessage
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice
from jobs.runner import HANDLERS
from schemas.mail.triage import MailClass, MailClassification

D = Decimal
s = get_settings()
assert "ar_rem2" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 22)
RUN = uuid.uuid4().hex[:6]
K = f"ap@zenith-{RUN}.test"
STRANGER = f"colleague@zenith-{RUN}.test"
results = []


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


def mail(subject, body, *, sender=K, in_reply_to=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "genworx.ardemo@gmail.com", subject
    m["Message-ID"] = f"<{uuid.uuid4().hex}@client.test>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)
    return m.as_bytes()


class FakeGmail:
    def __init__(self):
        self.messages, self.order, self.labelled = {}, [], []

    def add(self, mid, raw, thread=None):
        self.messages[mid] = RawMessage(mid, thread or f"t-{mid}", "1", 1_760_000_000_000, ("INBOX",), raw)
        self.order.insert(0, mid)

    async def list_message_ids(self, query, limit):
        return self.order[:limit]

    async def get_raw(self, mid):
        return self.messages[mid]

    async def ensure_labels(self, names):
        return {n: n for n in names}

    async def add_labels(self, mid, ids):
        self.labelled.append((mid, tuple(ids)))

    async def remove_labels(self, mid, ids):
        pass

    async def aclose(self):
        pass


SCRIPT, CALLS = {}, []


async def scripted(mail_obj, context, qualification, *, is_reply):
    CALLS.append(mail_obj.subject)
    cls, conf = SCRIPT[mail_obj.subject]
    return MailClassification(mail_class=cls, confidence=conf, reason=f"scripted {cls.value}"), "scripted", 1


async def triage():
    while True:
        await ex("update jobs set run_after = now() + interval '1 day' where state = 'Queued' and job_type <> 'mail_triage'")
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
        if job is None:
            return
        await queue.execute(job.id, "w-test", HANDLERS)


async def arrive(fake, mid, raw, thread, subject, cls, conf=93):
    fake.add(mid, raw, thread)
    SCRIPT[subject] = (cls, conf)
    await ingest.poll(fake)
    await triage()


async def row(subject):
    return (await q("select id, status::text, classification::text, label_applied from ingested_emails where subject=:s", s=subject))[0]


async def jobs_of(subject):
    return await q(
        "select j.job_type, j.state::text from jobs j join source_documents d on d.id = j.entity_id "
        "join ingested_emails e on e.id = d.ingested_email_id where e.subject = :s order by j.created_at", s=subject)


async def history(invoice_number):
    return [r[0] for r in await q("select reason from state_log where entity_type='invoice' and entity_id=(select id from invoices where invoice_number=:n) order by occurred_at, created_at", n=invoice_number)]


async def main():
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True
    import core.services.mail.triage as triage_mod

    triage_mod.classification.classify_mail = scripted
    queue.retry_delays_seconds = (3600, 3600, 3600)
    smtp.retry_delays_seconds = (0, 0, 0)

    async with SessionLocal() as db:
        c = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email=K, tax_id_type="GSTIN", tax_id="29ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.flush()
        for n in (1, 2, 3):
            db.add(Invoice(customer_id=c.id, invoice_number=f"Z-{n}", invoice_date=TODAY - timedelta(days=30), due_date=TODAY - timedelta(days=n),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as hc, httpx.AsyncClient(timeout=30) as mp:
        assert (await mp.get(f"{MAILPIT}/api/v1/info")).status_code == 200, "Mailpit must be running on :8025"
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await hc.post("/admin/users", headers=ADM, json={"name": "Fa One", "email": "fa.one@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.one@example.com', 'pass12345')).json()['access_token']}"}
        fa_id = (await q("select id from users where email='fa.one@example.com'"))[0][0]

        # three reminders written by the scan and sent by the associate
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": datetime(2026, 10, 22, 10, tzinfo=IST).isoformat()})
        sent = {}
        for n in (1, 2, 3):
            rid = (await q("select r.id from reminders r join invoices i on i.id=r.invoice_id where i.invoice_number=:n and r.state='Drafted'", n=f"Z-{n}"))[0][0]
            r = await hc.post(f"/reminders/{rid}/approve", headers=FA)
            assert r.status_code == 200, r.text
            sent[n] = (await q("select id, message_id, sent_subject from reminders where id=:i", i=rid))[0]
        mid1, mid2, mid3 = (sent[n][1] for n in (1, 2, 3))
        check("three reminders were sent, each with its own Message-ID", len({mid1, mid2, mid3}) == 3 and all(m.startswith("<") for m in (mid1, mid2, mid3)), str((mid1, mid2, mid3)))

        # ---- 1. the customer answers with a payment: found by our Message-ID ---------------------------------------
        CALLS.clear()
        await arrive(fake, "g-1", mail("Re: Payment reminder - Invoice Z-1", "Paid yesterday, UTR 998877, Rs 1000", in_reply_to=mid1), "TH-1", "Re: Payment reminder - Invoice Z-1", MailClass.PAYMENT, 94)
        check("a payment reply to our reminder: handed to the payment pipeline (a job)", [j for j in await jobs_of("Re: Payment reminder - Invoice Z-1")] == [("payment_extraction", "Queued")], str(await jobs_of("Re: Payment reminder - Invoice Z-1")))
        h = await history("Z-1")
        check("and a note on the invoice's history names the reminder, the sender and the mail link",
              any("replied to our payment reminder with a payment advice" in x and K in x and "mail.google.com" in x for x in h), str(h)[-300:])
        notes = await q("select message, type::text from notifications where recipient_user_id=:u and type='ReminderReply'", u=fa_id)
        check("the associate who sent the reminder got a ReminderReply notice", len(notes) == 1 and "Z-1" in notes[0][0], str(notes))
        step = (await q("select output_summary->'thread'->>'kind', output_summary->'thread'->>'how' from step_log where step_name='handoff' and entity_id=:e", e=(await row("Re: Payment reminder - Invoice Z-1"))[0]))[0]
        check("the hand-off step log records the link", step == ("reminder", "a reply to our mail"), str(step))
        learned = (await q("select gmail_thread_id from reminders where id=:i", i=sent[1][0]))[0][0]
        check("the reminder learned its Gmail thread from the reply", learned == "TH-1", str(learned))

        # ---- 2. a stranger replies in the same Gmail thread, no headers: found by the thread ------------------------
        CALLS.clear()
        await arrive(fake, "g-2", mail("ok thanks", "Noted, we will look at it.", sender=STRANGER), "TH-1", "ok thanks", MailClass.GENERAL, 95)
        r2 = await row("ok thanks")
        check("a stranger in a reminder's Gmail thread is NOT ignored without a look: the model was asked", "ok thanks" in CALLS, str(CALLS))
        check("general reply to a reminder: Processed, labelled as a payment-side conversation, not Ignored", r2[1:] == ("Processed", "general", "AR/Processed-Payment"), str(r2))
        check("noted on the invoice, no job", any("nothing in it is a payment or a complaint" in x for x in await history("Z-1")) and await jobs_of("ok thanks") == [])
        check("the approver is told again", (await q("select count(*) from notifications where recipient_user_id=:u and type='ReminderReply'", u=fa_id))[0][0] == 2)
        lst = await hc.get("/notifications", headers=FA)
        check("the notification list loads with ReminderReply rows in it (it used to fail on the unknown type)",
              lst.status_code == 200 and [x["type"] for x in lst.json()["items"]].count("ReminderReply") == 2, lst.text[:200])

        # ---- 3. only the REM reference in the subject: found by it; a complaint ----------------------------------------
        ref2 = "REM-" + str(sent[2][0])[:8].upper()
        check("the subject we sent carries the reference", ref2 in sent[2][2], sent[2][2])
        await arrive(fake, "g-3", mail(f"Fwd: reminder [{ref2}]", "We dispute this invoice, the goods never arrived.", sender=STRANGER), "TH-3", f"Fwd: reminder [{ref2}]", MailClass.DISPUTE, 92)
        r3 = await row(f"Fwd: reminder [{ref2}]")
        check("a complaint in a reminder's conversation is handed to the dispute pipeline", (await jobs_of(f"Fwd: reminder [{ref2}]")) == [("dispute_extraction", "Queued")] and r3[2] == "dispute", str(await jobs_of(f"Fwd: reminder [{ref2}]")))
        h = await history("Z-2")
        check("and the invoice's history says so, the link found by the subject reference", any("replied to our payment reminder with a complaint" in x for x in h), str(h)[-300:])
        step = (await q("select output_summary->'thread'->>'how' from step_log where step_name='triage' and entity_id=:e", e=r3[0]))[0][0]
        check("the triage step records how it was found", step == "the reminder reference in the subject", str(step))
        det = (await hc.get(f"/mail/triage/{r3[0]}", headers=FA)).json()
        check("the mail detail names the conversation it continues", (det["replies_to"] or "").startswith(ref2) and "reminder reference" in det["replies_to"], str(det["replies_to"]))

        # ---- 4. unrelated mail is still ignored, with no model call -----------------------------------------------------
        CALLS.clear()
        await arrive(fake, "g-4", mail("Weekly newsletter", "Buy our stuff", sender="promo@vendor.test"), None, "Weekly newsletter", MailClass.GENERAL)
        r4 = await row("Weekly newsletter")
        check("an unrelated stranger is ignored without a model call", r4[1:] == ("Processed", "general", "AR/Ignored") and CALLS == [], f"{r4} {CALLS}")

        # ---- 5. a reply to a draft that was never sent cannot be matched (nothing was sent) ------------------------------
        check("only SENT reminders carry a Message-ID", (await q("select count(*) from reminders where message_id is not null and state <> 'Sent'"))[0][0] == 0)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
