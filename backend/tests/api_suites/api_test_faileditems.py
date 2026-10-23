"""GET /failed-items and its re-runs: background work that gave up. Real database, no model, no mail. Proves: only a job that ended
Failed or DeadLettered AND has nothing after it is listed (a re-run, a later job on the same record, a mail a person has since
routed all take it off the list), every kind shows what it worked on, the search (with % and _ taken literally), the sorts and the
paging, that a re-run queues a new linked job of the same kind and is refused the second time (also when two people press it at
once), what cannot be re-run and why, the several-at-once call, and that a second failure shows with its earlier runs."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
import httpx
from sqlalchemy import select, text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Dispute, IngestedEmail, Job, Note, SourceDocument
from data.models.postgres.enums import EmailClassification, EmailStatus, ErrorClass, JobState

s = get_settings()
assert "ar_faileditems" in s.database_url, "refusing to run against a non-test database"
NOW = datetime.now(UTC)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def seed():
    """Returns the ids by name."""
    ids: dict[str, uuid.UUID] = {}
    async with SessionLocal() as db:
        customer = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email="ap@zenith.test", gstin="29ABCDE1234F1Z5",
                            deducts_tds=False, billing_currency="INR", country="India")
        db.add(customer)
        await db.flush()

        def doc(name, path="/x/file.pdf"):
            row = SourceDocument(file_name=name, file_path=path)
            db.add(row)
            return row

        docs = {
            "inv": doc("INV-1001_scan.pdf"),
            "nofile": doc("gone_100%.pdf", path=None),
            "rerun": doc("already_rerun.pdf"),
            "fine": doc("fine.pdf"),
            "rematch": doc("held_payment.pdf", path=None),
            "later": doc("moved_on.pdf"),
        }
        emails = {
            "bad": IngestedEmail(gmail_message_id="g-bad", sender="buyer@acme.example.com", subject="Payment_advice 7", received_at=NOW),
            "routed": IngestedEmail(gmail_message_id="g-routed", sender="who@x.example.com", subject="Decided by a person", received_at=NOW,
                                    status=EmailStatus.PROCESSED, classification=EmailClassification.PAYMENT),
        }
        db.add_all(emails.values())
        dispute = Dispute(customer_id=customer.id, summary="Goods arrived damaged", received_at=NOW)
        db.add(dispute)
        note = Note(note_number="CN-0007", customer_id=customer.id, amount=100, remaining_amount=100)
        db.add(note)
        await db.flush()
        for key, row in {**docs, **emails}.items():
            ids[key] = row.id
        ids["dispute"], ids["note"], ids["customer"] = dispute.id, note.id, customer.id

        def job(key, job_type, entity_type, entity, state, *, minutes_ago, attempts=1, error=None, rerun_of=None):
            row = Job(job_type=job_type, entity_type=entity_type, entity_id=entity, state=state, attempt_count=attempts,
                      last_error_class=ErrorClass.RETRYABLE if state is JobState.DEAD_LETTERED else ErrorClass.NON_RETRYABLE if error else None,
                      last_error_message=error, created_at=NOW - timedelta(minutes=minutes_ago + 1), finished_at=NOW - timedelta(minutes=minutes_ago),
                      queued_at=NOW - timedelta(minutes=minutes_ago + 1), rerun_of=rerun_of)
            db.add(row)
            return row

        rows = {
            "j_inv": job("j_inv", "document_intake", "source_document", docs["inv"].id, JobState.DEAD_LETTERED, minutes_ago=50, attempts=4, error="All models failed"),
            "j_nofile": job("j_nofile", "invoice_extraction", "source_document", docs["nofile"].id, JobState.FAILED, minutes_ago=40, error="The file is corrupt"),
            "j_rerun_old": job("j_rerun_old", "document_intake", "source_document", docs["rerun"].id, JobState.FAILED, minutes_ago=30, error="Old failure"),
            "j_rerun_new": job("j_rerun_new", "document_intake", "source_document", docs["rerun"].id, JobState.QUEUED, minutes_ago=5),
            "j_fine": job("j_fine", "document_intake", "source_document", docs["fine"].id, JobState.SUCCEEDED, minutes_ago=20),
            "j_rematch": job("j_rematch", "payment_rematch", "source_document", docs["rematch"].id, JobState.FAILED, minutes_ago=25, error="Database blinked"),
            "j_later_old": job("j_later_old", "document_intake", "source_document", docs["later"].id, JobState.FAILED, minutes_ago=60, error="Failed once"),
            "j_later_new": job("j_later_new", "payment_rematch", "source_document", docs["later"].id, JobState.SUCCEEDED, minutes_ago=10),
            "j_mail": job("j_mail", "mail_triage", "ingested_email", emails["bad"].id, JobState.DEAD_LETTERED, minutes_ago=15, attempts=4, error="The model is down"),
            "j_mail_routed": job("j_mail_routed", "mail_triage", "ingested_email", emails["routed"].id, JobState.DEAD_LETTERED, minutes_ago=14, attempts=4, error="The model is down"),
            "j_dispute": job("j_dispute", "dispute_clarification", "dispute", dispute.id, JobState.FAILED, minutes_ago=12, error="No draft could be made"),
            "j_note": job("j_note", "credit_note_mail", "note", note.id, JobState.DEAD_LETTERED, minutes_ago=8, attempts=4, error="SMTP refused"),
            "j_gone": job("j_gone", "mail_triage", "ingested_email", uuid.uuid4(), JobState.FAILED, minutes_ago=70, error="the record is gone"),
        }
        await db.flush()
        for key, row in rows.items():
            ids[key] = row.id
        await db.commit()
    return ids


async def main():
    ids = await seed()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.fi@example.com", "FA"), ("Ftm", "ftm.fi@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.fi@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.fi@example.com', 'pass12345')).json()['access_token']}"}

        async def page(headers=FA, **params):
            r = await c.get("/failed-items", headers=headers, params=params)
            return r, ([i["job_id"] for i in r.json()["items"]] if r.status_code == 200 else [])

        check("no token -> 401", (await c.get("/failed-items")).status_code == 401)
        check("no token on a re-run -> 401", (await c.post(f"/failed-items/{ids['j_inv']}/rerun")).status_code == 401)
        check("an FTM may use it too", (await page(FTM))[0].status_code == 200)

        r, listed = await page()
        want = {"j_inv", "j_nofile", "j_rerun_old", "j_rematch", "j_later_old", "j_mail", "j_dispute", "j_note", "j_gone"}
        want -= {"j_rerun_old", "j_later_old"}  # something came after both
        check("listed: failed or dead-lettered with nothing after, in no other state", set(listed) == {str(ids[k]) for k in want}, f"{r.json()['total']} {len(listed)}")
        check("the total is the whole list", r.json()["total"] == len(want))
        check("newest failure first", listed[0] == str(ids["j_note"]) and listed[-1] == str(ids["j_gone"]), str(listed))
        by_id = {i["job_id"]: i for i in r.json()["items"]}

        i = by_id[str(ids["j_inv"])]
        check("a document: kind, task, file name, state, attempts, error", (i["kind"], i["task"], i["subject"], i["state"], i["attempts"], i["error"]) ==
              ("document", "Reading an uploaded document", "INV-1001_scan.pdf", "DeadLettered", 4, "All models failed"), str(i))
        check("and it can be re-run", i["can_rerun"] is True and i["cannot_rerun_because"] is None and i["earlier_runs"] == 0)
        i = by_id[str(ids["j_mail"])]
        check("a mail: subject and sender", (i["kind"], i["subject"], i["detail"]) == ("mail", "Payment_advice 7", "buyer@acme.example.com"), str(i))
        check("a mail a person has since routed is not listed", str(ids["j_mail_routed"]) not in listed)
        i = by_id[str(ids["j_dispute"])]
        check("a dispute: what it is about and the customer", (i["kind"], i["subject"], i["detail"]) == ("dispute", "Goods arrived damaged", "Zenith Softlabs Pvt Ltd"), str(i))
        i = by_id[str(ids["j_note"])]
        check("a credit note: its number and the customer", (i["kind"], i["subject"], i["detail"], i["task"]) == ("credit_note", "CN-0007", "Zenith Softlabs Pvt Ltd", "Sending a credit note by e-mail"), str(i))
        i = by_id[str(ids["j_nofile"])]
        check("a document whose file is gone cannot be re-run, and says why", i["can_rerun"] is False and "file" in i["cannot_rerun_because"], str(i))
        i = by_id[str(ids["j_rematch"])]
        check("a held-payment match needs no file: it can be re-run", i["can_rerun"] is True and i["task"] == "Matching a held payment again", str(i))
        i = by_id[str(ids["j_gone"])]
        check("a job whose record is gone cannot be re-run, and says why", i["can_rerun"] is False and "gone" in i["cannot_rerun_because"], str(i))
        check("an item carries every field the screen needs", {"job_id", "kind", "task", "subject", "detail", "entity_id", "state", "error", "attempts", "failed_at", "earlier_runs", "can_rerun", "cannot_rerun_because"} <= set(i))

        for kind, expected in (("document", {"j_inv", "j_nofile", "j_rematch"}), ("mail", {"j_mail", "j_gone"}), ("dispute", {"j_dispute"}), ("credit_note", {"j_note"})):
            _, got = await page(kind=kind)
            check(f"kind {kind}", set(got) == {str(ids[k]) for k in expected}, str(got))
        check("an unknown kind -> 422", (await c.get("/failed-items", headers=FA, params={"kind": "other"})).status_code == 422)

        _, got = await page(q="INV-1001")
        check("search by file name", got == [str(ids["j_inv"])], str(got))
        _, got = await page(q="ACME")
        check("search by sender, any case", got == [str(ids["j_mail"])], str(got))
        _, got = await page(q="zenith")
        check("search by customer", set(got) == {str(ids["j_dispute"]), str(ids["j_note"])}, str(got))
        _, got = await page(q="smtp")
        check("search by the error", got == [str(ids["j_note"])], str(got))
        _, got = await page(q="100%")
        check("a % in the search matches itself, not everything", got == [str(ids["j_nofile"])], str(got))
        _, got = await page(q="advice_7")
        check("a _ in the search matches itself, not any character", got == [], str(got))
        _, got = await page(q="Payment_advice")
        check("an underscore in the subject is found", got == [str(ids["j_mail"])], str(got))
        r, got = await page(q="nothing like it")
        check("nothing matches -> empty with total 0", got == [] and r.json()["total"] == 0)
        _, got = await page(q="zenith", kind="credit_note")
        check("search and kind together", got == [str(ids["j_note"])], str(got))

        _, up = await page(sort="failed_at", dir="asc")
        check("oldest failure first when asked", up[0] == str(ids["j_gone"]) and up[-1] == str(ids["j_note"]), str(up))
        _, many = await page(sort="attempts", dir="desc")
        check("sort by attempts: the ones that used all four first", set(many[:3]) == {str(ids["j_inv"]), str(ids["j_mail"]), str(ids["j_note"])}, str(many))
        _, kinds = await page(sort="kind", dir="asc")
        check("sort by kind runs", len(kinds) == len(want))
        check("an unknown sort -> 422", (await c.get("/failed-items", headers=FA, params={"sort": "colour"})).status_code == 422)
        r1, p1 = await page(limit=4, offset=0)
        r2, p2 = await page(limit=4, offset=4)
        check("paging: the total is the whole list", r1.json()["total"] == len(want) and len(p1) == 4 and len(p2) == len(want) - 4, f"{len(p1)} {len(p2)}")
        check("limit above 100 -> 422", (await c.get("/failed-items", headers=FA, params={"limit": 101})).status_code == 422)

        # ---- re-run one -------------------------------------------------------------------------------------
        r = await c.post(f"/failed-items/{ids['j_inv']}/rerun", headers=FA)
        check("re-run answers with the new job", r.status_code == 200 and r.json()["ok"] is True and r.json()["new_job_id"], r.text[:200])
        new_id = r.json()["new_job_id"]
        row = (await q("select job_type, entity_type, entity_id::text, state::text, rerun_of::text, created_by is not null from jobs where id = :i", i=uuid.UUID(new_id)))[0]
        check("a new Queued job of the same kind on the same record, linked to the failed one, run for the person who asked",
              row == ("document_intake", "source_document", str(ids["inv"]), "Queued", str(ids["j_inv"]), True), str(row))
        _, listed = await page()
        check("the failed one has left the list", str(ids["j_inv"]) not in listed and (await page())[0].json()["total"] == len(want) - 1)
        r = await c.post(f"/failed-items/{ids['j_inv']}/rerun", headers=FA)
        check("running it again -> 409 (already run again)", r.status_code == 409 and "already" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/failed-items/{ids['j_fine']}/rerun", headers=FA)
        check("a job that did not fail -> 409", r.status_code == 409, r.text[:200])
        r = await c.post(f"/failed-items/{uuid.uuid4()}/rerun", headers=FA)
        check("no such item -> 404", r.status_code == 404)
        r = await c.post(f"/failed-items/{ids['j_nofile']}/rerun", headers=FA)
        check("a document whose file is gone -> 409", r.status_code == 409 and "file" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/failed-items/{ids['j_gone']}/rerun", headers=FA)
        check("a record that is gone -> 409", r.status_code == 409 and "gone" in r.json()["detail"], r.text[:200])
        r = await c.post("/failed-items/not-a-uuid/rerun", headers=FA)
        check("a bad id -> 422", r.status_code == 422)

        # a second failure shows with its earlier run counted
        await q_exec("update jobs set state='Failed', finished_at=now(), last_error_message='Failed again' where id=:i", uuid.UUID(new_id))
        r, got = await page()
        again = {i["job_id"]: i for i in r.json()["items"]}.get(new_id)
        check("the second failure is listed, once, with one earlier run, and the first stays off the list", again is not None and again["earlier_runs"] == 1 and str(ids["j_inv"]) not in got, str(again))

        # ---- two people at once ------------------------------------------------------------------------------
        a, b = await asyncio.gather(c.post(f"/failed-items/{ids['j_mail']}/rerun", headers=FA), c.post(f"/failed-items/{ids['j_mail']}/rerun", headers=FTM))
        check("two people re-running the same item at once: exactly one 200 and one 409", sorted([a.status_code, b.status_code]) == [200, 409], f"{a.status_code} {b.status_code}")
        n = (await q("select count(*) from jobs where rerun_of = :i", i=ids["j_mail"]))[0][0]
        check("and one new job only", n == 1, str(n))
        mail_job = (await q("select created_by from jobs where rerun_of = :i", i=ids["j_mail"]))[0][0]
        check("a mail's job keeps its original (system) owner", mail_job is None, str(mail_job))

        # ---- several at once ---------------------------------------------------------------------------------
        body = {"job_ids": [str(ids["j_dispute"]), str(ids["j_note"]), str(ids["j_nofile"]), str(ids["j_inv"]), str(uuid.uuid4()), str(ids["j_dispute"])]}
        r = await c.post("/failed-items/rerun", headers=FA, json=body)
        out = {x["job_id"]: x for x in r.json()["results"]} if r.status_code == 200 else {}
        check("several: always 200, one result per distinct id, the count queued", r.status_code == 200 and len(out) == 5 and r.json()["queued"] == 2, r.text[:300])
        check("the two that could go ahead did", out[str(ids["j_dispute"])]["ok"] and out[str(ids["j_note"])]["ok"] and out[str(ids["j_dispute"])]["new_job_id"])
        check("the others say why, and did not stop the rest", not out[str(ids["j_nofile"])]["ok"] and "file" in out[str(ids["j_nofile"])]["message"] and not out[str(ids["j_inv"])]["ok"] and not out[str(body['job_ids'][4])]["ok"], str(out)[:300])
        kinds_new = sorted(x[0] for x in await q("select job_type from jobs where rerun_of in (:d, :n)", d=ids["j_dispute"], n=ids["j_note"]))
        check("each was queued as its own kind", kinds_new == ["credit_note_mail", "dispute_clarification"], str(kinds_new))
        entity_types = sorted(x[0] for x in await q("select entity_type from jobs where rerun_of in (:d, :n)", d=ids["j_dispute"], n=ids["j_note"]))
        check("on the right kind of record", entity_types == ["dispute", "note"], str(entity_types))
        check("an empty list -> 422", (await c.post("/failed-items/rerun", headers=FA, json={"job_ids": []})).status_code == 422)
        check("more than 50 -> 422", (await c.post("/failed-items/rerun", headers=FA, json={"job_ids": [str(uuid.uuid4()) for _ in range(51)]})).status_code == 422)
        r, got = await page()
        check("what was re-run has left the list; the ones that could not be stay", str(ids["j_dispute"]) not in got and str(ids["j_note"]) not in got and str(ids["j_nofile"]) in got)
        steps = (await q("select count(*) from state_log where entity_type = 'dispute_job' and entity_id = :d and reason = 'Rerun'", d=ids["dispute"]))[0][0]
        check("the re-run is written to the state log", steps == 1, str(steps))

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


async def q_exec(sql, i):
    async with SessionLocal() as db:
        await db.execute(text(sql), {"i": i})
        await db.commit()


asyncio.run(main())
