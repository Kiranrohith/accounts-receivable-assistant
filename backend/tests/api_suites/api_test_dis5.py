"""D5: the scheduler and the dispute deadline scan, with time controlled. Real database and real SMTP
(the local Mailpit); no model is involved."""

import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

import core.services.mail.smtp as smtp
from api.rest.app import app
from config.settings import get_settings
from core.services.disputes import calendar, deadlines
from core.services.jobs import scheduler
from core.services.jobs.worker import Worker
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice

D = Decimal
s = get_settings()
assert "ar_dis5" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


def ist(y, m, d, h, mi):
    """A moment given in IST, as the UTC instant it is."""
    return datetime(y, m, d, h, mi, tzinfo=UTC) - timedelta(hours=5, minutes=30)


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


MON = "2026-10-19T06:00:00Z"  # 11:30 IST, a Monday: first response due Wed 21 Oct, resolution due Mon 2 Nov


async def main():
    clear_mail()
    smtp.retry_delays_seconds = (0.05, 0.05, 0.05)
    async with SessionLocal() as db:
        c1 = Customer(customer_name="D5 Check Ltd", contact="Priya", email="ap@d5check.test", tax_id_type="GSTIN", tax_id="27ABCDE1234F1Z5",
                      deducts_tds=False, billing_currency="INR", country="India")
        c2 = Customer(customer_name="D5 Nomail Ltd", contact="Nobody", email="no-address", tax_id_type="GSTIN", tax_id="29ABCDE1234F1Z5",
                      deducts_tds=False, billing_currency="INR", country="India")
        db.add_all([c1, c2])
        await db.flush()
        for n in range(1, 8):
            db.add(Invoice(customer_id=c1.id, invoice_number=f"D5-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        for n in range(8, 11):
            db.add(Invoice(customer_id=c2.id, invoice_number=f"D5-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()
        INV = {r[0]: str(r[1]) for r in (await db.execute(text("select invoice_number, id from invoices"))).all()}
        CID1 = c1.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        H = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        for name, email, role in [("Fa A", "fa.a@example.com", "FA"), ("Fa B", "fa.b@example.com", "FA"), ("Ftm A", "ftm.a@example.com", "FTM"), ("Ftm B", "ftm.b@example.com", "FTM")]:
            assert (await c.post("/admin/users", headers=H, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
            await asyncio.sleep(0.03)
        tok = {e: {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': f'{e}@example.com', 'password': 'pass12345'})).json()['access_token']}"} for e in ("fa.a", "fa.b", "ftm.a", "ftm.b")}
        uid = {r[0]: str(r[1]) for r in await sql("select email, id from users")}
        ADM_ID = uid[s.seed_admin_email.strip().lower()]
        FTMS = {uid["ftm.a@example.com"], uid["ftm.b@example.com"]}

        async def new(inv, *, received=MON, category="Quality", email=None, key=None):
            body = {"invoice_id": INV[inv], "summary": f"{key or inv} complaint", "received_at": received, "from_email": f"{inv.lower()}@cust.test" if email is None else email}
            if category:
                body["category"] = category
            r = await c.post("/disputes", headers=H, json=body)
            assert r.status_code == 201, r.text
            return r.json()["dispute_id"]

        async def detail(did):
            return (await c.get(f"/disputes/{did}", headers=H)).json()

        async def notices(user_id, type_, dispute_id):
            return await sql("select message from notifications where recipient_user_id=:u and type=:t and entity_id=:d order by created_at", u=user_id, t=type_, d=dispute_id)

        # ---- the disputes -----------------------------------------------------------------------------
        A = await new("D5-1")
        B = await new("D5-2")
        C = await new("D5-3")
        Dd = await new("D5-4")
        E = await new("D5-5", category=None)
        F = await new("D5-6")
        dA = await detail(A)
        check("the deadlines are what the calendar says (Mon + 2 working days)", dA["first_response_due"] == "2026-10-21" and dA["resolution_due"] == calendar.resolution_due(datetime(2026, 10, 19, 6, 0, tzinfo=UTC)).isoformat() == "2026-11-02", f"{dA['first_response_due']} {dA['resolution_due']}")
        owner_of = {k: (await detail(k))["assigned_to"] for k in (A, B, C, Dd, F)}

        # ============================================ scan 1: Tuesday, the day before the first-response deadline
        r1 = await deadlines.scan(ist(2026, 10, 20, 9, 15))
        warned1 = {(w["dispute_id"], w["deadline"]) for w in r1.warned}
        check("scan 1: nothing is missed yet, so nothing is escalated", r1.escalated == [] and r1.looked_at == 5, f"{r1.looked_at} {r1.escalated}")
        check("scan 1: the owners of the five working disputes get a first-response warning (the classify-queue one does not)",
              warned1 == {(k, "first response") for k in (A, B, C, Dd, F)}, str(warned1))
        got = [await notices(owner_of[k], "DeadlineWarning", k) for k in (A, B)]
        check("scan 1: the warning goes to the owner and names the date", all(any("2026-10-21" in m[0] and "(today)" not in m[0] for m in rows) for rows in got), str(got))
        r1b = await deadlines.scan(ist(2026, 10, 20, 9, 15))
        check("scan 1 again: idempotent, no second warning", r1b.warned == [] and r1b.escalated == [], str(r1b.as_dict()))
        r1c = await deadlines.scan(ist(2026, 10, 21, 9, 15))
        check("scan on the due day itself: still just a warning already given, no escalation", r1c.escalated == [] and r1c.warned == [], str(r1c.as_dict()))

        # things that happen before the Thursday scan
        await sql("update disputes set first_response_at=:t, status='ClarificationSent' where id=:d", t=datetime(2026, 10, 21, 10, 0, tzinfo=UTC), d=B)       # answered in time
        await sql("update disputes set first_response_at=:t, status='ClarificationSent' where id=:d", t=datetime(2026, 10, 21, 19, 0, tzinfo=UTC), d=C)       # answered 00:30 IST on the 22nd: late
        r = await c.post(f"/disputes/{Dd}/resolve", headers=tok["fa.a"] if owner_of[Dd] == uid["fa.a@example.com"] else tok["fa.b"], json={"narrative": "Settled just before the scan"})
        check("(setup) D was resolved before the scan ran", r.status_code == 200, r.text[:80])
        await sql("update disputes set status='Escalated', escalated_at=now(), assigned_to=:u where id=:d", u=uid["ftm.a@example.com"], d=F)  # already escalated earlier

        # ============================================ scan 2: Thursday 09:16 IST, by the scheduler itself
        await scheduler.seed()
        row = await sql("select kind, last_run_at from scheduled_runs where schedule_name='deadline_scan'")
        DAILY = {"deadline_scan": scheduler.SCHEDULES["deadline_scan"]}  # the gmail_poll schedule is covered by api_test_mail1
        check("the scheduler seeded the deadline_scan row at the epoch", row and row[0][0] == "daily_ist" and row[0][1].year == 1970, str(row))
        check("a tick before 09:15 IST does nothing", await scheduler.tick(ist(2026, 10, 22, 9, 14), schedules=DAILY) == [])
        ran = await scheduler.tick(ist(2026, 10, 22, 9, 16), schedules=DAILY)
        check("a tick after 09:15 IST runs the deadline scan", ran == ["deadline_scan"], str(ran))
        row = await sql("select last_run_at from scheduled_runs where schedule_name='deadline_scan'")
        check("its last run is persisted", row[0][0] == ist(2026, 10, 22, 9, 16))
        check("a second tick the same day does nothing", await scheduler.tick(ist(2026, 10, 22, 15, 0), schedules=DAILY) == [])
        step = await sql("select result::text, output_summary from step_log where entity_type='schedule' and step_name='deadline_scan'")
        check("the run is in the step log with what it did", step and step[0][0] == "success" and len(step[0][1]["escalated"]) == 2, str(step))

        dA, dC = await detail(A), await detail(C)
        check("scan 2: A (no response by its date) is Escalated", dA["status"] == "Escalated" and dA["escalated_at"] is not None)
        check("scan 2: C (answered, but after its date) is Escalated too", dC["status"] == "Escalated")
        check("scan 2: B (answered in time), D (resolved before the scan), E (still in the classify queue), F (already escalated) are left alone",
              (await detail(B))["status"] == "ClarificationSent" and (await detail(Dd))["status"] == "Resolved" and (await detail(E))["status"] == "Raised" and (await detail(F))["status"] == "Escalated")
        check("scan 2: each went to an FTM, and the two are balanced between the two FTMs", {dA["assigned_to"], dC["assigned_to"]} == FTMS, f"{dA['assigned_to']} {dC['assigned_to']}")
        check("scan 2: the deadlines were not touched (the clock is inherited)", dA["first_response_due"] == "2026-10-21" and dA["resolution_due"] == "2026-11-02" and dA["first_response_at"] is None)
        check("scan 2: the timeline says why, by the scheduler", any(t["to_state"] == "Escalated" and t["triggered_by_type"] == "scheduler" and "has not been sent" in t["reason"] for t in dA["timeline"]) and any("was sent late" in (t["reason"] or "") for t in dC["timeline"]))
        check("scan 2: the new owner is told", len(await notices(dA["assigned_to"], "Escalation", A)) == 1 and "escalated to you" in (await notices(dA["assigned_to"], "Escalation", A))[0][0])
        check("scan 2: the admin is told of each escalation", len(await notices(ADM_ID, "Escalation", A)) == 1 and len(await notices(ADM_ID, "Escalation", C)) == 1)
        mm = mails()
        check("scan 2: the customer was sent a status update at the complaint's address (twice, once per escalation)",
              sorted(m["to"][0] for m in mm) == ["d5-1@cust.test", "d5-3@cust.test"] and all("An update on your query [DSP-" in m["subject"] for m in mm), str([m["to"] for m in mm]))
        mA = next(m for m in mm if m["to"] == ["d5-1@cust.test"])
        check("scan 2: it names the invoice and our reference and promises nothing about dates or amounts", "invoice D5-1" in mA["text"] and "2026" not in mA["text"] and "INR" not in mA["text"], mA["text"][:160])
        check("scan 2: the update is recorded as a sent message but is NOT counted as the first response", any(m["kind"] == "sent" for m in dA["messages"]) and dA["first_response_at"] is None)
        before = (len(mails()), (await sql("select count(*) from notifications"))[0][0])
        r2 = await deadlines.scan(ist(2026, 10, 22, 9, 16))
        check("scan 2 again: idempotent (no escalation, no mail, no notice)", r2.escalated == [] and r2.warned == [] and (len(mails()), (await sql("select count(*) from notifications"))[0][0]) == before, str(r2.as_dict()))

        # ============================================ scan 3: Friday 30 Oct, the working day before the resolution date (via the API)
        r = await c.post("/disputes/deadline-scan?as_of=2026-10-30T04:00:00Z", headers=tok["fa.a"])
        check("the scan endpoint is for admins: an FA gets 403", r.status_code == 403)
        check("... an FTM too", (await c.post("/disputes/deadline-scan", headers=tok["ftm.a"])).status_code == 403)
        check("... and it needs a token", (await c.post("/disputes/deadline-scan")).status_code == 401)
        r = await c.post("/disputes/deadline-scan?as_of=2026-10-30T04:00:00Z", headers=H)
        w = {(x["dispute_id"], x["deadline"]) for x in r.json()["warned"]}
        check("scan 3: a resolution warning for each open dispute, including the escalated ones (to their FTM)",
              r.status_code == 200 and w == {(k, "resolution") for k in (A, B, C, F)} and r.json()["escalated"] == [], str(w))
        ftm_notes = await notices((await detail(A))["assigned_to"], "DeadlineWarning", A)
        check("scan 3: the warning for an escalated dispute went to its FTM", len(ftm_notes) == 1 and "resolution" in ftm_notes[0][0], str(ftm_notes))
        check("scan 3 again: idempotent", (await c.post("/disputes/deadline-scan?as_of=2026-10-30T04:00:00Z", headers=H)).json()["warned"] == [])

        # ============================================ scan 4: Tuesday 3 Nov, the resolution date has passed
        r = await c.post("/disputes/deadline-scan?as_of=2026-11-03T04:00:00Z", headers=H)
        esc = r.json()["escalated"]
        check("scan 4: only B is newly escalated (its first response was in time, the resolution is overdue); A, C, F are not escalated twice",
              [e["dispute_id"] for e in esc] == [B] and "resolution was due on 2026-11-02" in esc[0]["reason"] and "first response" not in esc[0]["reason"], str(esc))
        check("scan 4: B's customer was updated", esc[0]["customer_update"] == "sent" and any(m["to"] == ["d5-2@cust.test"] for m in mails()))
        check("scan 4: B is now with an FTM", (await detail(B))["status"] == "Escalated" and (await detail(B))["assigned_to"] in FTMS)

        # ============================================ the mail server is down when a dispute is escalated
        G = await new("D5-7", received="2026-10-26T06:00:00Z")
        H_ = await new("D5-8", received="2026-10-27T06:00:00Z", email="")
        I_ = await new("D5-9", received="2026-10-28T06:00:00Z", email="")
        n_mail = len(mails())
        real_port = s.smtp_port
        s.smtp_port = 1
        r = await deadlines.scan(ist(2026, 10, 29, 9, 15))
        s.smtp_port = real_port
        eg = next(e for e in r.escalated if e["dispute_id"] == G)
        check("scan 5 (mail down): G is escalated anyway, and the customer update is reported as failed", eg["customer_update"] == "failed" and (await detail(G))["status"] == "Escalated", str(r.escalated))
        check("scan 5: the new owner got a SendFailure notice", len(await notices(eg["owner_id"], "SendFailure", G)) == 1)
        check("scan 5: no update was recorded as sent, no mail went out", not any(m["kind"] == "sent" for m in (await detail(G))["messages"]) and len(mails()) == n_mail)
        check("scan 5: H and I (not due yet) were only warned", [e["dispute_id"] for e in r.escalated] == [G] and any(w["dispute_id"] == H_ for w in r.warned))

        # ============================================ nobody to write to
        r = await deadlines.scan(ist(2026, 10, 30, 9, 15))
        eh = next(e for e in r.escalated if e["dispute_id"] == H_)
        check("scan 6: a dispute with no usable address is still escalated; the update is reported as no_address", eh["customer_update"] == "no_address" and (await detail(H_))["status"] == "Escalated", str(r.escalated))

        # ============================================ no FTM is active: an admin takes it
        await sql("update users set is_active=false where email in ('ftm.a@example.com','ftm.b@example.com')")
        r = await deadlines.scan(ist(2026, 10, 31, 9, 15))
        ei = next(e for e in r.escalated if e["dispute_id"] == I_)
        check("scan 7: with no active FTM the admin takes the escalation", ei["owner_id"] == ADM_ID and (await detail(I_))["assigned_to"] == ADM_ID, str(ei))
        check("scan 7: the admin gets one notice, not two", len(await notices(ADM_ID, "Escalation", I_)) == 1)
        await sql("update users set is_active=true where email in ('ftm.a@example.com','ftm.b@example.com')")

        # ============================================ the scheduler itself
        calls: list[str] = []

        async def slow(now):
            calls.append("slow")
            await asyncio.sleep(0.4)
            return {"n": 1}

        flaky_state = {"fail": True}

        async def flaky(now):
            calls.append("flaky")
            if flaky_state["fail"]:
                raise RuntimeError("scripted failure")
            return {"ok": True}

        defs = {
            "t_slow": scheduler.Schedule("interval", seconds=3600),
            "t_flaky": scheduler.Schedule("interval", seconds=60),
        }
        acts = {"t_slow": slow, "t_flaky": flaky}
        async with SessionLocal() as db:
            from data.repositories.platform import schedules_repo
            await schedules_repo.seed(db, {n: d.kind for n, d in defs.items()})
        now = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
        both = await asyncio.gather(
            scheduler.tick(now, schedules={"t_slow": defs["t_slow"]}, actions=acts),
            scheduler.tick(now, schedules={"t_slow": defs["t_slow"]}, actions=acts),
        )
        check("two workers ticking at once: the schedule runs exactly once (SKIP LOCKED)", calls.count("slow") == 1 and sorted(map(len, both)) == [0, 1], f"{calls} {both}")
        check("it is not due again within its interval", await scheduler.tick(now + timedelta(seconds=3599), schedules={"t_slow": defs["t_slow"]}, actions=acts) == [])
        check("and is due after it", await scheduler.tick(now + timedelta(seconds=3600), schedules={"t_slow": defs["t_slow"]}, actions=acts) == ["t_slow"])
        r = await scheduler.tick(now, schedules={"t_flaky": defs["t_flaky"]}, actions=acts)
        lr = (await sql("select last_run_at from scheduled_runs where schedule_name='t_flaky'"))[0][0]
        err = await sql("select result::text, error_message from step_log where entity_type='schedule' and step_name='t_flaky'")
        check("a failing run is not recorded as done (it stays due) and the error is logged", r == [] and lr.year == 1970 and err and err[0][0] == "error" and "scripted failure" in err[0][1], f"{r} {lr} {err}")
        flaky_state["fail"] = False
        check("the next tick retries it and succeeds", await scheduler.tick(now, schedules={"t_flaky": defs["t_flaky"]}, actions=acts) == ["t_flaky"])
        check("a schedule with no row is skipped quietly", await scheduler.tick(now, schedules={"t_missing": defs["t_slow"]}, actions=acts) == [])
        await sql("delete from scheduled_runs where schedule_name in ('t_slow','t_flaky')")

        # ============================================ a worker seeds the schedules
        await sql("delete from scheduled_runs where schedule_name='deadline_scan'")
        await sql("update disputes set status='Resolved', resolved_at=now() where status <> 'Resolved'")  # so the worker's real-clock scan finds nothing to do
        worker = Worker({}, concurrency=1, run_schedules=True)
        await worker.start()
        row = await sql("select kind from scheduled_runs where schedule_name='deadline_scan'")
        check("a starting worker seeds the deadline_scan schedule", row == [("daily_ist",)], str(row))
        await worker.stop(grace_seconds=2)
        check("and stops cleanly (the scheduler task is cancelled)", worker._scheduler is not None and worker._scheduler.done())
        quiet = Worker({}, concurrency=1, run_schedules=False)
        await quiet.start()
        check("a worker started without schedules runs no scheduler", quiet._scheduler is None)
        await quiet.stop(grace_seconds=2)

        # ============================================ housekeeping
        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("the money checker holds", found == [], str(found))
        dup = (await sql("select count(*) from (select entity_id, to_state from state_log where entity_type='dispute' and to_state='Escalated' group by entity_id, to_state having count(*) > 1) t"))[0][0]
        check("no dispute was escalated twice", dup == 0)
        check("the dispute timeline carries no job states", (await sql("select count(*) from state_log where entity_type='dispute' and to_state in ('Queued','Running','Succeeded')"))[0][0] == 0)

    await engine.dispose()
    clear_mail()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
