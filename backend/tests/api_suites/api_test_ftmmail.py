"""The finance team manager (FTM) reads the company mailbox and the collection tasks. Real database, no model, no mail.
Proves: FTM may open the Mail Log, Live Mail and one mail's detail (mail.log.view); may send a mail that was set aside as
general mail on to its pipeline (mail.ignored.route) and ONLY such a mail; may not work the triage queue, route a mail in
the queue or a handed-on one, or reroute (all still mail.triage.classify); FA and Admin are unchanged; the two new
permissions are held by FTM and Admin and not by FA. Collection tasks: the list carries the invoice's due date, the days
overdue (open tasks only) and what is still owed, and an FTM sees tasks assigned to someone else."""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.mail import ingest
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, IngestedEmail, Invoice, Task
from data.models.postgres.enums import EmailClassification, EmailStatus, TaskStatus, TaskType

s = get_settings()
assert "ar_ftmmail" in s.database_url, "refusing to run against a non-test database"
NOW = datetime.now(UTC)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


class FakeGmail:
    """Labels are best effort after a decision; here they go nowhere (the real mailbox is never touched)."""

    async def ensure_labels(self, names):
        return {n: n for n in names}

    async def add_labels(self, mid, ids):
        pass

    async def remove_labels(self, mid, ids):
        pass

    async def aclose(self):
        pass


async def mail(key, *, status, classification, subject):
    async with SessionLocal() as db:
        m = IngestedEmail(gmail_message_id=f"{key}-{uuid.uuid4().hex[:8]}", sender=f"{key}@x.example.com", subject=subject, status=status,
                          classification=classification, received_at=NOW - timedelta(hours=1))
        db.add(m)
        await db.commit()
    path = ingest.eml_path(m.gmail_message_id)  # the stored copy of the mail, as the poller leaves it
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"From: {m.sender}", "To: ar@genworx.test", f"Subject: {subject}", "", "Please find the payment details.", ""]
    path.write_bytes("\r\n".join(lines).encode())
    return str(m.id)


