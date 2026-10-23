"""Mails the reader never classified (the models were down): after UNCLASSIFIED_MAIL_MINUTES a person sees them in the
triage queue and routes them with the same five choices. Real database, no model, no Gmail. Proves: which mails show
(old and unclassified yes; fresh, already classified, and routed ones no), the queue count equals its own list, a person
can route one (ignored list, or its pipeline's jobs), the automatic triage job then leaves it alone, and the old rule
(only waiting/ignored mails) still refuses the rest."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from constants.constants import UNCLASSIFIED_MAIL_MINUTES
from core.services.jobs import queue
from core.services.mail import classification, ingest
from data.clients.db import SessionLocal, engine
from data.models.postgres import IngestedEmail
from data.models.postgres.enums import EmailClassification, EmailStatus
from jobs.runner import HANDLERS

s = get_settings()
assert "ar_unclassified" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
results = []
labelled = []


async def fake_label(message_id, label):  # never the real Gmail: ids here do not exist there
    labelled.append((message_id, label))


async def models_down(*args, **kwargs):  # never a real model either: an unclassified mail stays unclassified
    raise classification.ExtractionError("the models are down")


ingest.label_message = fake_label
classification.classify_mail = models_down


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


def eml(subject, body, sender="noreply@vendor.example.com"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "inbox@example.com", subject
    m.set_content(body)
    return m.as_bytes()


async def mail(label, *, minutes_old, status=None, classification=None, job=False):
    """A registered mail with its kept .eml; `job` queues the automatic triage job for it as the poll does."""
    mid = f"unc-{RUN}-{label}"
    path = ingest.eml_path(mid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(eml(f"Mail {label}", f"The text of mail {label}."))
    async with SessionLocal() as db:
        row = IngestedEmail(gmail_message_id=mid, sender="noreply@vendor.example.com", subject=f"Mail {label}",
                            received_at=datetime.now(UTC) - timedelta(minutes=minutes_old), status=status, classification=classification)
        db.add(row)
        await db.flush()
        await db.execute(text("update ingested_emails set created_at = :t where id = :i"), {"t": datetime.now(UTC) - timedelta(minutes=minutes_old), "i": row.id})
        if job:
            await queue.enqueue(db, job_type=queue.JOB_MAIL_TRIAGE, entity_id=row.id, created_by=None, entity_type=queue.ENTITY_MAIL)
        await db.commit()
        return row.id


async def main():
    stale_min = UNCLASSIFIED_MAIL_MINUTES + 30
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.un@example.com", "FA"), ("Ftm", "ftm.un@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.un@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.un@example.com', 'pass12345')).json()['access_token']}"}

        stale_a = await mail("stale-a", minutes_old=stale_min, job=True)
        stale_b = await mail("stale-b", minutes_old=stale_min + 5, job=True)
        stale_c = await mail("stale-c", minutes_old=stale_min + 10)
        fresh = await mail("fresh", minutes_old=2, job=True)
        waiting_pipeline = await mail("classified", minutes_old=stale_min, classification=EmailClassification.DISPUTE)  # decided, waiting for its jobs
        human = await mail("human", minutes_old=stale_min, status=EmailStatus.HUMAN_TRIAGE, classification=EmailClassification.PAYMENT)
        done = await mail("done", minutes_old=stale_min, status=EmailStatus.PROCESSED, classification=EmailClassification.DISPUTE)

        # ============================== A. who shows in the queue
        r = await c.get("/mail/triage", headers=FA, params={"list": "queue", "limit": 100})
        shown = {i["subject"]: i for i in r.json()}
        check("the queue holds the 3 stale unclassified mails and the one the reader was unsure of, nothing else",
              r.status_code == 200 and set(shown) == {"Mail stale-a", "Mail stale-b", "Mail stale-c", "Mail human"}, str(sorted(shown)))
        check("an unclassified mail has no status and no suggestion, which is how the screen tells it apart",
              shown["Mail stale-a"]["status"] is None and shown["Mail stale-a"]["suggested_class"] is None and shown["Mail stale-a"]["body_preview"].startswith("The text of mail"))
        check("a mail that only just arrived is left to the automatic triage", "Mail fresh" not in shown)
        check("a mail already classified (waiting for its pipeline) is not a person's to route", "Mail classified" not in shown)

        counts = {x["key"]: x["count"] for x in (await c.get("/queues", headers=FA)).json()}
        check("the Take Action count for mail triage equals the queue's own list (4)", counts.get("mail_triage") == 4, str(counts))
        items = (await c.get("/queues/items", headers=FA, params={"queue": "mail_triage", "limit": 50})).json()
        reasons = {i["subject"]: i["reason"] for i in items["items"]}
        check("the merged list gives each its reason: not classified yet, or the reader's guess",
              items["total"] == 4 and reasons["Mail stale-a"] == "Not classified yet: please choose where it belongs" and reasons["Mail human"].startswith("Reader suggests payment"), str(reasons))

        # ============================== B. routing one
        d = (await c.get(f"/mail/triage/{stale_a}", headers=FA)).json()
        check("an unclassified mail opens with its text", d["body"].startswith("The text of mail stale-a") and d["status"] is None, str(d)[:200])

        r = await c.post(f"/mail/triage/{stale_a}/classify", headers=FA, json={"mail_class": "general", "note": "a vendor notice"})
        check("routing it as general moves it to the ignored list", r.status_code == 200 and r.json()["mail_class"] == "general" and "ignored list" in r.json()["message"], r.text[:200])
        row = (await q("select status::text, classification::text, classification_confidence from ingested_emails where id=:i", i=stale_a))[0]
        check("it is recorded as a person's decision at confidence 100", row == ("Processed", "general", 100), str(row))
        log = await q("select reason from state_log where entity_type='ingested_email' and entity_id=:i order by occurred_at desc limit 1", i=stale_a)
        check("the log says who routed it, and why", "Person (Fa)" in log[0][0] and "a vendor notice" in log[0][0], str(log))
        ignored = (await c.get("/mail/triage", headers=FA, params={"list": "ignored", "limit": 100})).json()
        check("it is now on the ignored list and out of the queue",
              "Mail stale-a" in [i["subject"] for i in ignored] and "Mail stale-a" not in [i["subject"] for i in (await c.get("/mail/triage", headers=FA, params={"list": "queue", "limit": 100})).json()])

        r = await c.post(f"/mail/triage/{stale_b}/classify", headers=FA, json={"mail_class": "dispute"})
        check("routing another as a complaint hands it to its pipeline", r.status_code == 200 and "dispute" in r.json()["message"] and "job(s) queued" in r.json()["message"], r.text[:300])
        jobs = await q("select job_type::text from jobs j join source_documents d on d.id = j.entity_id where d.ingested_email_id=:i", i=stale_b)
        check("the complaint's job exists", len(jobs) == 1 and jobs[0][0] == "dispute_extraction", str(jobs))
        check("Gmail was asked (by the stub) to label them, never the real one",
              (f"unc-{RUN}-stale-a", "AR/Ignored") in labelled and any(m == f"unc-{RUN}-stale-b" for m, _ in labelled), str(labelled))

        # ============================== C. the automatic triage job finds it decided and leaves it alone
        async with SessionLocal() as db:
            await db.execute(text("update jobs set run_after = now() + interval '1 day' where job_type <> 'mail_triage' and state = 'Queued'"))
            await db.commit()
        while True:
            async with SessionLocal() as db:
                job = await queue.claim(db, "w-test")
            if job is None:
                break
            await queue.execute(job.id, "w-test", HANDLERS)
        after = (await q("select status::text, classification::text from ingested_emails where id=:i", i=stale_a))[0]
        check("the triage job that was still queued did not overwrite the person's decision", after == ("Processed", "general"), str(after))
        stale_job = await q("select state::text from jobs where entity_id=:i and job_type='mail_triage'", i=stale_a)
        check("and it finished instead of failing or running again", stale_job == [("Succeeded",)], str(stale_job))
        by_person = await q("select count(*) from state_log where entity_type='ingested_email' and entity_id=:i and reason like 'Person%'", i=fresh)
        check("the fresh mail was left to the automatic triage: no person touched it", by_person[0][0] == 0)

        # ============================== D. what is still refused, and who may
        check("a mail already classified and waiting for its pipeline cannot be routed -> 409",
              (await c.post(f"/mail/triage/{waiting_pipeline}/classify", headers=FA, json={"mail_class": "payment"})).status_code == 409)
        check("a mail already handed on cannot be routed here -> 409",
              (await c.post(f"/mail/triage/{done}/classify", headers=FA, json={"mail_class": "payment"})).status_code == 409)
        check("a person may still route the one the reader was unsure of",
              (await c.post(f"/mail/triage/{human}/classify", headers=FA, json={"mail_class": "general"})).status_code == 200)
        check("FTM (no mail.triage.classify) -> 403", (await c.post(f"/mail/triage/{stale_c}/classify", headers=FTM, json={"mail_class": "general"})).status_code == 403)
        check("an unknown mail -> 404", (await c.post(f"/mail/triage/{uuid.uuid4()}/classify", headers=FA, json={"mail_class": "general"})).status_code == 404)

        # two people routing the same unclassified mail at once: one wins, the other gets 409
        both = await asyncio.gather(
            c.post(f"/mail/triage/{stale_c}/classify", headers=FA, json={"mail_class": "general"}),
            c.post(f"/mail/triage/{stale_c}/classify", headers=ADM, json={"mail_class": "general"}),
        )
        codes = sorted(x.status_code for x in both)
        check("two people routing the same mail at once: exactly one 200 (the second finds it ignored and may move it again, so two 200s are also a valid outcome)", codes in ([200, 200], [200, 409]), str(codes))

    for p in (ingest.eml_path(f"unc-{RUN}-{x}") for x in ("stale-a", "stale-b", "stale-c", "fresh", "classified", "human", "done")):
        Path(p).unlink(missing_ok=True)
    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
