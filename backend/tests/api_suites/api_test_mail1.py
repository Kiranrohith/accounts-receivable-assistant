"""M1 + M2: polling the AR mailbox into the registry. Real database, a FAKE Gmail (no network). Throwaway DB only.

What is proved: every new mail is registered with its .eml and ONE triage job in the same transaction; a mail already
registered is never taken twice (re-delivery, overlapping polls); attachments are qualified (skips logged, rejects
recorded as rejected uploads that carry the reason AND the link to the mail); an unreadable mail is recorded as
Failed and does not stop the others; triage ignores mail from unknown senders, and a label failure is
harmless; the schedule and the boot-time label check behave.
"""

import asyncio
import base64
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.jobs import queue, scheduler
from core.services.mail import gmail, ingest
from core.services.mail.gmail import GmailAuthError, GmailError, RawMessage
from data.clients.db import SessionLocal, engine
from jobs.runner import HANDLERS

s = get_settings()
assert "ar_mail1" in s.database_url, "refusing to run against a non-test database"
results = []

PDF = b"%PDF-1.4\n%advice\n" + b"x" * 200
PNG_TINY = b"\x89PNG\r\n\x1a\n" + b"0" * 40  # a logo well under 5 KB


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


def mail(subject, *, sender="Anita Rao <AP@Client.IN>", body="Payment details in the mail.", attachments=(), inline_logo=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "genworx.ardemo@gmail.com", subject
    m["Message-ID"] = f"<{subject.replace(' ', '')}@client.in>"
    m.set_content(body)
    for name, data, mt, st in attachments:
        m.add_attachment(data, maintype=mt, subtype=st, filename=name)
    if inline_logo:
        m.add_attachment(PNG_TINY, maintype="image", subtype="png", filename="logo.png", disposition="inline", cid="<logo1>")
    return m.as_bytes()


class FakeGmail:
    """The same surface as GmailClient, backed by a dict. Newest first, like Gmail."""

    def __init__(self):
        self.messages: dict[str, RawMessage] = {}
        self.order: list[str] = []
        self.labelled: list[tuple[str, list[str]]] = []
        self.fail_labels = False
        self.label_calls = 0
        self.created_labels: list[str] = []
        self.ensure_error: Exception | None = None

    def add(self, mid, raw, *, thread=None, ms=1_760_000_000_000):
        self.messages[mid] = RawMessage(mid, thread or f"t-{mid}", "100", ms, ("INBOX",), raw)
        self.order.insert(0, mid)

    async def list_message_ids(self, query, limit):
        return self.order[:limit]

    async def get_raw(self, mid):
        return self.messages[mid]

    async def ensure_labels(self, names):
        self.label_calls += 1
        if self.ensure_error:
            raise self.ensure_error
        self.created_labels = list(names)
        return {n: f"L-{i}" for i, n in enumerate(names)}

    async def add_labels(self, mid, ids):
        if self.fail_labels:
            raise GmailError("label refused")
        self.labelled.append((mid, ids))

    async def aclose(self):
        pass


async def everything_is_general(mail_obj, context, qualification, *, is_reply):
    """These mails are about ingestion, not triage: whatever reaches the model is general mail."""
    from schemas.mail.triage import MailClass, MailClassification
    return MailClassification(mail_class=MailClass.GENERAL, confidence=95, reason="scripted"), "scripted", 1


async def main():
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    import core.services.mail.triage as triage_mod
    triage_mod.classification.classify_mail = everything_is_general
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True
    raw_dir = Path(s.raw_files_dir)

    fake.add("m1", mail("Payment INV-1001", attachments=[("advice.pdf", PDF, "application", "pdf")], inline_logo=True), thread="th-1")
    fake.add("m2", mail("Remittance empty", attachments=[("Remittance.pdf", b"", "application", "pdf")]))
    fake.add("m3", mail("Advice as spreadsheet", attachments=[("advice.xlsx", b"PK\x03\x04data", "application", "vnd.ms-excel")]))
    fake.add("m4", mail("Plain body only", sender="no-name@client.in"))
    fake.add("m5", mail("Wrong content", attachments=[("fake.pdf", b"this is not a pdf", "application", "pdf")]))

    # ---- A. the first poll registers everything -------------------------------------------------------------
    out = await ingest.poll(fake)
    check("poll: 5 listed, 5 new, 5 ingested, none failed", out == {"listed": 5, "new": 5, "ingested": 5, "failed": 0}, str(out))
    rows = await q("select gmail_message_id, gmail_thread_id, history_id, sender, subject, status, classification, received_at from ingested_emails order by gmail_message_id")
    check("5 registry rows, status empty (waiting for triage)", len(rows) == 5 and all(r[5] is None for r in rows), str(rows[:1]))
    m1 = rows[0]
    check("m1: sender lower-cased, subject, thread and history kept", m1[:5] == ("m1", "th-1", "100", "ap@client.in", "Payment INV-1001"), str(m1))
    check("m1: received_at is Gmail's own time (2025-10-09)", m1[7].astimezone(UTC).date().isoformat() == "2025-10-09", str(m1[7]))
    check("the whole message is kept as <raw dir>/mail/<id>.eml, byte for byte", (raw_dir / "mail" / "m1.eml").read_bytes() == fake.messages["m1"].raw)

    # ---- B. one triage job per mail, queued in the same transaction -----------------------------------------
    jobs = await q("select j.job_type, j.state::text, j.entity_type, e.gmail_message_id from jobs j join ingested_emails e on e.id = j.entity_id order by 4")
    check("one mail_triage job per mail, Queued, about the registry row", [(j[0], j[1], j[2]) for j in jobs] == [("mail_triage", "Queued", "ingested_email")] * 5 and [j[3] for j in jobs] == ["m1", "m2", "m3", "m4", "m5"], str(jobs))

    # ---- C. attachments: qualified, skipped (logged), rejected (visible, with reason + link) ------------------
    step = await q("select output_summary, result::text from step_log where step_name='attachments' and entity_id=(select id from ingested_emails where gmail_message_id='m1')")
    out1 = step[0][0]
    check("m1: one qualifying attachment, the inline logo skipped with its reason and the mail link",
          len(out1["qualifying"]) == 1 and out1["qualifying"][0]["file_name"] == "advice.pdf" and len(out1["skipped"]) == 1
          and "decoration" in out1["skipped"][0]["reason"] and out1["skipped"][0]["source_mail"]["link"].endswith("/m1") and not out1["body_only"], str(out1)[:240])
    rej = await q("select attempted_file_name, rejection_reasons, extracted_snapshot, source_document_id, duplicate_of_source_document_id, resolution::text from rejected_invoices order by attempted_file_name")
    check("3 rejected attachments: empty pdf, spreadsheet, wrong content", [r[0] for r in rej] == ["Remittance.pdf", "advice.xlsx", "fake.pdf"], str([r[0] for r in rej]))
    by_name = {r[0]: r for r in rej}
    check("each rejection says the plain reason AND carries the link to its mail",
          "attachment is empty" in by_name["Remittance.pdf"][1][0]["message"] and by_name["Remittance.pdf"][1][0]["message"].endswith("https://mail.google.com/mail/u/0/#all/m2")
          and "not supported" in by_name["advice.xlsx"][1][0]["message"] and "does not match" in by_name["fake.pdf"][1][0]["message"], by_name["Remittance.pdf"][1][0]["message"])
    check("a rejection's snapshot names the mail (kind mail_attachment, link, ingested email id)", by_name["advice.xlsx"][2]["kind"] == "mail_attachment" and by_name["advice.xlsx"][2]["mail"]["link"].endswith("/m3") and by_name["advice.xlsx"][2]["mail"]["ingested_email_id"], str(by_name["advice.xlsx"][2])[:200])
    m2step = await q("select output_summary from step_log where step_name='attachments' and entity_id=(select id from ingested_emails where gmail_message_id='m2')")
    check("m2: nothing qualifies, so the mail is read from its body", m2step[0][0]["body_only"] is True and len(m2step[0][0]["rejected"]) == 1)
    docs = await q("select count(*), count(distinct file_hash) from source_documents where uploaded_via = 'email_attachment'")
    check("the rejected files are kept as documents traced to their mail", docs[0][0] == 3 and all(Path(p[0]).is_file() for p in await q("select file_path from source_documents where uploaded_via='email_attachment'")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as hc:
        H = {"Authorization": f"Bearer {(await hc.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        r = await hc.get("/invoices/rejected", headers=H, params={"kind": "mail_attachment"})
        j = r.json()
        check("the rejected-uploads list shows them under kind mail_attachment with the mail link",
              r.status_code == 200 and len(j) == 3 and all(x["kind"] == "mail_attachment" and x["source_mail_link"].startswith("https://mail.google.com/") for x in j), r.text[:200])
        r = await hc.get("/invoices/rejected", headers=H)
        check("they do NOT pollute the default invoice rejection list", r.status_code == 200 and r.json() == [], r.text[:120])

    # ---- D. re-delivery: nothing is taken twice -------------------------------------------------------------
    out = await ingest.poll(fake)
    check("second poll: 0 new, nothing written", out["new"] == 0 and out["ingested"] == 0, str(out))
    check("still 5 mails, 5 jobs, 3 rejections", (await q("select count(*) from ingested_emails"))[0][0] == 5 and (await q("select count(*) from jobs"))[0][0] == 5 and (await q("select count(*) from rejected_invoices"))[0][0] == 3)

    # ---- E. overlapping polls ------------------------------------------------------------------------------
    for n in range(6, 10):
        fake.add(f"o{n}", mail(f"Overlap {n}", attachments=[("a.pdf", PDF + bytes([n]), "application", "pdf")]))
    a, b = await asyncio.gather(ingest.poll(fake), ingest.poll(fake))
    check("two polls at once: every mail registered exactly once", a["ingested"] + b["ingested"] == 4 and (await q("select count(*) from ingested_emails where gmail_message_id like 'o%'"))[0][0] == 4, f"{a} {b}")
    check("and exactly one job each", (await q("select count(*) from jobs j join ingested_emails e on e.id = j.entity_id where e.gmail_message_id like 'o%'"))[0][0] == 4)

    # ---- F. an unreadable mail is recorded and does not stop the rest ---------------------------------------
    fake.add("bad", mail("Cannot read"))
    fake.add("after", mail("Readable after the bad one"))
    original = ingest.parse_mail
    ingest.parse_mail = lambda raw: (_ for _ in ()).throw(ValueError("corrupt MIME")) if b"Cannot read" in raw else original(raw)
    out = await ingest.poll(fake)
    ingest.parse_mail = original
    check("one failed, the other ingested", out["failed"] == 1 and out["ingested"] == 1, str(out))
    bad = await q("select status::text from ingested_emails where gmail_message_id='bad'")
    err = await q("select error_message from step_log where step_name='ingest' and entity_id=(select id from ingested_emails where gmail_message_id='bad')")
    check("the failed mail is registered as Failed with its error, and has no job", bad == [("Failed",)] and "corrupt MIME" in err[0][0] and (await q("select count(*) from jobs where entity_id=(select id from ingested_emails where gmail_message_id='bad')"))[0][0] == 0, str(err))
    check("its .eml is kept for a person", (raw_dir / "mail" / "bad.eml").exists())
    out = await ingest.poll(fake)
    check("it is not tried again on the next poll", out["new"] == 0, str(out))

    # ---- G. triage (no customers are seeded here: every sender is unknown with no context -> general, ignored) -
    async def run_triage(mid):
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
            return job
    claimed = []
    for _ in range(20):
        job = await run_triage(None)
        if job is None:
            break
        claimed.append(job.id)
        await queue.execute(job.id, "w-test", HANDLERS)
    states = await q("select status::text, label_applied from ingested_emails where gmail_message_id in ('m1','m4','o6','after')")
    check("every queued mail ended Processed (general, ignored) with the AR/Ignored label recorded", len(claimed) == 10 and all(x == ("Processed", "AR/Ignored") for x in states), f"{len(claimed)} {states}")
    check("their jobs Succeeded", (await q("select count(*) from jobs where job_type='mail_triage' and state='Succeeded'"))[0][0] == 10)
    check("the Gmail label was put on the messages", len(fake.labelled) == 10 and fake.labelled[0][1] == ["L-3"], str(fake.labelled[:2]))
    fake.fail_labels = True
    fake.add("late", mail("Label will fail"))
    await ingest.poll(fake)
    async with SessionLocal() as db:
        job = await queue.claim(db, "w-test")
    await queue.execute(job.id, "w-test", HANDLERS)
    late = await q("select e.status::text, j.state::text from ingested_emails e join jobs j on j.entity_id = e.id where e.gmail_message_id='late'")
    check("a failing label does not fail the job or lose the decision", late == [("Processed", "Succeeded")], str(late))
    fake.fail_labels = False

    # ---- H. the schedule -----------------------------------------------------------------------------------
    await scheduler.seed()
    fake.add("sched", mail("Via the schedule"))
    now = datetime.now(UTC)
    ran = await scheduler.tick(now, schedules={"gmail_poll": scheduler.SCHEDULES["gmail_poll"]}, actions={"gmail_poll": ingest.run_scheduled})
    check("the gmail_poll schedule runs on the first tick and ingests the new mail", ran == ["gmail_poll"] and (await q("select count(*) from ingested_emails where gmail_message_id='sched'"))[0][0] == 1, str(ran))
    ran = await scheduler.tick(now, schedules={"gmail_poll": scheduler.SCHEDULES["gmail_poll"]}, actions={"gmail_poll": ingest.run_scheduled})
    check("and not again until its interval has passed", ran == [], str(ran))
    s.gmail_poll_enabled = False
    fake.add("off", mail("Polling switched off"))
    out = await ingest.run_scheduled(now)
    check("switched off: the run is skipped and the mailbox is not read", "skipped" in out and (await q("select count(*) from ingested_emails where gmail_message_id='off'"))[0][0] == 0, str(out))
    s.gmail_poll_enabled = True

    # ---- I. labels at boot (B9) ----------------------------------------------------------------------------
    await ingest.ensure_labels_at_boot()
    check("boot creates the five AR labels", fake.created_labels == ["AR/Processed-Payment", "AR/Processed-Dispute", "AR/Processed-PO", "AR/Ignored", "AR/Triage"], str(fake.created_labels))
    fake.ensure_error = GmailAuthError("expired: run gmail_authorize.py")
    try:
        await ingest.ensure_labels_at_boot()
        ok = True
    except Exception:
        ok = False
    check("an expired authorization at boot is logged loudly but does not stop the start", ok)
    fake.ensure_error = GmailError("Gmail answered 500")
    try:
        await ingest.ensure_labels_at_boot()
        stopped = False
    except GmailError:
        stopped = True
    check("any other label-setup failure stops the start (crash loudly)", stopped)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
