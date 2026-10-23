"""Reminders and the 60-day handoff. Real database, real SMTP path into Mailpit (http://localhost:8025 must be up), no
model. Proves: the scan (tiers, catch-up, skips, weekend shift, 60-day handoff, suppression, idempotence), the people's
actions (edit, approve = send with live figures, reject), the cancel rules (payment, dispute, paid, stale at approval),
the failure paths (no address, allowlist, server down), the debit-note PDF, the collection tasks (assignment, bundle,
auto-close, manual close, permissions), the scheduler entry, money identities."""

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.jobs import scheduler
from core.services.mail import smtp
from core.services.payments.engine import run_matching
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, InvoiceLineItem, Payment
from data.models.postgres.enums import InvoiceStatus, InvoiceType, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_rem1" in s.database_url, "refusing to run against a non-test database"
MAILPIT = "http://localhost:8025"
IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 22)  # a Thursday
RUN = uuid.uuid4().hex[:6]  # Mailpit keeps mail between runs: every address is new each time
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


def at(day: date, hour=10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def ex(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


async def customer(label, email=None):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="Anita", email=email or f"ap@{label.lower()}-{RUN}.test",
                     gstin=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id, c.email


async def invoice(cid, number, balance, *, due, status=InvoiceStatus.OPEN, disputed=False, kind=InvoiceType.INVOICE, paid="0", lines=()):
    async with SessionLocal() as db:
        inv = Invoice(customer_id=cid, invoice_number=number, type=kind, invoice_date=due - timedelta(days=30), due_date=due,
                      invoice_currency="INR", amount_inr=D(balance) + D(paid), collectible_total=D(balance) + D(paid),
                      amount_paid=D(paid), status=status, is_disputed=disputed)
        db.add(inv)
        await db.flush()
        for desc, total in lines:
            db.add(InvoiceLineItem(invoice_id=inv.id, version_no=1, description=desc, quantity=D(1), unit_price=D(total), line_total=D(total)))
        await db.commit()
        return inv.id


async def rows(number):
    return await q("select r.reminder_tier, r.state::text, r.weekend_shift_days, r.id from reminders r join invoices i on i.id=r.invoice_id "
                   "where i.invoice_number=:n order by r.created_at, r.reminder_tier", n=number)


async def one(number, tier, state=None):
    found = [r for r in await rows(number) if r[0] == tier and (state is None or r[1] == state)]
    return found[-1] if found else None


async def mailbox(mp, address):
    return (await mp.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{address}"})).json().get("messages", [])


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as hc, httpx.AsyncClient(timeout=30) as mp:
        assert (await mp.get(f"{MAILPIT}/api/v1/info")).status_code == 200, "Mailpit must be running on :8025"
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa One", "fa.one@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM"), ("Ftm Two", "ftm.two@example.com", "FTM")):
            assert (await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        H = {u: {"Authorization": f"Bearer {(await login(f'{u}@example.com', 'pass12345')).json()['access_token']}"} for u in ("fa.one", "ftm.one", "ftm.two")}
        FA, FTM1, FTM2 = H["fa.one"], H["ftm.one"], H["ftm.two"]
        smtp.retry_delays_seconds = (0, 0, 0)
        check("the test date is a Thursday", TODAY.weekday() == 3)

        # ============================== A. the scan: tiers, catch-up, skips, handoff, suppression =====================
        ca, addr_a = await customer("Alpha")
        await invoice(ca, "A-1", "900", due=ago(1))  # one day overdue -> gentle
        await invoice(ca, "A-15", "900", due=ago(15))  # exactly 15 -> firm (tier 1 skipped)
        await invoice(ca, "A-18", "900", due=ago(18))  # 18 -> firm, tier 1 skipped (catch-up)
        await invoice(ca, "A-30", "900", due=ago(30))  # 30 -> final, tiers 1 and 15 skipped
        await invoice(ca, "A-0", "900", due=ago(0))  # due today: not overdue
        await invoice(ca, "A-PAID", "0", due=ago(40), status=InvoiceStatus.PAID, paid="900")
        await invoice(ca, "A-DISP", "900", due=ago(20), disputed=True)
        await invoice(ca, "A-PART", "400", due=ago(16), status=InvoiceStatus.PARTLY_PAID, paid="500")
        cb, _ = await customer("Beta")
        await invoice(cb, "B-CASH", "900", due=ago(5))
        async with SessionLocal() as db:
            db.add(Payment(state=PaymentState.UNAPPLIED, customer_id=cb, amount_received_inr=D("100"), unapplied_amount=D("100"), payment_currency="INR"))
            await db.commit()
        cc, _ = await customer("Gamma")
        await invoice(cc, "C-60", "900", due=ago(60))  # 60 days -> handoff
        await invoice(cc, "C-90", "900", due=ago(90))  # already 90 -> handoff, no reminders at all
        await invoice(cc, "C-60D", "900", due=ago(61), disputed=True)  # disputed at 60+: no task (D6)

        r = await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY).isoformat()})
        res = r.json()
        check("the scan runs for an admin", r.status_code == 200, r.text[:200])
        check("an FA may not run the scan -> 403", (await hc.post("/reminders/scan", headers=FA)).status_code == 403)
        check("drafted: A-1 (tier 1), A-15, A-18 (tier 15), A-30 (tier 30), A-PART (tier 15) = 5", res["drafted"] == 5, str(res))
        check("A-1: a gentle reminder is drafted", await one("A-1", 1, "Drafted") is not None, str(await rows("A-1")))
        check("A-18: only the FIRM reminder is drafted; tier 1 is recorded as skipped", [(t, st) for t, st, *_ in await rows("A-18")] == [(1, "SkippedPastThreshold"), (15, "Drafted")], str(await rows("A-18")))
        check("A-15: tier 15 drafted, tier 1 skipped", sorted((t, st) for t, st, *_ in await rows("A-15")) == [(1, "SkippedPastThreshold"), (15, "Drafted")])
        check("A-30: tier 30 drafted, tiers 1 and 15 skipped", sorted((t, st) for t, st, *_ in await rows("A-30")) == [(1, "SkippedPastThreshold"), (15, "SkippedPastThreshold"), (30, "Drafted")])
        check("skipped tiers counted: A-15, A-18, A-30, A-PART = 1+1+2+1 = 5", res["skipped_tiers"] == 5, str(res["skipped_tiers"]))
        check("not overdue / paid / disputed / holding unapplied cash: no reminder rows at all",
              [await rows(n) for n in ("A-0", "A-PAID", "A-DISP", "B-CASH")] == [[], [], [], []], str([await rows(n) for n in ("A-0", "A-PAID", "A-DISP", "B-CASH")]))
        check("suppressed counted (A-DISP and B-CASH; the disputed 61-day one too)", res["suppressed"] == 3, str(res["suppressed"]))
        check("60 days overdue: no reminder, one collection task; already 90: the same; disputed at 61: nothing",
              await rows("C-60") == [] and await rows("C-90") == [] and res["handed_off"] == 2 and
              (await q("select count(*) from tasks t join invoices i on i.id=t.invoice_id where i.invoice_number in ('C-60','C-90') and t.status='Open'"))[0][0] == 2 and
              (await q("select count(*) from tasks t join invoices i on i.id=t.invoice_id where i.invoice_number='C-60D'"))[0][0] == 0, str(res))
        again = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 15).isoformat()})).json()
        check("a second scan the same day changes NOTHING (idempotent)", again["drafted"] == 0 and again["skipped_tiers"] == 0 and again["handed_off"] == 0 and again["cancelled"] == 0, str(again))
        check("the reminder rows are exactly as before (no duplicates)", len(await rows("A-18")) == 2 and len(await rows("A-1")) == 1)
        st = (await q("select scheduled_date, weekend_shift_days from reminders r join invoices i on i.id=r.invoice_id where i.invoice_number='A-1' and r.reminder_tier=1"))[0]
        check("scheduled_date is the day the tier was crossed (due + 1), no weekend shift", st[0] == TODAY and st[1] == 0, str(st))

        # ============================== B. the weekend shift ===========================================================
        cw, _ = await customer("Weekend")
        await invoice(cw, "W-1", "900", due=date(2026, 10, 23))  # Friday: tier 1 falls on Saturday 24th
        sat = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(date(2026, 10, 24)).isoformat()})).json()
        sun = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(date(2026, 10, 25)).isoformat()})).json()
        check("Saturday and Sunday: nothing is drafted for a tier that falls on Saturday", await rows("W-1") == [], f"{sat['drafted']} {sun['drafted']}")
        mon = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(date(2026, 10, 26)).isoformat()})).json()
        w = await one("W-1", 1, "Drafted")
        check("Monday: drafted, with the 2 days the weekend pushed it", w is not None and w[2] == 2, str(await rows("W-1")))
        sched = (await q("select scheduled_date from reminders where id=:i", i=w[3]))[0][0]
        check("it remembers the day the tier was crossed (Saturday), not the day it was drafted", sched == date(2026, 10, 24), str(sched))

        # ============================== C. the associate: read, edit, approve (send), reject ==========================
        a1 = await one("A-1", 1, "Drafted")
        det = (await hc.get(f"/reminders/{a1[3]}", headers=FA)).json()
        check("detail: subject, body with the figure markers, a live preview, recipient and can_send",
              det["state"] == "Drafted" and "[[balance]]" in det["body"] and "INR 900.00" in det["preview_body"] and det["recipient"] == addr_a and det["can_send"], str(det)[:300])
        check("the list is open to the finance team for the read-only question 'was this customer notified?'",
              (await hc.get("/reminders", headers=FTM1, params={"invoice_id": det["invoice_id"]})).status_code == 200)
        check("but an FTM may not act: edit, approve and reject -> 403",
              [(await hc.request(m, f"/reminders/{a1[3]}{p}", headers=FTM1, json=b)).status_code for m, p, b in (("PATCH", "", {"body": "x"}), ("POST", "/approve", {}), ("POST", "/reject", {"reason": "x"}))] == [403, 403, 403])
        r = await hc.patch(f"/reminders/{a1[3]}", headers=FA, json={"body": "Dear Anita, a quick reminder: invoice A-1 has [[balance]] outstanding, [[days_past_due]] day(s) past due. Thank you!"})
        check("the associate edits the wording", r.status_code == 200, r.text[:160])
        await ex("update invoices set amount_paid = 100, status='PartlyPaid' where invoice_number='A-1'")  # the balance moves before approval
        det = (await hc.get(f"/reminders/{a1[3]}", headers=FA)).json()
        check("D4: the preview keeps the prose but shows the LIVE balance (800 now)", "INR 800.00" in det["preview_body"] and "quick reminder" in det["preview_body"], det["preview_body"][:200])
        r = await hc.post(f"/reminders/{a1[3]}/approve", headers=FA)
        check("approve sends it", r.status_code == 200 and r.json()["state"] == "Sent", r.text[:200])
        got = await mailbox(mp, addr_a)
        ref = "REM-" + str(a1[3])[:8].upper()
        check("the customer got ONE mail, with the reminder reference in the subject", len(got) == 1 and ref in got[0]["Subject"] and "Payment reminder" in got[0]["Subject"], str([g["Subject"] for g in got]))
        msg = (await mp.get(f"{MAILPIT}/api/v1/message/{got[0]['ID']}")).json()
        check("the text has the edited prose and the TRUE figures at the moment of sending", "quick reminder" in msg["Text"] and "INR 800.00" in msg["Text"] and "1 day(s) past due" in msg["Text"] and "[[" not in msg["Text"], msg["Text"][:300])
        row = (await q("select state::text, sent_to, sent_subject, message_id, days_overdue_at_send, handled_by is not null, sent_body from reminders where id=:i", i=a1[3]))[0]
        check("persisted: the sent text, the address, the Message-ID, the true day count and who sent it", row[0] == "Sent" and row[1] == addr_a and ref in row[2] and row[3].startswith("<") and row[4] == 1 and row[5] and "INR 800.00" in row[6], str(row)[:200])
        check("the invoice's history records it", (await q("select count(*) from state_log where entity_type='invoice' and reason like 'Reminder (day 1) sent to%'"))[0][0] == 1)
        check("approving again -> 409 (not a draft any more)", (await hc.post(f"/reminders/{a1[3]}/approve", headers=FA)).status_code == 409)
        check("editing a sent reminder -> 409", (await hc.patch(f"/reminders/{a1[3]}", headers=FA, json={"body": "x"})).status_code == 409)
        det = (await hc.get(f"/reminders/{a1[3]}", headers=FTM1)).json()
        check("anyone with view_sent can see it was sent, with the exact text", det["state"] == "Sent" and det["sent_body"] == row[6])
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 16).isoformat()})
        check("a sent tier is never drafted again by a later scan", len([r for r in await rows("A-1") if r[0] == 1]) == 1)

        # reject
        a18 = await one("A-18", 15, "Drafted")
        check("reject needs a reason (422)", (await hc.post(f"/reminders/{a18[3]}/reject", headers=FA, json={})).status_code == 422)
        r = await hc.post(f"/reminders/{a18[3]}/reject", headers=FA, json={"reason": "Customer called, paying tomorrow"})
        check("reject: nothing is sent, the reason is kept", r.status_code == 200 and r.json()["state"] == "Rejected" and len(await mailbox(mp, addr_a)) == 1)
        nxt = at(TODAY + timedelta(days=1))
        res = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": nxt.isoformat()})).json()
        check("a REJECTED tier is drafted again by the next scan (the partial unique index lets it)", (await one("A-18", 15, "Drafted")) is not None and res["drafted"] >= 1, str(await rows("A-18")))

        # ============================== D. cancel rules ===============================================================
        a15 = await one("A-15", 15, "Drafted")
        # a dispute opens on the invoice -> the draft is cancelled at once
        inv15 = (await q("select id from invoices where invoice_number='A-15'"))[0][0]
        r = await hc.post("/disputes", headers=FA, json={"invoice_id": str(inv15), "summary": "The goods were damaged", "category": "Quality"})
        check("a dispute is opened on A-15", r.status_code in (200, 201), r.text[:160])
        dispute_id = r.json().get("dispute_id")
        check("D3: the waiting draft was AutoCancelled the moment the dispute opened", (await one("A-15", 15, "AutoCancelled")) is not None and (await one("A-15", 15, "Drafted")) is None, str(await rows("A-15")))
        res = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY + timedelta(days=1), 11).isoformat()})).json()
        check("while the dispute is open the scan does not draft it again", (await one("A-15", 15, "Drafted")) is None)
        r = await hc.post(f"/disputes/{dispute_id}/resolve", headers=FA, json={"narrative": "Replacement sent, customer satisfied"})
        check("the dispute is resolved", r.status_code == 200, r.text[:160])
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY + timedelta(days=2)).isoformat()})
        check("after the resolution the scan drafts the tier again (the cancelled row does not block it)", (await one("A-15", 15, "Drafted")) is not None, str(await rows("A-15")))

        # a payment arrives -> the draft is cancelled; the cycle does not reset (the next scan quotes the lower balance)
        a30 = await one("A-30", 30, "Drafted")
        async with SessionLocal() as db:
            p = Payment(state=PaymentState.MATCHING, customer_id=ca, payment_currency="INR", payment_date=TODAY, reference_number="UTR-A30",
                        matched_layer=MatchedLayer.L1, amount_received_inr=D("300"), extracted_invoice_numbers=[{"invoice_number": "A-30", "amount": None}])
            db.add(p)
            await db.commit()
            pid = p.id
        async with SessionLocal() as db:
            await run_matching(db, pid, None)
        check("a part payment on A-30 cancelled its waiting draft at once", (await one("A-30", 30, "AutoCancelled")) is not None, str(await rows("A-30")))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY + timedelta(days=1), 12).isoformat()})
        again30 = await one("A-30", 30, "Drafted")
        det = (await hc.get(f"/reminders/{again30[3]}", headers=FA)).json()
        check("the next scan drafts tier 30 again, quoting the LOWER balance, days still counted from the original due date",
              again30 is not None and "INR 600.00" in det["preview_body"] and det["days_overdue"] == 30, f"{det['days_overdue']} {det['preview_body'][:160]}")

        # stale at approval: the invoice was paid by hand after the draft was written
        apart = await one("A-PART", 15, "Drafted")
        await ex("update invoices set amount_paid = collectible_total, status='Paid' where invoice_number='A-PART'")
        r = await hc.post(f"/reminders/{apart[3]}/approve", headers=FA)
        check("approving a draft whose invoice is paid -> 409, and the draft is cancelled, nothing sent",
              r.status_code == 409 and (await one("A-PART", 15, "AutoCancelled")) is not None, r.text[:200])

        # unapplied cash appears after the draft: the scan's clear-out cancels it
        await ex("update invoices set status='Open' where invoice_number='B-CASH'")
        cd, _ = await customer("Delta")
        await invoice(cd, "D-1", "900", due=ago(3))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 17).isoformat()})
        d1 = await one("D-1", 1, "Drafted")
        async with SessionLocal() as db:
            db.add(Payment(state=PaymentState.UNAPPLIED, customer_id=cd, amount_received_inr=D("50"), unapplied_amount=D("50"), payment_currency="INR"))
            await db.commit()
        det = (await hc.get(f"/reminders/{d1[3]}", headers=FA)).json()
        check("the detail says it cannot be sent while the customer holds unapplied cash", not det["can_send"] and "not applied" in det["blocked_reason"], str(det["blocked_reason"]))
        res = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 18).isoformat()})).json()
        check("the scan's clear-out cancelled that draft", (await one("D-1", 1, "AutoCancelled")) is not None and res["cancelled"] >= 1, str(await rows("D-1")))

        # ============================== E. failure paths ==============================================================
        ce, _ = await customer("Echo", email="not-an-address")
        await invoice(ce, "E-1", "900", due=ago(2))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 19).isoformat()})
        e1 = await one("E-1", 1, "Drafted")
        det = (await hc.get(f"/reminders/{e1[3]}", headers=FA)).json()
        check("no valid address: the draft is flagged, recipient empty, cannot be sent", det["recipient"] is None and not det["can_send"] and "no valid e-mail" in det["blocked_reason"])
        r = await hc.post(f"/reminders/{e1[3]}/approve", headers=FA)
        check("approve without an address -> 422 and still a draft", r.status_code == 422 and (await one("E-1", 1, "Drafted")) is not None, r.text[:200])
        await ex("update customers set email=:e where id=:c", e=f"echo-fixed@echo-{RUN}.test", c=ce)
        r = await hc.post(f"/reminders/{e1[3]}/approve", headers=FA)
        check("D7: the customer's address is fixed, approve again -> sent", r.status_code == 200 and len(await mailbox(mp, f"echo-fixed@echo-{RUN}.test")) == 1, r.text[:200])

        cf, addr_f = await customer("Foxtrot")
        await invoice(cf, "F-1", "900", due=ago(2))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 20).isoformat()})
        f1 = await one("F-1", 1, "Drafted")
        s.smtp_allowlist_bypass, s.smtp_allowed_recipients = False, "someone-else@demo.test"
        r = await hc.post(f"/reminders/{f1[3]}/approve", headers=FA)
        fa_id = (await q("select id from users where email='fa.one@example.com'"))[0][0]
        check("a recipient not on the allowlist: 502 with the reason, the reminder stays a draft, nothing sent",
              r.status_code == 502 and "demo allowlist" in r.text and (await one("F-1", 1, "Drafted")) is not None and await mailbox(mp, addr_f) == [], r.text[:200])
        check("the approver got a SendFailure notice and the step log has the error row",
              (await q("select count(*) from notifications where recipient_user_id=:u and type='SendFailure' and entity_id=:r", u=fa_id, r=f1[3]))[0][0] == 1
              and (await q("select count(*) from step_log where entity_type='reminder' and entity_id=:r and result='error'", r=f1[3]))[0][0] == 1)
        s.smtp_allowed_recipients = addr_f
        r = await hc.post(f"/reminders/{f1[3]}/approve", headers=FA)
        check("allowed again: it goes out", r.status_code == 200 and len(await mailbox(mp, addr_f)) == 1)
        s.smtp_allowlist_bypass, s.smtp_allowed_recipients = True, ""
        cg, addr_g = await customer("Golf")
        await invoice(cg, "G-1", "900", due=ago(2))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 21).isoformat()})
        g1 = await one("G-1", 1, "Drafted")
        real_port, s.smtp_port = s.smtp_port, 1
        r = await hc.post(f"/reminders/{g1[3]}/approve", headers=FA)
        check("the mail server is down: 502 after the transport retries, still a draft", r.status_code == 502 and (await one("G-1", 1, "Drafted")) is not None, r.text[:200])
        s.smtp_port = real_port
        check("the server is back: approve works", (await hc.post(f"/reminders/{g1[3]}/approve", headers=FA)).status_code == 200)

        # ============================== F. a debit note carries its PDF ===============================================
        ch, addr_h = await customer("Hotel")
        await invoice(ch, "DN-1", "500", due=ago(2), kind=InvoiceType.DEBIT_NOTE, lines=[("Late payment fee", "300"), ("Re-issue charge", "200")])
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 22).isoformat()})
        dn = await one("DN-1", 1, "Drafted")
        r = await hc.post(f"/reminders/{dn[3]}/approve", headers=FA)
        got = await mailbox(mp, addr_h)
        msg = (await mp.get(f"{MAILPIT}/api/v1/message/{got[0]['ID']}")).json() if got else {}
        atts = msg.get("Attachments", [])
        check("a debit note's reminder carries the generated PDF", r.status_code == 200 and len(atts) == 1 and atts[0]["FileName"] == "DN-1.pdf", f"{r.text[:100]} {atts}")
        if atts:
            import pypdfium2

            pdf = (await mp.get(f"{MAILPIT}/api/v1/message/{got[0]['ID']}/part/{atts[0]['PartID']}")).content
            words = "\n".join(p.get_textpage().get_text_range() for p in pypdfium2.PdfDocument(pdf))
            check("the PDF is the debit note: number, customer and both lines", all(x in words for x in ("DEBIT NOTE", "DN-1", "Hotel Ltd", "Late payment fee", "Re-issue charge")), words[:200])
        check("it is stored once as a generated document and linked to the invoice",
              (await q("select count(*) from source_documents d join invoices i on i.generated_document_id=d.id where i.invoice_number='DN-1' and d.uploaded_via='generated'"))[0][0] == 1)

        # ============================== G. collection tasks ============================================================
        tasks = (await hc.get("/tasks", headers=FTM1, params={"status": "Open"})).json()
        check("the two 60+ day invoices became two open tasks, one for each team member (least loaded, then the other)",
              len(tasks) == 2 and {t["assigned_to_name"] for t in tasks} == {"Ftm One", "Ftm Two"}, str([(t["invoice_number"], t["assigned_to_name"]) for t in tasks]))
        check("an FA may not see the collection tasks -> 403", (await hc.get("/tasks", headers=FA)).status_code == 403)
        t60 = next(t for t in tasks if t["invoice_number"] == "C-60")
        det = (await hc.get(f"/tasks/{t60['task_id']}", headers=FTM2)).json()
        pl = det["payload"]
        check("the bundle: invoice with balance and days, customer contact, payments and reminders",
              pl["invoice"]["invoice_number"] == "C-60" and pl["invoice"]["days_overdue"] == 60 and pl["invoice"]["balance_text"] == "INR 900.00"
              and pl["customer"]["name"] == "Gamma Ltd" and pl["payments"] == [] and pl["reminders"] == [], str(pl)[:300])
        ftm_notes = (await q("select count(*) from notifications where type='Assignment' and entity_type='task'"))[0][0]
        check("each assignee was notified", ftm_notes == 2, str(ftm_notes))
        check("the invoice's history says it was handed to collections", (await q("select count(*) from state_log where entity_type='invoice' and reason like 'Handed to collections%'"))[0][0] == 2)
        # a third invoice at 60: goes to whoever has fewer
        await invoice(cc, "C-60B", "900", due=ago(60))
        await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY, 23).isoformat()})
        tasks = (await hc.get("/tasks", headers=FTM1, params={"status": "Open"})).json()
        by_owner = {}
        for t in tasks:
            by_owner[t["assigned_to_name"]] = by_owner.get(t["assigned_to_name"], 0) + 1
        check("three open tasks are shared 2 + 1 (least-loaded rule)", sorted(by_owner.values()) == [1, 2], str(by_owner))
        check("one task per invoice: a repeat scan adds none", len((await hc.get("/tasks", headers=FTM1, params={"status": "Open"})).json()) == 3)
        # auto-close when paid (through the money path)
        async with SessionLocal() as db:
            p = Payment(state=PaymentState.MATCHING, customer_id=cc, payment_currency="INR", payment_date=TODAY, reference_number="UTR-C60",
                        matched_layer=MatchedLayer.L1, amount_received_inr=D("900"), extracted_invoice_numbers=[{"invoice_number": "C-60", "amount": None}])
            db.add(p)
            await db.commit()
            pid = p.id
        async with SessionLocal() as db:
            await run_matching(db, pid, None)
        det = (await hc.get(f"/tasks/{t60['task_id']}", headers=FTM1)).json()
        check("the invoice was paid: its task closed itself (Paid, by the system)", det["status"] == "Closed" and det["closed_reason"] == "Paid", str(det)[:200])
        # manual close
        t90 = next(t for t in tasks if t["invoice_number"] == "C-90")
        check("a note is required to close a task by hand (422)", (await hc.post(f"/tasks/{t90['task_id']}/close", headers=FTM1, json={"reason": "Dismissed"})).status_code == 422)
        check("an FA may not close one -> 403", (await hc.post(f"/tasks/{t90['task_id']}/close", headers=FA, json={"reason": "Dismissed", "note": "x"})).status_code == 403)
        r = await hc.post(f"/tasks/{t90['task_id']}/close", headers=FTM2, json={"reason": "ResolvedManually", "note": "Customer paid by cheque"})
        check("a team member closes a task by hand", r.status_code == 200 and r.json()["closed_reason"] == "ResolvedManually" and r.json()["closed_note"] == "Customer paid by cheque", r.text[:200])
        check("closing it again -> 409", (await hc.post(f"/tasks/{t90['task_id']}/close", headers=FTM2, json={"reason": "Dismissed", "note": "x"})).status_code == 409)
        # the scan closes a task whose invoice was paid outside the money path
        t60b = next(t for t in tasks if t["invoice_number"] == "C-60B")
        await ex("update invoices set amount_paid = collectible_total, status='Paid' where invoice_number='C-60B'")
        res = (await hc.post("/reminders/scan", headers=ADM, params={"as_of": at(TODAY + timedelta(days=1), 9).isoformat()})).json()
        check("the scan also closes the task of an invoice paid some other way", res["tasks_closed"] == 1 and (await hc.get(f"/tasks/{t60b['task_id']}", headers=FTM1)).json()["closed_reason"] == "Paid", str(res))

        # ============================== H. the scheduler runs it every morning ========================================
        check("the reminder scan is on the scheduler", "reminder_scan" in scheduler.SCHEDULES and "reminder_scan" in scheduler.ACTIONS and scheduler.SCHEDULES["reminder_scan"].at.hour == 9 and scheduler.SCHEDULES["reminder_scan"].at.minute == 0)
        await scheduler.seed()
        daily = {"reminder_scan": scheduler.SCHEDULES["reminder_scan"]}
        check("a tick before 09:00 IST does nothing", await scheduler.tick(at(date(2026, 11, 3), 8), schedules=daily) == [])
        ran = await scheduler.tick(at(date(2026, 11, 3), 9), schedules=daily)
        check("a tick at 09:00 IST runs the reminder scan", ran == ["reminder_scan"], str(ran))
        check("and only once that day", await scheduler.tick(at(date(2026, 11, 3), 15), schedules=daily) == [])

        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        by_hand = {"A-1", "A-PART", "A-PAID", "C-60B"}  # invoices this test paid by editing the row, which the identity rightly objects to
        found = [v for v in found if not (v.entity == "invoice" and v.entity_id in by_hand)]
        check("the money identities hold (for everything that went through the money path)", not found, str(found)[:200])
        check("every invoice has at most one ACTIVE reminder per tier", (await q("select count(*) from (select invoice_id, reminder_tier from reminders where state not in ('AutoCancelled','Rejected') group by 1,2 having count(*)>1) x"))[0][0] == 0)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