async def main():
    ingest.client_factory = lambda: FakeGmail()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.fm@example.com", "FA"), ("Ftm", "ftm.fm@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.fm@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.fm@example.com', 'pass12345')).json()['access_token']}"}

        # ---- the permissions
        rows = await q("select r.code, p.code from role_permissions rp join roles r on r.id=rp.role_id join permissions p on p.id=rp.permission_id where p.code in ('mail.log.view','mail.ignored.route')")
        by = {(r[0], r[1]) for r in rows}
        check("FTM and Admin hold both new permissions", by == {("FTM", "mail.log.view"), ("FTM", "mail.ignored.route"), ("ADM", "mail.log.view"), ("ADM", "mail.ignored.route")}, str(sorted(by)))
        me = (await c.get("/auth/me", headers=FTM)).json()["permissions"]
        check("FTM's own permissions list shows them and nothing else new", "mail.log.view" in me and "mail.ignored.route" in me and "mail.triage.classify" not in me and "users.manage" not in me)

        # ---- reading
        ignored = await mail("ignored", status=EmailStatus.PROCESSED, classification=EmailClassification.GENERAL, subject="Set aside as general")
        waiting = await mail("waiting", status=EmailStatus.HUMAN_TRIAGE, classification=None, subject="Waiting for a person")
        routed = await mail("routed", status=EmailStatus.PROCESSED, classification=EmailClassification.PAYMENT, subject="Handed to payments")
        r = await c.get("/mail/log", headers=FTM)
        check("FTM reads the mail log", r.status_code == 200 and r.json()["total"] == 3, r.text[:120])
        check("FTM reads the live mail feed", (await c.get("/mail/live", headers=FTM)).status_code == 200)
        check("FTM opens one mail's detail", (await c.get(f"/mail/triage/{ignored}", headers=FTM)).status_code == 200)
        check("FA still reads the log", (await c.get("/mail/log", headers=FA)).status_code == 200)

        # ---- what FTM may not do
        check("FTM may not list the triage queue -> 403", (await c.get("/mail/triage", headers=FTM)).status_code == 403)
        r = await c.post(f"/mail/triage/{waiting}/classify", headers=FTM, json={"mail_class": "payment"})
        check("FTM may not route a mail waiting in the queue -> 403", r.status_code == 403, r.text[:160])
        r = await c.post(f"/mail/triage/{routed}/classify", headers=FTM, json={"mail_class": "dispute"})
        check("FTM may not route a mail already handed on -> 403", r.status_code == 403, r.text[:160])
        r = await c.post(f"/mail/triage/{routed}/reroute", headers=FTM, json={"mail_class": "dispute"})
        check("FTM may not reroute -> 403", r.status_code == 403, r.text[:160])
        before = (await q("select status::text, classification::text from ingested_emails where id=:i", i=waiting))[0]
        check("... still waiting after the refused attempt", before[0] is not None and before[1] is None, str(before))

        # ---- what FTM may do: send a set-aside mail on
        r = await c.post(f"/mail/triage/{ignored}/classify", headers=FTM, json={"mail_class": "payment", "note": "it was a payment advice"})
        check("FTM sends a set-aside mail on to its pipeline", r.status_code == 200 and r.json()["mail_class"] == "payment", r.text[:200])
        after = (await q("select classification::text from ingested_emails where id=:i", i=ignored))[0][0]
        check("... and the mail now carries that decision", after == "Payment" or after == "payment", str(after))
        check("a mail gone from the ignored list cannot be routed again -> 403 for FTM", (await c.post(f"/mail/triage/{ignored}/classify", headers=FTM, json={"mail_class": "dispute"})).status_code == 403)
        check("an unknown mail -> 404", (await c.post(f"/mail/triage/{uuid.uuid4()}/classify", headers=FTM, json={"mail_class": "payment"})).status_code in (403, 404))

        # ---- unchanged for FA and Admin
        r = await c.post(f"/mail/triage/{waiting}/classify", headers=FA, json={"mail_class": "general"})
        check("FA still routes a queue mail", r.status_code == 200, r.text[:160])
        check("Admin still lists the triage queue", (await c.get("/mail/triage", headers=ADM)).status_code == 200)

        # ---- collection tasks: the list shows due date, days overdue and balance, and FTM sees another person's task
        async with SessionLocal() as db:
            cust = Customer(customer_name="Overdue Customer", contact="C", email="od@ftmmail.test", tax_id="29ABCDE1234F1Z5", tax_id_type="GSTIN",
                            deducts_tds=False, billing_currency="INR", country="India")
            db.add(cust)
            await db.flush()
            inv = Invoice(customer_id=cust.id, invoice_number="INV-OD-1", invoice_date=date.today() - timedelta(days=150), due_date=date.today() - timedelta(days=116),
                          invoice_currency="INR", amount_inr=Decimal("63720"), collectible_total=Decimal("63720"))
            db.add(inv)
            await db.flush()
            admin_id = (await db.execute(text("select id from users where email=:e"), {"e": s.seed_admin_email})).scalar()
            open_task = Task(type=TaskType.COLLECTION_60D, customer_id=cust.id, invoice_id=inv.id, assigned_to=admin_id, status=TaskStatus.OPEN, payload={"invoice": {"invoice_number": "INV-OD-1"}})
            closed_task = Task(type=TaskType.COLLECTION_60D, customer_id=cust.id, invoice_id=inv.id, assigned_to=admin_id, status=TaskStatus.CLOSED, payload={})
            db.add_all([open_task, closed_task])
            await db.commit()
        r = await c.get("/tasks", headers=FTM, params={"status": "Open"})
        items = r.json()
        check("FTM sees a task assigned to someone else (Admin)", r.status_code == 200 and len(items) == 1 and items[0]["assigned_to_name"], r.text[:200])
        it = items[0]
        check("the item carries the due date, the days overdue and what is still owed", it["due_date"] == (date.today() - timedelta(days=116)).isoformat() and it["days_overdue"] in (115, 116, 117) and Decimal(it["balance"]) == Decimal("63720"), str(it))
        r = await c.get("/tasks", headers=FTM, params={"status": "Closed"})
        check("a closed task has no days overdue", r.json()[0]["days_overdue"] is None, r.text[:200])
        check("the detail carries them too", (await c.get(f"/tasks/{it['task_id']}", headers=FTM)).json()["balance"] is not None)
        check("FA may not list collection tasks -> 403", (await c.get("/tasks", headers=FA)).status_code == 403)

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
