"""M3: mail triage. Real database, a fake Gmail, a SCRIPTED classifier (the model is measured separately against the kit).
Proves: the no-model rules (and that they spend no model call), the routes (automatic / human queue / ignored), the
queue API, the person's decision (route, pull back out of ignored, 409s, a race), and a model outage retried not guessed."""

import asyncio
import sys
from email.message import EmailMessage

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.jobs import queue
from core.services.mail import classification, gmail, ingest
from core.services.mail.gmail import GmailError, RawMessage
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice
from jobs.runner import HANDLERS
from schemas.mail.triage import MailClass, MailClassification
from datetime import date
from decimal import Decimal

D = Decimal
s = get_settings()
assert "ar_mail2" in s.database_url, "refusing to run against a non-test database"
results = []
PDF = b"%PDF-1.4\n%advice\n" + b"x" * 200


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def ex(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


def mail(subject, body, *, sender, pdf=None, pdf_name="advice.pdf"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "genworx.ardemo@gmail.com", subject
    m.set_content(body)
    if pdf is not None:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename=pdf_name)
    return m.as_bytes()


class FakeGmail:
    def __init__(self):
        self.messages, self.order, self.labelled = {}, [], []

    def add(self, mid, raw):
        self.messages[mid] = RawMessage(mid, f"t-{mid}", "1", 1_760_000_000_000, ("INBOX",), raw)
        self.order.insert(0, mid)

    async def list_message_ids(self, query, limit):
        return self.order[:limit]

    async def get_raw(self, mid):
        return self.messages[mid]

    async def ensure_labels(self, names):
        return {n: f"L-{i}" for i, n in enumerate(names)}

    async def add_labels(self, mid, ids):
        self.labelled.append((mid, ids))

    async def aclose(self):
        pass


SCRIPT: dict[str, object] = {}  # subject -> MailClassification | Exception
CALLS: list[str] = []


async def scripted(mail_obj, context, qualification, *, is_reply):
    CALLS.append(mail_obj.subject)
    outcome = SCRIPT[mail_obj.subject]
    if isinstance(outcome, Exception):
        raise outcome
    return outcome, "scripted-model", 1


def v(cls, confidence=92, mixed=False):
    return MailClassification(mail_class=cls, confidence=confidence, mixed_po_and_payment=mixed, reason=f"scripted {cls.value}")


async def run_all_jobs():
    n = 0
    while True:
        await ex("update jobs set run_after = now() + interval '1 day' where job_type <> 'mail_triage' and state = 'Queued'")
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
        if job is None:
            return n
        await queue.execute(job.id, "w-test", HANDLERS)
        n += 1


async def row(subject):
    return (await q("select status::text, classification::text, classification_confidence, label_applied from ingested_emails where subject=:s", s=subject))[0]


async def main():
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True
    classification.classify_mail = scripted
    import core.services.mail.triage as triage_mod
    triage_mod.classification.classify_mail = scripted
    queue.retry_delays_seconds = (3600, 3600, 3600)  # a retried job is not due again within this test

    async with SessionLocal() as db:
        c = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email="ap@zenith.test", gstin="29ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.flush()
        db.add(Invoice(customer_id=c.id, invoice_number="INV-1001", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()

    K = "ap@zenith.test"
    cases = {
        "auto payment": (mail("auto payment", "Paid INV-1001, advice attached", sender=K, pdf=PDF), v(MailClass.PAYMENT)),
        "unsure": (mail("unsure", "hello, see below", sender=K), v(MailClass.PAYMENT, 60)),
        "spam": (mail("spam", "Win a prize now", sender="promo@spam.example"), None),
        "stranger quotes us": (mail("stranger quotes us", "Please note payment of INV-1001", sender="payer@other.example"), v(MailClass.PAYMENT, 90)),
        "po and advice": (mail("po and advice", "Our PO and the payment advice", sender=K, pdf=PDF), v(MailClass.PURCHASE_ORDER, 97, mixed=True)),
        "body only po": (mail("body only po", "Please supply 10 units, this is our order", sender=K), v(MailClass.PURCHASE_ORDER, 95)),
        "bare known": (mail("", "", sender=K, pdf=PDF), None),
        "bare unknown": (mail("", "", sender="who@unknown.example", pdf=PDF, pdf_name="x.pdf"), None),
        "question": (mail("question", "What is your bank account number?", sender=K), v(MailClass.GENERAL, 96)),
        "complaint": (mail("complaint", "Goods arrived damaged, will not pay INV-1001", sender=K), v(MailClass.DISPUTE, 88)),
        "paid and complaint": (mail("paid and complaint", "Paid 40,000 of 50,000 as goods were damaged", sender=K), v(MailClass.BOTH, 90)),
    }
    for i, (subject, (raw, verdict)) in enumerate(cases.items()):
        fake.add(f"t{i}", raw)
        if verdict is not None:
            SCRIPT[subject] = verdict
    await ingest.poll(fake)

    # ---- the model is down: the job is retried, the mail is not guessed ------------------------------------
    SCRIPT["complaint"] = classification.ExtractionError("all models failed")
    await run_all_jobs()
    down = await row("complaint")
    jobs = await q("select state::text, attempt_count from jobs where entity_id=(select id from ingested_emails where subject='complaint')")
    check("model outage: the mail is left undecided and its job is retried (not guessed, not failed)", down == (None, None, None, None) and jobs[0][0] in ("Queued", "Running") and jobs[0][1] >= 1, f"{down} {jobs}")
    SCRIPT["complaint"] = v(MailClass.DISPUTE, 88)
    await ex("update jobs set run_after = now() where state = 'Queued'")
    await run_all_jobs()
    check("when the model is back the retry decides it: dispute, handed off", (await row("complaint"))[:3] == ("Processed", "dispute", 88), str(await row("complaint")))

    # ---- routes ------------------------------------------------------------------------------------------
    r = await row("auto payment")
    check("confident payment: classified, handed off (Processed), labelled AR/Processed-Payment", r == ("Processed", "payment", 92, "AR/Processed-Payment"), str(r))
    r = await row("unsure")
    check("60 confidence: human triage queue, label AR/Triage, suggestion kept", r == ("HumanTriage", "payment", 60, "AR/Triage"), str(r))
    r = await row("spam")
    check("unknown sender, no context: general, ignored (Processed + AR/Ignored)", r == ("Processed", "general", 90, "AR/Ignored"), str(r))
    check("and the model was never asked about the no-context mails", "spam" not in CALLS and "" not in CALLS, str(CALLS))
    r = await row("stranger quotes us")
    check("an unknown sender who quotes OUR invoice is not ignored: the model decided (payment, handed off)", r[:3] == ("Processed", "payment", 90) and "stranger quotes us" in CALLS, str(r))
    r = await row("po and advice")
    check("a PO mixed with a payment advice goes to a person even at 97", r == ("HumanTriage", "purchase_order", 97, "AR/Triage"), str(r))
    r = await row("body only po")
    check("a PO mail with no usable attachment goes to a person", r == ("HumanTriage", "purchase_order", 95, "AR/Triage"), str(r))
    bare = await q("select status::text, classification::text, classification_confidence from ingested_emails where subject is null and sender='ap@zenith.test'")
    check("a bare attachment from a known customer: payment candidate, handed off, no model call", bare == [("Processed", "payment", 80)], str(bare))
    bare = await q("select status::text, classification::text from ingested_emails where subject is null and sender='who@unknown.example'")
    check("a bare attachment from an unknown sender: ignored", bare == [("Processed", "general")], str(bare))
    r = await row("question")
    check("a confident general mail from a customer: ignored", r == ("Processed", "general", 96, "AR/Ignored"), str(r))
    check("dispute and both are handed off", (await row("complaint"))[:2] == ("Processed", "dispute") and (await row("paid and complaint"))[:2] == ("Processed", "both"))
    step = await q("select output_summary, model_used from step_log where step_name='triage' and entity_id=(select id from ingested_emails where subject='unsure')")
    out = step[0][0]
    check("the triage step log keeps the route, reason, context and the model's verdict", out["route"] == "human" and "Not sure enough" in out["reason"] and out["context"]["sender_known"] is True and out["verdict"]["confidence"] == 60 and step[0][1] == "scripted-model", str(out)[:200])
    check("every triage job Succeeded", (await q("select count(*) from jobs where job_type='mail_triage' and state <> 'Succeeded'"))[0][0] == 0)
    check("Gmail got the labels: ignored, queue and the Processed-* ones", {"L-0", "L-1", "L-3", "L-4"} <= {x for _, ids in fake.labelled for x in ids}, str(sorted({x for _, ids in fake.labelled for x in ids})))

    # ---- the queue API -----------------------------------------------------------------------------------
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa One", "fa.one@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            assert (await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.one@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}

        r = await hc.get("/mail/triage", headers=FTM)
        check("an FTM cannot see the triage queue -> 403", r.status_code == 403, r.text[:100])
        r = await hc.get("/mail/triage", headers=FA)
        queue_items = r.json()
        subjects = sorted(i["subject"] or "" for i in queue_items)
        check("the FA sees exactly the 3 mails waiting for a person", r.status_code == 200 and subjects == ["body only po", "po and advice", "unsure"], str(subjects))
        item = next(i for i in queue_items if i["subject"] == "po and advice")
        check("each item says what was suggested, how sure, why, the Gmail link and a text preview",
              item["suggested_class"] == "purchase_order" and item["confidence"] == 97 and "one mail" in item["reason"]
              and item["mail_link"].startswith("https://mail.google.com/") and item["attachments"] == 1 and "payment advice" in item["body_preview"], str(item)[:260])
        d = (await hc.get(f"/mail/triage/{item['email_id']}", headers=FA)).json()
        check("the detail has the full text, each attachment with what was done and why, and the history",
              "payment advice" in d["body"] and d["attachment_list"][0]["file_name"] == "advice.pdf" and d["attachment_list"][0]["verdict"] == "qualify"
              and any(t["to"] == "HumanTriage" for t in d["timeline"]), str(d)[:260])
        ign = (await hc.get("/mail/triage", headers=FA, params={"list": "ignored"})).json()
        check("the ignored list holds the 3 general mails", len(ign) == 3 and sorted(i["subject"] or "" for i in ign) == ["", "question", "spam"], str([i["subject"] for i in ign]))

        # a person routes
        unsure_id = next(i["email_id"] for i in queue_items if i["subject"] == "unsure")
        r = await hc.post(f"/mail/triage/{unsure_id}/classify", headers=FA, json={"mail_class": "dispute", "note": "reads as a complaint"})
        check("a person routes a queued mail: 200, handed to its pipeline", r.status_code == 200 and r.json()["status"] == "Processed" and r.json()["mail_class"] == "dispute" and "job" in r.json()["message"], r.text[:200])
        r1 = await row("unsure")
        check("the registry row: dispute, 100 (a person), Processed; it left the queue", r1[:3] == ("Processed", "dispute", 100) and len((await hc.get("/mail/triage", headers=FA)).json()) == 2, str(r1))
        tl = (await hc.get(f"/mail/triage/{unsure_id}", headers=FA)).json()["timeline"]
        check("the history says who routed it and why, then what was handed off", any("reads as a complaint" in (t["reason"] or "") for t in tl) and "job" in (tl[-1]["reason"] or ""), str(tl[-2:]))
        r = await hc.post(f"/mail/triage/{unsure_id}/classify", headers=FA, json={"mail_class": "payment"})
        check("routing it again -> 409 (it is no longer waiting for a person)", r.status_code == 409, r.text[:120])
        auto_id = (await q("select id from ingested_emails where subject='auto payment'"))[0][0]
        r = await hc.post(f"/mail/triage/{auto_id}/classify", headers=FA, json={"mail_class": "dispute"})
        check("an already handed-off mail cannot be re-routed here -> 409", r.status_code == 409, r.text[:120])
        r = await hc.post(f"/mail/triage/{item['email_id']}/classify", headers=FA, json={"mail_class": "general"})
        check("a person can say a queued mail is general: Processed, AR/Ignored", r.status_code == 200 and (await row("po and advice"))[0] == "Processed" and (await row("po and advice"))[3] == "AR/Ignored", r.text[:160])
        q_id = (await q("select id from ingested_emails where subject='question'"))[0][0]
        r = await hc.post(f"/mail/triage/{q_id}/classify", headers=FA, json={"mail_class": "dispute"})
        check("a mail wrongly ignored can be pulled back out and routed", r.status_code == 200 and (await row("question"))[:2] == ("Processed", "dispute"), r.text[:160])
        r = await hc.post(f"/mail/triage/{q_id}/classify", headers=FTM, json={"mail_class": "payment"})
        check("an FTM cannot route -> 403", r.status_code == 403)
        r = await hc.post(f"/mail/triage/{uuid_zero()}/classify", headers=FA, json={"mail_class": "payment"})
        check("an unknown mail -> 404", r.status_code == 404)
        r = await hc.post(f"/mail/triage/{unsure_id}/classify", headers=FA, json={"mail_class": "nonsense"})
        check("an unknown class -> 422", r.status_code == 422)

        # two people at once
        body_only = next(i["email_id"] for i in queue_items if i["subject"] == "body only po")
        a, b = await asyncio.gather(
            hc.post(f"/mail/triage/{body_only}/classify", headers=FA, json={"mail_class": "purchase_order"}),
            hc.post(f"/mail/triage/{body_only}/classify", headers=ADM, json={"mail_class": "general"}),
        )
        check("two people routing the same mail at once: exactly one 200 and one 409", sorted([a.status_code, b.status_code]) == [200, 409], f"{a.status_code} {b.status_code}")

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


def uuid_zero():
    return "00000000-0000-0000-0000-000000000000"


asyncio.run(main())
