"""Decision 65 end to end: the recipient allowlist on a real send. Real database, REAL SMTP (the local Mailpit),
read back through Mailpit's API. Throwaway DB only."""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal
from data.models.postgres import Customer, Invoice

D = Decimal
s = get_settings()
assert "ar_dis8" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def sql(query, **params):
    async with SessionLocal() as db:
        r = await db.execute(text(query), params)
        rows = r.all() if r.returns_rows else None
        await db.commit()
        return rows


def mails():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        return [[a["Address"] for a in m.get(f"/api/v1/message/{i['ID']}").json()["To"]]
                for i in m.get("/api/v1/messages").json().get("messages", [])]


def clear_mail():
    with httpx.Client(base_url=MAILPIT, timeout=10) as m:
        m.delete("/api/v1/messages")


async def main():
    clear_mail()
    async with SessionLocal() as db:
        cust = Customer(customer_name="D8 Check Ltd", contact="Priya", email="ap@d8check.test", tax_id_type="GSTIN", tax_id="27ABCDE1234F1Z5",
                        deducts_tds=False, billing_currency="INR", country="India")
        db.add(cust)
        await db.flush()
        for n in range(1, 5):
            db.add(Invoice(customer_id=cust.id, invoice_number=f"D8-{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()
        INV = {r[0]: str(r[1]) for r in (await db.execute(text("select invoice_number, id from invoices"))).all()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        H = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}

        async def resolved_draft(inv, frm):
            r = await c.post("/disputes", headers=H, json={"invoice_id": INV[inv], "summary": "damaged goods", "category": "Quality", "from_email": frm})
            assert r.status_code == 201, r.text
            did = r.json()["dispute_id"]
            r = await c.post(f"/disputes/{did}/resolve", headers=H, json={"narrative": "Agreed a credit with the account manager."})
            assert r.status_code == 200, r.text
            return did, r.json()["resolution_draft"]["message_id"]

        def restrict(allowed):
            s.smtp_allowlist_bypass = False
            s.smtp_allowed_recipients = allowed

        # A: an allowlisted recipient is sent, and it really reaches the sink
        restrict("priya@d8check.test, other@d8check.test")
        a, mid = await resolved_draft("D8-1", "Priya@D8Check.test")
        r = await c.post(f"/disputes/{a}/messages/{mid}/send", headers=H)
        check("A: allowlisted recipient -> 200", r.status_code == 200, r.text[:150])
        check("A: the mail arrived at Mailpit", mails() == [["priya@d8check.test"]], str(mails()))

        # B: an unlisted recipient never reaches a server: failed, stays a draft, step-logged, owner notified
        clear_mail()
        b, mid = await resolved_draft("D8-2", "stranger@elsewhere.test")
        r = await c.post(f"/disputes/{b}/messages/{mid}/send", headers=H)
        check("B: unlisted recipient -> 502 with the plain reason", r.status_code == 502 and "recipient not in the demo allowlist" in r.text, r.text[:200])
        check("B: nothing reached Mailpit (and nothing was rerouted there)", mails() == [], str(mails()))
        kind = (await sql("select kind::text from dispute_messages where id = :m", m=mid))[0][0]
        check("B: the message is still a draft (not marked sent)", kind == "draft", kind)
        steps = await sql("select result::text, error_message from step_log where entity_id = :d and step_name = 'send_email'", d=b)
        check("B: a step-log row records the failure", len(steps) == 1 and steps[0][0] == "error" and "allowlist" in steps[0][1], str(steps))
        notes = await sql("select type::text, message from notifications where entity_id = :d", d=b)
        check("B: the owner got a SendFailure notification with the reason", any(n[0] == "SendFailure" and "allowlist" in n[1] for n in notes), str(notes))

        # C: an empty allowlist refuses everything
        restrict("")
        cc, mid = await resolved_draft("D8-3", "priya@d8check.test")
        r = await c.post(f"/disputes/{cc}/messages/{mid}/send", headers=H)
        check("C: empty allowlist refuses even a known address", r.status_code == 502 and "allowlist" in r.text and mails() == [], r.text[:150])

        # D: the bypass flag (dev/CI) lets the same mail through to the sink
        s.smtp_allowlist_bypass = True
        r = await c.post(f"/disputes/{cc}/messages/{mid}/send", headers=H)
        check("D: with the bypass on, the same draft sends", r.status_code == 200 and mails() == [["priya@d8check.test"]], r.text[:150])

    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
