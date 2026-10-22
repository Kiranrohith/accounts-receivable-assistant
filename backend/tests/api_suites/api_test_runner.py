import asyncio
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from docx import Document
from sqlalchemy import text

import core.services.documents.intake as intake
import core.services.invoices.processing as proc
import core.services.jobs.queue as queue
import testsupport
from api.rest.app import app
from config.settings import get_settings
from core.services.documents.classification import ClassificationOutcome, ExtractionError
from core.services.invoices.extraction import ExtractionOutcome
from data.clients.db import SessionLocal, engine
from data.models.postgres.enums import JobState
from schemas.documents.classification import DocClassification, DocumentClass
from schemas.invoices.extraction import ExtractedInvoice, FieldConfidences, LineItem

s = get_settings()
assert "ar_rn" in s.database_url, "refusing to run against a non-test database"
results = []
D = Decimal


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


# ---- controllable fake model: behaviour per document key
calls: dict[str, int] = {}  # classifier calls per key
behaviour: dict[str, str] = {}  # key -> "flaky2" | "dead" | "slow"
SLEEP = {"slow": 3.5, "k_fence": 3.0, "l_stop": 4.0}


def key_of(markdown: str) -> str:
    return markdown.split("CASE:")[1].split()[0].replace("\\", "")  # the parser escapes _ as \_


async def classify(markdown):
    key = key_of(markdown)
    calls[key] = calls.get(key, 0) + 1
    mode = behaviour.get(key, "")
    if mode == "dead" or (mode == "flaky2" and calls[key] <= 2):
        raise ExtractionError("model unavailable")
    if key in SLEEP:
        await asyncio.sleep(SLEEP[key])
    return ClassificationOutcome(DocClassification(doc_class=DocumentClass.INVOICE, confidence=95, reason="test"), "fake", 1)


async def extract_invoice(markdown):
    key = key_of(markdown)
    inv = ExtractedInvoice(
        invoice_number=f"INV-{key}", customer_name="Zenith Softlabs Pvt Ltd", customer_gstin="33AABCZ1234F1Z5",
        invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="INR", total_amount=D("1000"),
        line_items=[LineItem(description="Consulting", quantity=D("1"))],
        confidence=FieldConfidences(**{f: 95 for f in FieldConfidences.model_fields}),
    )
    return ExtractionOutcome(inv, "fake", 1)


intake.classify_document = classify
proc.extract_invoice = extract_invoice


