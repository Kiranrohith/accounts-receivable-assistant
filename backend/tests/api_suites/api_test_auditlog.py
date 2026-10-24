"""GET /logs/state, /logs/steps, /logs/steps/{id} and /logs/facets: the admin audit screens. Real database, no model, no mail. Proves:
only an admin may read them (each log has its own permission), a record's whole timeline in the order it happened, the names of
records and of the people who acted, the filters (kind, record, trigger, result, step, job), the text search with % and _ taken
literally, the IST day window, the sorts and paging, one step in full with its inputs, outputs and retries, and the facets."""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Job, SourceDocument, StateLog, StepLog
from data.models.postgres.enums import JobState, StepResult, TriggerType

s = get_settings()
assert "ar_auditlog" in s.database_url, "refusing to run against a non-test database"
results = []
BASE = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def seed(admin_id):
    ids = {}
    async with SessionLocal() as db:
        customer = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email="ap@zenith.test", tax_id_type="GSTIN", tax_id="29ABCDE1234F1Z5",
                            deducts_tds=False, billing_currency="INR", country="India")
        db.add(customer)
        await db.flush()
        invoice = Invoice(customer_id=customer.id, invoice_number="INV-9001", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                          invoice_currency="INR", amount_inr=1000, collectible_total=1000)
        document = SourceDocument(file_name="advice_100%.pdf", file_path="/x/a.pdf")
        db.add_all([invoice, document])
        await db.flush()
        job = Job(job_type="document_intake", entity_type="source_document", entity_id=document.id, state=JobState.SUCCEEDED)
        db.add(job)
        await db.flush()
        ids.update(invoice=invoice.id, document=document.id, job=job.id, gone=uuid.uuid4())

        def st(entity_type, entity_id, from_state, to_state, trig, hours, reason=None, by=None):
            db.add(StateLog(entity_type=entity_type, entity_id=entity_id, from_state=from_state, to_state=to_state, triggered_by_type=trig,
                            triggered_by_id=by, reason=reason, occurred_at=BASE + timedelta(hours=hours)))

        st("invoice", invoice.id, None, "Open", TriggerType.SYSTEM, 0, "Created from uploaded document")
        st("invoice", invoice.id, "Open", "PartlyPaid", TriggerType.USER, 1, "Payment applied: UTR_77 (100% of the first part)", by=admin_id)
        st("invoice", invoice.id, "PartlyPaid", "Paid", TriggerType.SCHEDULER, 2, "Settled")
        st("source_document", document.id, None, "Received", TriggerType.SYSTEM, 0.5, "Uploaded")
        st("dispute", ids["gone"], None, "Raised", TriggerType.SYSTEM, 3, "a record that no longer exists")
        # an IST day edge: 20:00 UTC on 10 Oct is 01:30 IST on 11 Oct
        db.add(StateLog(entity_type="invoice", entity_id=invoice.id, from_state="Paid", to_state="Open", triggered_by_type=TriggerType.USER,
                        triggered_by_id=admin_id, reason="late night", occurred_at=datetime(2026, 10, 10, 20, 0, tzinfo=UTC)))

        def sp(step, result, ms, minutes, *, error=None, model=None, entity=("source_document", document.id), job_id=job.id, inp=None, out=None, attempts=None):
            row = StepLog(job_id=job_id, entity_type=entity[0], entity_id=entity[1], step_name=step, result=result, error_message=error, model_used=model,
                          duration_ms=ms, occurred_at=BASE + timedelta(minutes=minutes), input_summary=inp, output_summary=out, attempt_history=attempts)
            db.add(row)
            return row

        sp("parse", StepResult.SUCCESS, 1200, 1, out={"pages": 2})
        bad = sp("extract", StepResult.ERROR, 30000, 2, error="Model timeout_after 30s", model="groq-llama", inp={"file": "advice_100%.pdf"},
                 attempts=[{"model": "gemini", "error": "429"}, {"model": "groq-llama", "error": "timeout"}])
        sp("route", StepResult.WARNING, 40, 3, out={"disposition": "review"})
        sp("gmail_poll", StepResult.SUCCESS, 900, 4, entity=("schedule", None), job_id=None, out={"new": 3})
        sp("triage", StepResult.SUCCESS, 80, 5, entity=("dispute", ids["gone"]), job_id=None)
        await db.flush()
        ids["bad_step"] = bad.id
        await db.commit()
    return ids


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        r = await login(s.seed_admin_email, s.seed_admin_password)
        ADM = {"Authorization": f"Bearer {r.json()['access_token']}"}
        admin_id = (await c.get("/auth/me", headers=ADM)).json()["id"]
        for name, email, role in (("Fa", "fa.al@example.com", "FA"), ("Ftm", "ftm.al@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.al@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.al@example.com', 'pass12345')).json()['access_token']}"}
        ids = await seed(uuid.UUID(admin_id))

        for path in ("/logs/state", "/logs/steps", "/logs/facets", f"/logs/steps/{ids['bad_step']}"):
            check(f"{path}: no token -> 401", (await c.get(path)).status_code == 401)
            check(f"{path}: an FA -> 403", (await c.get(path, headers=FA)).status_code == 403)
            check(f"{path}: an FTM -> 403", (await c.get(path, headers=FTM)).status_code == 403)

        async def state(**params):
            r = await c.get("/logs/state", headers=ADM, params=params)
            return r, ([f"{i['entity_type']}:{i['to_state']}" for i in r.json()["items"]] if r.status_code == 200 else [])

        # ---- the state log -----------------------------------------------------------------------------------
        r, rows = await state()
        # the system's own writes (the admin's login is not one) may add rows: assert on what we seeded by entity
        _, timeline = await state(entity_type="invoice", entity_id=str(ids["invoice"]), dir="asc")
        check("a record's whole timeline, in the order it happened", timeline == ["invoice:Open", "invoice:PartlyPaid", "invoice:Paid", "invoice:Open"], str(timeline))
        _, newest = await state(entity_type="invoice", entity_id=str(ids["invoice"]))
        check("newest first by default", newest == list(reversed(timeline)), str(newest))
        r, _ = await state(entity_type="invoice", entity_id=str(ids["invoice"]), dir="asc")
        first = r.json()["items"][0]
        check("a row carries the record's name, the states, who and why", (first["entity_label"], first["from_state"], first["to_state"], first["triggered_by_type"], first["reason"]) ==
              ("INV-9001", None, "Open", "system", "Created from uploaded document"), str(first))
        second = r.json()["items"][1]
        check("a person's change names the person", second["triggered_by_type"] == "user" and second["triggered_by_id"] == admin_id and second["triggered_by_name"], str(second))
        r, _ = await state(entity_type="source_document", entity_id=str(ids["document"]))
        check("a file is named by its file name", r.json()["items"][0]["entity_label"] == "advice_100%.pdf", r.text[:200])
        r, _ = await state(entity_type="dispute", entity_id=str(ids["gone"]))
        check("a record that is gone has no name and still shows", r.json()["total"] == 1 and r.json()["items"][0]["entity_label"] is None, r.text[:200])
        r, _ = await state(entity_type="invoice")
        check("kind narrows it, with the total", r.json()["total"] == 4, str(r.json()["total"]))
        r, _ = await state(trigger="scheduler", entity_id=str(ids["invoice"]))
        check("trigger narrows it", [i["to_state"] for i in r.json()["items"]] == ["Paid"], r.text[:200])
        r, _ = await state(trigger="user", entity_id=str(ids["invoice"]))
        check("user-triggered", r.json()["total"] == 2, r.text[:200])
        _, got = await state(q="settled")
        check("search the reason, any case", got == ["invoice:Paid"], str(got))
        _, got = await state(q="100%")
        check("a % in the search matches itself, not everything", got == ["invoice:PartlyPaid"], str(got))
        _, got = await state(q="UTR_7")
        check("a _ in the search matches itself", got == ["invoice:PartlyPaid"], str(got))
        _, got = await state(q="UTRx7")
        check("a _ is not any character", got == [], str(got))
        _, got = await state(q="partlypaid", entity_id=str(ids["invoice"]))
        check("search also reads the states", set(got) == {"invoice:PartlyPaid", "invoice:Paid"}, str(got))

        r, _ = await state(entity_id=str(ids["invoice"]), **{"from": "2026-10-11"})
        check("a day window starts at the start of that day in India: 01:30 IST on the 11th is in", [i["reason"] for i in r.json()["items"]] == ["late night"], r.text[:200])
        r, _ = await state(entity_id=str(ids["invoice"]), to="2026-10-10")
        check("and ends at the end of the last day in India: the 11th's entry is out, the 10th's are in", r.json()["total"] == 3, r.text[:200])
        r, _ = await state(entity_id=str(ids["invoice"]), **{"from": "2026-10-10", "to": "2026-10-11"})
        check("both days together", r.json()["total"] == 4)
        r, _ = await state(entity_id=str(ids["invoice"]), **{"from": "2026-10-12"})
        check("a window with nothing in it -> empty with total 0", r.json() == {"items": [], "total": 0})

        r1, _ = await state(entity_id=str(ids["invoice"]), limit=3, offset=0, dir="asc")
        r2, _ = await state(entity_id=str(ids["invoice"]), limit=3, offset=3, dir="asc")
        check("paging: the total is the whole timeline", r1.json()["total"] == 4 and len(r1.json()["items"]) == 3 and len(r2.json()["items"]) == 1)
        for bad in ({"trigger": "robot"}, {"entity_id": "nope"}, {"dir": "sideways"}, {"limit": 101}, {"from": "yesterday"}):
            check(f"state {bad} -> 422", (await c.get("/logs/state", headers=ADM, params=bad)).status_code == 422)

        # ---- the step log ------------------------------------------------------------------------------------
        async def steps(**params):
            r = await c.get("/logs/steps", headers=ADM, params=params)
            return r, ([i["step_name"] for i in r.json()["items"]] if r.status_code == 200 else [])

        r, names = await steps()
        check("every step, newest first, with the total", r.json()["total"] == 5 and names[0] == "triage" and names[-1] == "parse", str(names))
        _, got = await steps(entity_type="source_document", entity_id=str(ids["document"]), dir="asc")
        check("one record's steps in the order they ran", got == ["parse", "extract", "route"], str(got))
        _, got = await steps(job_id=str(ids["job"]), dir="asc")
        check("one job's steps", got == ["parse", "extract", "route"], str(got))
        _, got = await steps(step_name="extract")
        check("by step name", got == ["extract"], str(got))
        _, got = await steps(result="error")
        check("by result: the failing step", got == ["extract"], str(got))
        _, got = await steps(result="warning")
        check("by result: the warning", got == ["route"], str(got))
        _, got = await steps(entity_type="schedule")
        check("a schedule's step has no record and still shows", got == ["gmail_poll"], str(got))
        r, _ = await steps(entity_type="source_document")
        item = [i for i in r.json()["items"] if i["step_name"] == "extract"][0]
        check("a step row carries the file's name, the error, model and time",
              (item["entity_label"], item["error_message"], item["model_used"], item["duration_ms"], item["result"]) == ("advice_100%.pdf", "Model timeout_after 30s", "groq-llama", 30000, "error"), str(item))
        check("the list leaves the heavy parts for the detail", "input_summary" not in item and "attempt_history" not in item)
        _, got = await steps(q="timeout_after")
        check("search the error as plain text", got == ["extract"], str(got))
        _, got = await steps(q="timeout.after")
        check("a . is not any character, and nothing else matches it", got == [], str(got))
        _, got = await steps(q="GROQ")
        check("search the model, any case", got == ["extract"], str(got))
        _, got = await steps(q="gmail")
        check("search the step name", got == ["gmail_poll"], str(got))
        _, got = await steps(sort="duration", dir="desc")
        check("longest first", got[:2] == ["extract", "parse"], str(got))
        _, got = await steps(sort="duration", dir="asc")
        check("shortest first", got[0] == "route", str(got))
        r, _ = await steps(**{"from": "2026-10-10", "to": "2026-10-10"})
        check("the day window applies to steps too", r.json()["total"] == 5)
        r, _ = await steps(**{"from": "2026-10-11"})
        check("and excludes the other days", r.json()["total"] == 0)
        r1, p1 = await steps(limit=2, offset=0)
        r2, p2 = await steps(limit=2, offset=4)
        check("paging: the total is the whole log", r1.json()["total"] == 5 and len(p1) == 2 and len(p2) == 1)
        for bad in ({"result": "great"}, {"sort": "colour"}, {"job_id": "x"}, {"limit": 0}):
            check(f"steps {bad} -> 422", (await c.get("/logs/steps", headers=ADM, params=bad)).status_code == 422)

        d = await c.get(f"/logs/steps/{ids['bad_step']}", headers=ADM)
        j = d.json()
        check("one step in full: inputs, outputs and the retry history", d.status_code == 200 and j["input_summary"] == {"file": "advice_100%.pdf"} and j["output_summary"] is None and
              [a["model"] for a in j["attempt_history"]] == ["gemini", "groq-llama"] and j["job_id"] == str(ids["job"]), d.text[:300])
        check("an unknown step -> 404", (await c.get(f"/logs/steps/{uuid.uuid4()}", headers=ADM)).status_code == 404)
        check("a bad step id -> 422", (await c.get("/logs/steps/not-a-uuid", headers=ADM)).status_code == 422)

        f = (await c.get("/logs/facets", headers=ADM)).json()
        check("facets: the kinds that have state changes", {"invoice", "source_document", "dispute"} <= set(f["state_entity_types"]), str(f["state_entity_types"]))
        check("facets: the kinds and names that have steps", {"source_document", "schedule", "dispute"} <= set(f["step_entity_types"]) and {"parse", "extract", "route", "gmail_poll"} <= set(f["step_names"]), str(f))
        check("facets: the fixed results and triggers", f["results"] == ["success", "warning", "error"] and f["triggers"] == ["user", "system", "scheduler"], str(f))
        check("the logs are append-only here: nothing to write", (await c.post("/logs/state", headers=ADM, json={})).status_code in (404, 405))

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
