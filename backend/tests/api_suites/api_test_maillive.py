"""GET /mail/live: the mail being processed right now. Real database, no model, no mail. Proves: a mail shows from the moment it waits to
be read (with its place in the line), while it is being read, while it waits to try again, and while each attachment is read (with the
step it is at); a mail that ended stays only for the time asked and says how it ended (done, ignored, review with where to decide it,
rejected, failed); a document run again shows only its newest attempt; a file a person uploaded is never listed; and who may look."""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, IngestedEmail, Job, PendingInvoiceChange, PurchaseOrder, SourceDocument, StepLog
from data.models.postgres.enums import (
    DocKind, DocType, EmailClassification, EmailStatus, JobState, PendingChangeReason, StepResult, UploadedVia,
)

s = get_settings()
assert "ar_maillive" in s.database_url, "refusing to run against a non-test database"
NOW = datetime.now(UTC)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


async def seed():
    ids = {}
    async with SessionLocal() as db:
        customer = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email="ap@zenith.test", tax_id_type="GSTIN", tax_id="29ABCDE1234F1Z5",
                            deducts_tds=False, billing_currency="INR", country="India")
        db.add(customer)
        await db.flush()

        def mail(key, subject, *, status=None, cls=None, conf=None):
            row = IngestedEmail(gmail_message_id=f"g-{key}", sender=f"{key}@acme.example.com", subject=subject, received_at=ago(600), status=status,
                                classification=cls, classification_confidence=conf)
            db.add(row)
            return row

        def doc(name, email, kind=DocKind.INVOICE, via=UploadedVia.EMAIL_ATTACHMENT):
            row = SourceDocument(file_name=name, file_path="/x/f.pdf", doc_type=DocType.PDF, file_hash=uuid.uuid4().hex, doc_kind=kind, uploaded_via=via, ingested_email_id=email.id if email else None)
            db.add(row)
            return row

        def job(job_type, entity_type, entity, state, *, created, attempts=0, queued=None, started=None, finished=None, run_after=None):
            row = Job(job_type=job_type, entity_type=entity_type, entity_id=entity.id, state=state, attempt_count=attempts, created_at=created,
                      queued_at=queued or created, run_after=run_after or created, started_at=started, finished_at=finished)
            db.add(row)
            return row

        m = {
            "wait": mail("wait", "Waiting to be read"),
            "read": mail("read", "Being read"),
            "retry": mail("retry", "Waiting to retry"),
            "po_run": mail("po_run", "PO being read", cls=EmailClassification.PURCHASE_ORDER, conf=93),
            "two": mail("two", "Two attachments", cls=EmailClassification.PAYMENT, conf=90),
            "po_done": mail("po_done", "PO just created", status=EmailStatus.PROCESSED, cls=EmailClassification.PURCHASE_ORDER, conf=95),
            "old": mail("old", "Ended long ago", status=EmailStatus.PROCESSED, cls=EmailClassification.PURCHASE_ORDER, conf=95),
            "general": mail("general", "Newsletter", status=EmailStatus.PROCESSED, cls=EmailClassification.GENERAL, conf=90),
            "human": mail("human", "Not sure", status=EmailStatus.HUMAN_TRIAGE, cls=EmailClassification.PAYMENT, conf=60),
            "review": mail("review", "Needs a look", status=EmailStatus.PROCESSED, cls=EmailClassification.PAYMENT, conf=90),
            "failed": mail("failed", "Gave up", status=EmailStatus.PROCESSED, cls=EmailClassification.PAYMENT, conf=90),
            "rerun": mail("rerun", "Run again", status=EmailStatus.PROCESSED, cls=EmailClassification.PAYMENT, conf=90),
        }
        await db.flush()
        d = {
            "po_run": doc("po_6146.pdf", m["po_run"], DocKind.PO),
            "two_a": doc("advice_a.pdf", m["two"], DocKind.PAYMENT),
            "two_b": doc("advice_b.pdf", m["two"], DocKind.PAYMENT),
            "po_done": doc("po_77.pdf", m["po_done"], DocKind.PO),
            "old": doc("po_old.pdf", m["old"], DocKind.PO),
            "review": doc("odd.pdf", m["review"]),
            "failed": doc("broken.pdf", m["failed"]),
            "rerun": doc("again.pdf", m["rerun"], DocKind.PAYMENT),
            "uploaded": doc("by_hand.pdf", None, via=UploadedVia.UPLOAD),
        }
        await db.flush()
        db.add(PurchaseOrder(customer_id=customer.id, po_number="PO-77", po_date=date(2026, 10, 1), source_document_id=d["po_done"].id))
        db.add(PurchaseOrder(customer_id=customer.id, po_number="PO-OLD", po_date=date(2026, 10, 1), source_document_id=d["old"].id))
        db.add(PendingInvoiceChange(source_document_id=d["review"].id, reason=PendingChangeReason.EXTRACTION_REVIEW, proposed_snapshot={"reasons": ["Customer unknown"]}))

        T, D = "mail_triage", "ingested_email"
        S, QU, RU, OK, FA, DL = "source_document", JobState.QUEUED, JobState.RUNNING, JobState.SUCCEEDED, JobState.FAILED, JobState.DEAD_LETTERED
        jobs = {
            "wait": job(T, D, m["wait"], QU, created=ago(50)),
            "read": job(T, D, m["read"], RU, created=ago(40), attempts=1, started=ago(5)),
            "retry": job(T, D, m["retry"], QU, created=ago(300), attempts=1, run_after=NOW + timedelta(seconds=120)),
            "po_run_t": job(T, D, m["po_run"], OK, created=ago(400), finished=ago(300)),
            "po_run_d": job("document_intake", S, d["po_run"], RU, created=ago(299), attempts=1, started=ago(20)),
            "two_t": job(T, D, m["two"], OK, created=ago(400), finished=ago(300)),
            "two_a": job("document_intake", S, d["two_a"], RU, created=ago(299), attempts=1, started=ago(30)),
            "two_b": job("document_intake", S, d["two_b"], OK, created=ago(299), attempts=1, started=ago(40), finished=ago(10)),
            "po_done_t": job(T, D, m["po_done"], OK, created=ago(60), finished=ago(12)),
            "po_done_d": job("document_intake", S, d["po_done"], OK, created=ago(11), attempts=1, started=ago(10), finished=ago(5)),
            "old_t": job(T, D, m["old"], OK, created=ago(500), finished=ago(450)),
            "old_d": job("document_intake", S, d["old"], OK, created=ago(449), attempts=1, started=ago(448), finished=ago(400)),
            "general_t": job(T, D, m["general"], OK, created=ago(30), finished=ago(6)),
            "human_t": job(T, D, m["human"], OK, created=ago(30), finished=ago(7)),
            "review_t": job(T, D, m["review"], OK, created=ago(60), finished=ago(40)),
            "review_d": job("document_intake", S, d["review"], OK, created=ago(39), attempts=1, started=ago(38), finished=ago(8)),
            "failed_t": job(T, D, m["failed"], OK, created=ago(60), finished=ago(40)),
            "failed_d": job("document_intake", S, d["failed"], DL, created=ago(39), attempts=4, started=ago(38), finished=ago(9)),
            "rerun_t": job(T, D, m["rerun"], OK, created=ago(200), finished=ago(190)),
            "rerun_old": job("document_intake", S, d["rerun"], FA, created=ago(189), attempts=1, started=ago(188), finished=ago(60)),
            "rerun_new": job("document_intake", S, d["rerun"], QU, created=ago(20), run_after=ago(20)),
            "uploaded": job("document_intake", S, d["uploaded"], RU, created=ago(10), attempts=1, started=ago(9)),
        }
        await db.flush()
        for key, row in {**m, **d}.items():
            ids[key] = row.id
        for key, row in jobs.items():
            ids["job_" + key] = row.id
        # steps this attempt has finished
        for name, document, job_row, minutes in (("parse", d["po_run"], jobs["po_run_d"], 15), ("classify", d["po_run"], jobs["po_run_d"], 10),
                                                 ("parse", d["two_a"], jobs["two_a"], 25)):
            db.add(StepLog(job_id=job_row.id, entity_type=S, entity_id=document.id, step_name=name, result=StepResult.SUCCESS, occurred_at=ago(minutes)))
        # a step of an EARLIER attempt of the running PO job must not count
        db.add(StepLog(job_id=jobs["po_run_d"].id, entity_type=S, entity_id=d["po_run"].id, step_name="validate", result=StepResult.SUCCESS, occurred_at=ago(200)))
        await db.commit()
    return ids


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.ml2@example.com", "FA"), ("Ftm", "ftm.ml2@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.ml2@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.ml2@example.com', 'pass12345')).json()['access_token']}"}
        check("no token -> 401", (await c.get("/mail/live")).status_code == 401)
        check("an FTM may not look -> 403", (await c.get("/mail/live", headers=FTM)).status_code == 403)
        check("an empty feed answers", (await c.get("/mail/live", headers=FA)).json()["items"] == [])
        ids = await seed()

        r = await c.get("/mail/live", headers=FA)
        feed = r.json()
        by = {i["subject"]: i for i in feed["items"]}
        check("200 with the server's clock", r.status_code == 200 and feed["as_of"])
        check("only mail: the file a person uploaded is not listed", not any(d["upload"]["file_name"] == "by_hand.pdf" for i in feed["items"] for d in i["documents"]) and len(feed["items"]) == 11, str(sorted(by)))
        check("a mail that ended long ago is not listed", "Ended long ago" not in by)

        w = by["Waiting to be read"]
        check("waiting to be read: state, stage and its place in the line (first)", (w["state"], w["stage"], w["ahead"], w["documents"], w["finished_how"]) == ("waiting", "Waiting to be read", 0, [], None), str(w))
        check("a mail carries who sent it and a link to it", w["sender"] == "wait@acme.example.com" and w["mail_link"].endswith("g-wait") and w["email_id"] == str(ids["wait"]))
        r_ = by["Being read"]
        check("being read: running, deciding what the mail is", (r_["state"], r_["stage"], r_["ahead"]) == ("running", "Deciding what the mail is", None), str(r_))
        t = by["Waiting to retry"]
        check("waiting to retry: told apart from waiting its turn", (t["state"], t["stage"], t["ahead"]) == ("retrying", "Waiting to try again", None), str(t))

        p = by["PO being read"]
        check("an attachment being read: the mail is running and says the step, from this attempt only",
              (p["state"], p["stage"], p["mail_class"], p["confidence"]) == ("running", "Reading its details", "purchase_order", 93), str(p)[:300])
        check("and the document says what it is and how far", len(p["documents"]) == 1 and p["documents"][0]["state"] == "running" and p["documents"][0]["upload"]["document_kind"] in ("po", "unknown") and p["documents"][0]["upload"]["file_name"] == "po_6146.pdf", str(p["documents"])[:300])
        two = by["Two attachments"]
        check("two attachments, one done: the mail says how many", two["state"] == "running" and two["stage"] == "Reading 2 attachments (1 done)", str(two["stage"]))
        check("each attachment has its own state", sorted(d["state"] for d in two["documents"]) == ["finished", "running"] and sorted(d["upload"]["file_name"] for d in two["documents"]) == ["advice_a.pdf", "advice_b.pdf"])

        g = by["Newsletter"]
        check("general mail set aside: ended as ignored", (g["state"], g["finished_how"], g["stage"], g["review_queue"]) == ("finished", "ignored", "General mail, set aside", None), str(g))
        h = by["Not sure"]
        check("a mail a person must route: review, with the queue and the mail to open", (h["finished_how"], h["review_queue"], h["review_id"]) == ("review", "mail_triage", str(ids["human"])), str(h))
        done = by["PO just created"]
        check("a PO just created: ended as done, with the purchase order the document became", (done["state"], done["finished_how"], done["stage"]) == ("finished", "done", "Processed") and done["documents"][0]["upload"]["purchase_order_id"] and done["finished_at"], str(done)[:300])
        rv = by["Needs a look"]
        check("an attachment sent to review: ended as review, and the document carries its review", rv["finished_how"] == "review" and rv["documents"][0]["finished_how"] == "review" and rv["documents"][0]["upload"]["review_id"] and rv["documents"][0]["upload"]["review_kind"] == "extraction", str(rv)[:300])
        fl = by["Gave up"]
        check("an attachment that gave up: ended as failed", (fl["finished_how"], fl["stage"]) == ("failed", "Gave up") and fl["documents"][0]["finished_how"] == "failed", str(fl)[:300])
        rr = by["Run again"]
        check("a document run again: only the new attempt, waiting its turn, never the old failure", (rr["state"], rr["stage"]) == ("waiting", "Its attachment is waiting for its turn") and len(rr["documents"]) == 1 and rr["documents"][0]["state"] == "waiting", str(rr)[:300])
        check("and its place in the line is counted behind the older waiting mail", rr["ahead"] is not None and rr["ahead"] > w["ahead"], f"{rr['ahead']} {w['ahead']}")

        states = [i["state"] for i in feed["items"]]
        first_finished = states.index("finished")
        check("what is still going on is listed before what ended", "finished" not in states[:first_finished] and all(x == "finished" for x in states[first_finished:]), str(states))

        # ---- the time a finished mail stays --------------------------------------------------------------------
        r0 = await c.get("/mail/live", headers=FA, params={"recent_seconds": 0})
        subjects0 = {i["subject"] for i in r0.json()["items"]}
        check("recent_seconds=0: only what is still going on", all(i["state"] != "finished" for i in r0.json()["items"]) and "PO just created" not in subjects0 and "Waiting to be read" in subjects0, str(sorted(subjects0)))
        r_long = await c.get("/mail/live", headers=FA, params={"recent_seconds": 300})
        check("a longer window keeps what ended minutes ago (not the one older than that)", "Ended long ago" not in {i["subject"] for i in r_long.json()["items"]} and "PO being read" in {i["subject"] for i in r_long.json()["items"]})
        check("a negative or huge window -> 422", (await c.get("/mail/live", headers=FA, params={"recent_seconds": -1})).status_code == 422 and (await c.get("/mail/live", headers=FA, params={"recent_seconds": 301})).status_code == 422)

        # ---- it moves on: a worker finishes the read mail -------------------------------------------------------
        async with SessionLocal() as db:
            row = await db.get(Job, ids["job_read"])
            row.state, row.finished_at = JobState.SUCCEEDED, datetime.now(UTC)
            mail_row = await db.get(IngestedEmail, ids["read"])
            mail_row.status, mail_row.classification = EmailStatus.PROCESSED, EmailClassification.GENERAL
            await db.commit()
        after = {i["subject"]: i for i in (await c.get("/mail/live", headers=FA)).json()["items"]}
        check("when the worker finishes it, the mail turns from running to ended", after["Being read"]["state"] == "finished" and after["Being read"]["finished_how"] == "ignored", str(after["Being read"])[:200])

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