async def main():
    tmp = Path(tempfile.mkdtemp())

    def make(key):
        d = Document()
        d.add_paragraph(f"CASE:{key}")
        d.save(tmp / f"{key}.docx")

    keys = [f"a{i}" for i in range(12)] + ["flaky", "dead", "slow", "g_stale", "g_alive", "h_long", "i_spent", "k_fence", "l_stop"]
    for k in keys:
        make(k)
    (tmp / "corrupt.pdf").write_bytes(b"%PDF-1.4\nthis is not really a pdf\n")
    behaviour.update({"flaky": "flaky2", "dead": "dead"})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        await c.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        cust = lambda **kw: {"contact": "Contact", "country": "India", "billing_currency": "INR", **kw}
        assert (await c.post("/customers", headers=ADM, json=cust(customer_name="Zenith Softlabs Pvt Ltd", email="ap@zenith.com", gstin="33AABCZ1234F1Z5", deducts_tds=True))).status_code == 201

        async def q(sql, **p):
            async with SessionLocal() as db:
                return (await db.execute(text(sql), p)).all()

        async def one(sql, **p):
            rows = await q(sql, **p)
            return rows[0][0] if rows else None

        ids: dict[str, str] = {}

        async def up(name):
            fn = f"{name}.docx" if (tmp / f"{name}.docx").exists() else name
            r = await c.post("/documents/upload", headers=ADM, files={"file": (fn, (tmp / fn).read_bytes())})
            assert r.status_code == 202, r.text
            ids[name] = r.json()["source_document_id"]
            return ids[name]

        async def jobs_of(doc):
            return await q("select state::text, attempt_count, job_type, locked_by, rerun_of is not null from jobs where entity_id=:i order by created_at", i=doc)

        async def wait_state(doc, states, n=1, timeout=120):
            for _ in range(int(timeout * 5)):
                js = await jobs_of(doc)
                if len(js) >= n and js[-1][0] in states:
                    return js
                await asyncio.sleep(0.2)
            return await jobs_of(doc)

        async def status(doc):
            return (await c.get(f"/documents/uploads/{doc}", headers=ADM)).json()

        # ===================================================== A. enqueue: no worker running yet
        for i in range(12):
            await up(f"a{i}")
        js = await jobs_of(ids["a0"])
        check("upload queues a job in the same transaction (no worker yet): Queued, attempt 0", js == [("Queued", 0, "document_intake", None, False)], str(js))
        st = await status(ids["a0"])
        check("status while queued: Processing", st["status"] == "Processing" and st["processing_status"] == "Running", str(st["status"]))
        check("12 documents, 12 queued jobs, nothing processed", (await one("select count(*) from jobs where state='Queued'")) == 12 and (await one("select count(*) from invoices")) == 0)

        # ===================================================== B. claim exclusivity
        queue.retry_delays_seconds = (0.2, 0.2, 0.2)
        queue.heartbeat_seconds = 1.0
        queue.stale_heartbeat_seconds = 30
        worker = await testsupport.start_worker(concurrency=4)
        for i in range(12):
            await wait_state(ids[f"a{i}"], ("Succeeded", "Failed", "DeadLettered"))
        check("12 jobs, 4 slots: all Succeeded after exactly one attempt each", (await one("select count(*) from jobs where state='Succeeded' and attempt_count=1")) == 12)
        check("each document was parsed exactly once (no job taken twice)", (await one("select count(*) from (select entity_id from step_log where step_name='parse' group by entity_id having count(*) > 1) t")) == 0 and (await one("select count(*) from step_log where step_name='parse'")) == 12)
        check("12 invoices, none duplicated", (await one("select count(*) from invoices")) == 12 and (await one("select count(distinct invoice_number) from invoices")) == 12)
        slots = await q("select distinct locked_by from jobs")
        check("work was spread over more than one slot", len(slots) >= 2, str(len(slots)))
        check("every slot id belongs to the one worker process", all(r[0].startswith(worker.worker_id) for r in slots))

        # ===================================================== C. retry with backoff, then success
        queue.retry_delays_seconds = (1.5, 1.5, 1.5)
        did = await up("flaky")
        seen = None
        for _ in range(60):
            st = await status(did)
            if st["retry_at"]:
                seen = st
                break
            await asyncio.sleep(0.1)
        check("while a retry is pending: still Processing, the error and retry_at are shown", seen and seen["status"] == "Processing" and "model unavailable" in (seen["error"] or "") and seen["retry_at"], str(seen and (seen["status"], seen["error"])))
        js = await wait_state(did, ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("flaky model: fails twice, third attempt succeeds -> Succeeded, attempt_count 3", js == [("Succeeded", 3, "document_intake", js[0][3], False)], str(js))
        logs = await q("select reason from state_log where entity_id=:i and reason like 'Retrying%' order by occurred_at", i=did)
        check("both failed attempts are in the state log with the reason", len(logs) == 2 and "attempt 1 of 4" in logs[0][0] and "attempt 2 of 4" in logs[1][0], str([r[0][:50] for r in logs]))
        check("the invoice was created exactly once", (await one("select count(*) from invoices where invoice_number='INV-flaky'")) == 1)
        queue.retry_delays_seconds = (0.2, 0.2, 0.2)

        # ===================================================== D. dead letter, then a person re-runs it
        did = await up("dead")
        js = await wait_state(did, ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("model never recovers: 4 attempts, then DeadLettered", js == [("DeadLettered", 4, "document_intake", js[0][3], False)], str(js))
        st = await status(did)
        check("status Failed with the error; no business rows", st["status"] == "Failed" and "model unavailable" in st["error"] and (await one("select count(*) from invoices where invoice_number='INV-dead'")) == 0, str(st["status"]))
        check("state log says it gave up", (await one("select count(*) from state_log where entity_id=:i and reason like 'Gave up after 4 attempts%'", i=did)) == 1)
        r = await c.post(f"/documents/uploads/{ids['a0']}/rerun", headers=ADM)
        check("re-run of an upload that succeeded -> 409", r.status_code == 409, r.text[:100])
        check("re-run of an unknown upload -> 404; no token -> 401", (await c.post("/documents/uploads/00000000-0000-0000-0000-000000000009/rerun", headers=ADM)).status_code == 404 and (await c.post(f"/documents/uploads/{did}/rerun")).status_code == 401)
        behaviour["dead"] = ""  # the model is back
        rs = await asyncio.gather(
            c.post(f"/documents/uploads/{did}/rerun", headers=FA),
            c.post(f"/documents/uploads/{did}/rerun", headers=ADM),
        )
        check("two simultaneous re-runs: one 202, one 409", sorted(x.status_code for x in rs) == [202, 409], str([x.status_code for x in rs]))
        js = await wait_state(did, ("Succeeded", "Failed", "DeadLettered"), n=2, timeout=60)
        check("re-run is a new job linked to the failed one, and it succeeds", len(js) == 2 and js[1][0] == "Succeeded" and js[1][4] is True and js[1][1] == 1, str(js))
        check("invoice created once after the re-run", (await one("select count(*) from invoices where invoice_number='INV-dead'")) == 1)

        # ===================================================== E. a non-retryable failure is not retried
        cid = await up("corrupt.pdf")
        js = await wait_state(cid, ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("corrupt file: Failed after ONE attempt (no retry)", js and js[0][0] == "Failed" and js[0][1] == 1 and len(js) == 1, str(js))
        check("status Failed", (await status(cid))["status"] == "Failed")

        # ===================================================== F. heartbeat
        did = await up("slow")
        beats = set()
        for _ in range(60):
            row = await q("select state::text, heartbeat_at from jobs where entity_id=:i", i=did)
            if row and row[0][0] == "Running" and row[0][1]:
                beats.add(row[0][1])
            if row and row[0][0] == "Succeeded":
                break
            await asyncio.sleep(0.25)
        check("a long job's heartbeat keeps advancing while it runs (1s interval)", len(beats) >= 3, f"{len(beats)} distinct heartbeats")
        check("slow job still Succeeded", (await wait_state(did, ("Succeeded",)))[0][0] == "Succeeded")

        # ===================================================== G-J. recovery after a dead worker (watchdog at boot)
        await worker.stop(grace_seconds=2)
        for k in ("g_stale", "g_alive", "h_long", "i_spent"):
            await up(k)
        now = datetime.now(UTC)

        async def set_running(doc, *, worker_id, started, beat, attempts):
            async with SessionLocal() as db:
                await db.execute(text("update jobs set state='Running', locked_by=:w, started_at=:s, heartbeat_at=:b, attempt_count=:a where entity_id=:i"),
                                 {"w": worker_id, "s": started, "b": beat, "a": attempts, "i": doc})
                await db.commit()

        await set_running(ids["g_stale"], worker_id="dead-worker", started=now - timedelta(seconds=200), beat=now - timedelta(seconds=200), attempts=1)
        await set_running(ids["g_alive"], worker_id="other-worker", started=now, beat=now, attempts=1)
        await set_running(ids["h_long"], worker_id="other-worker", started=now - timedelta(minutes=20), beat=now, attempts=1)
        await set_running(ids["i_spent"], worker_id="dead-worker", started=now - timedelta(seconds=200), beat=now - timedelta(seconds=200), attempts=4)
        # J: a document that already has its outcome, whose earlier attempt "died" before finishing the job
        async with SessionLocal() as db:
            job = await queue.enqueue(db, job_type=queue.JOB_INTAKE, entity_id=__import__("uuid").UUID(ids["a1"]), created_by=None)
            job.queued_at = now - timedelta(minutes=5)  # queued before the invoice was written
            await db.commit()
        # the boot-time watchdog pass runs when the worker starts
        before_calls, before_parse = dict(calls), await one("select count(*) from step_log where step_name='parse' and entity_id=:i", i=ids["a1"])
        # that fake "job J" must look like an interrupted running attempt:
        async with SessionLocal() as db:
            await db.execute(text("update jobs set state='Running', locked_by='dead-worker', started_at=:s, heartbeat_at=:s, attempt_count=1 where entity_id=:i and state='Queued'"),
                             {"s": now - timedelta(seconds=200), "i": ids["a1"]})
            await db.commit()
        worker = await testsupport.start_worker(concurrency=2)
        js_g = await wait_state(ids["g_stale"], ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("G: a job whose worker stopped heartbeating is reclaimed at boot and finished on attempt 2", js_g[0][0] == "Succeeded" and js_g[0][1] == 2, str(js_g))
        check("G: the reclaim is in the state log", (await one("select count(*) from state_log where entity_id=:i and reason like 'Reclaimed%'", i=ids["g_stale"])) == 1)
        check("G: its invoice exists once", (await one("select count(*) from invoices where invoice_number='INV-g_stale'")) == 1, str(await q("select step_name, result::text, left(error_message,80) from step_log where entity_id=:i order by occurred_at", i=ids["g_stale"])))
        js = await jobs_of(ids["g_alive"])
        check("G: a Running job with a fresh heartbeat is left alone", js[0][0] == "Running" and js[0][3] == "other-worker", str(js))
        async with SessionLocal() as db:  # it belongs to a made-up worker: close it so it cannot be reclaimed later
            await db.execute(text("update jobs set state='Succeeded' where entity_id=:i"), {"i": ids["g_alive"]})
            await db.commit()
        js = await jobs_of(ids["h_long"])
        err = await one("select last_error_message from jobs where entity_id=:i", i=ids["h_long"])
        check("H: a job past the 15-minute cap is Failed, not retried", js[0][0] == "Failed" and "longer than" in (err or ""), f"{js} {err}")
        js = await jobs_of(ids["i_spent"])
        check("I: a stale job with all attempts used is DeadLettered", js[0][0] == "DeadLettered", str(js))
        js = await wait_state(ids["a1"], ("Succeeded", "Failed", "DeadLettered"), n=2, timeout=30)
        await asyncio.sleep(1)
        check("J: an interrupted job whose outcome was already written is closed without redoing it", js and js[-1][0] == "Succeeded" and (await one("select count(*) from step_log where step_name='parse' and entity_id=:i", i=ids["a1"])) == before_parse and calls.get("a1", 0) == before_calls.get("a1", 0) and (await one("select count(*) from invoices where invoice_number='INV-a1'")) == 1, f"{js}")

        # ===================================================== K. fencing: the watchdog takes a job from a live worker
        did = await up("k_fence")
        for _ in range(100):  # wait until a slot is running it (inside the slow classifier)
            row = await q("select state::text from jobs where entity_id=:i", i=did)
            if row and row[0][0] == "Running":
                break
            await asyncio.sleep(0.1)
        async with SessionLocal() as db:  # the watchdog (or a person) takes the job away from the live worker
            await db.execute(text("update jobs set state='Queued', locked_by=null, heartbeat_at=null, run_after=now() + interval '1 hour' where entity_id=:i"), {"i": did})
            await db.commit()
        await asyncio.sleep(4.5)  # the first worker finishes its work and tries to record the result
        js = await jobs_of(did)
        check("K: the first worker's late result does not overwrite a job it no longer owns", js[0][0] == "Queued", str(js))
        check("K: ...and it wrote NO business rows either (no invoice from the lost attempt)", (await one("select count(*) from invoices where invoice_number='INV-k_fence'")) == 0)

        # ===================================================== L. graceful shutdown puts the running job back
        await worker.stop(grace_seconds=2)
        async with SessionLocal() as db:  # release the fenced job for a later worker
            await db.execute(text("update jobs set run_after=now() + interval '1 hour' where entity_id=:i"), {"i": did})
            await db.commit()
        lid = await up("l_stop")
        w2 = await testsupport.start_worker(concurrency=1)
        for _ in range(100):
            row = await q("select state::text, locked_by from jobs where entity_id=:i", i=lid)
            if row and row[0][0] == "Running":
                break
            await asyncio.sleep(0.1)
        await w2.stop(grace_seconds=0.3)
        js = await jobs_of(lid)
        check("L: stopping a worker mid-job puts the job back, attempt not counted", js[0][0] == "Queued" and js[0][1] == 0, str(js))
        check("L: the shutdown is in the state log", (await one("select count(*) from state_log where entity_id=:i and reason like 'Worker shutting down%'", i=lid)) == 1)
        async with SessionLocal() as db:
            await db.execute(text("update jobs set run_after=now() where entity_id=:i"), {"i": did})
            await db.commit()
        w3 = await testsupport.start_worker(concurrency=2)
        js = await wait_state(lid, ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("L: another worker finishes it; one invoice", js[0][0] == "Succeeded" and (await one("select count(*) from invoices where invoice_number='INV-l_stop'")) == 1, str(js))
        js = await wait_state(did, ("Succeeded", "Failed", "DeadLettered"), timeout=60)
        check("K: when the job is later reclaimed it is closed without a duplicate invoice", js[0][0] == "Succeeded" and (await one("select count(*) from invoices where invoice_number='INV-k_fence'")) == 1, str(js))
        await w3.stop(grace_seconds=2)
        check("no job left Running or Queued except the deliberately untouched one", (await one("select count(*) from jobs where state in ('Queued','Running')")) == 0)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
