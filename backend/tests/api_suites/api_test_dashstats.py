"""GET /dashboard/receivables: the dashboard's figures. Real database, no model, no mail. Proves: all zeros (and all five aging
buckets) on an empty book; then, on invoices placed exactly on and either side of every bucket edge, the totals, the overdue and
due-soon parts, the aging buckets with their disputed share, the top overdue customers (largest first, capped at five), money
received this month (rejected, failed, last month's and amount-less payments left out), unapplied money, and the permission."""

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from core.services.disputes.calendar import today_ist
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_dashstats" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
TODAY = today_ist()
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def customer(label):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=label, contact="Anita", email=f"ap{_n[0]}@{RUN}.example.com", gstin=f"29ABCDE{_n[0]:04d}F1Z5",
                     billing_currency="INR", country="India", deducts_tds=False)
        db.add(c)
        await db.commit()
        return c.id


async def invoice(cid, number, balance, days_from_today, *, status=InvoiceStatus.OPEN, paid="0", disputed=False, hold=False):
    """due `days_from_today` days from today (negative = that many days late); `balance` is what is still to collect."""
    total = D(balance) + D(paid)
    async with SessionLocal() as db:
        db.add(Invoice(customer_id=cid, invoice_number=number, invoice_date=TODAY - timedelta(days=120), due_date=TODAY + timedelta(days=days_from_today),
                       invoice_currency="INR", amount_inr=total, tds_amount=D(0), collectible_total=total, amount_paid=D(paid), status=status,
                       is_disputed=disputed, po_hold=hold))
        await db.commit()


async def payment(ref, amount, state, *, on=None, unapplied="0"):
    async with SessionLocal() as db:
        db.add(Payment(reference_number=ref, amount_received_inr=None if amount is None else D(amount), state=state, unapplied_amount=D(unapplied),
                       matched_layer=MatchedLayer.L4, payment_currency="INR", payment_date=on or TODAY))
        await db.commit()


def buckets(body):
    return {b["key"]: (b["count"], D(b["amount"]), b["disputed_count"], D(b["disputed_amount"])) for b in body["aging"]}


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.dash@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.dash@example.com', 'pass12345')).json()['access_token']}"}

        check("no token -> 401", (await c.get("/dashboard/receivables")).status_code == 401)

        # ======================================== A. an empty book
        r = await c.get("/dashboard/receivables", headers=FA)
        b = r.json()
        check("an empty book answers, with today's date in IST", r.status_code == 200 and b["as_of"] == TODAY.isoformat(), r.text[:200])
        check("... all zeros and no one overdue", b["outstanding_count"] == 0 and D(b["outstanding_amount"]) == 0 and b["overdue_count"] == 0 and b["top_overdue"] == [] and D(b["unapplied_amount"]) == 0 and b["received_this_month_count"] == 0)
        check("... and all five aging buckets, in order, even though they are empty", [x["key"] for x in b["aging"]] == ["current", "1-30", "31-60", "61-90", "90+"] and all(x["count"] == 0 for x in b["aging"]), str(b["aging"]))

        # ======================================== B. invoices on and either side of every edge
        acme, zenith, globex = await customer("Acme Traders"), await customer("Zenith Softlabs"), await customer("Globex Corp")
        s1, s2, s3 = await customer("Small One"), await customer("Small Two"), await customer("Small Three")
        await invoice(acme, "A", "1000", +10)  # not yet due
        await invoice(acme, "B", "2000", 0)  # due today: not late, and falls due within the week
        await invoice(acme, "K", "1500", +7)  # the last day of "due soon"
        await invoice(acme, "L", "1600", +8)  # one day past the "due soon" window
        await invoice(acme, "C", "3000", -1, status=InvoiceStatus.PARTLY_PAID, paid="1000")  # 1 day late: the first of 1-30 (balance is what is left)
        await invoice(acme, "D", "4000", -30, disputed=True)  # the last of 1-30
        await invoice(acme, "E", "5000", -31)  # the first of 31-60
        await invoice(zenith, "F", "6000", -60)  # the last of 31-60
        await invoice(zenith, "G", "7000", -61)  # the first of 61-90
        await invoice(globex, "H", "8000", -90)  # the last of 61-90
        await invoice(globex, "I", "9000", -91, hold=True)  # the first of 90+
        await invoice(globex, "J", "5000", -100, status=InvoiceStatus.PAID, paid="5000")  # paid: owes nothing, counted nowhere
        await invoice(s1, "S1", "100", -5)
        await invoice(s2, "S2", "200", -5)
        await invoice(s3, "S3", "300", -5)

        b = (await c.get("/dashboard/receivables", headers=FA)).json()
        check("outstanding: every invoice still to collect, nothing for a paid one", (b["outstanding_count"], D(b["outstanding_amount"])) == (14, D("48700")), str((b["outstanding_count"], b["outstanding_amount"])))
        check("overdue: due before today (the one due today is not)", (b["overdue_count"], D(b["overdue_amount"])) == (10, D("42600")), str((b["overdue_count"], b["overdue_amount"])))
        check("due soon: today up to and including the 7th day", (b["due_soon_count"], D(b["due_soon_amount"])) == (2, D("3500")), str((b["due_soon_count"], b["due_soon_amount"])))
        check("disputed: counted and summed", (b["disputed_count"], D(b["disputed_amount"])) == (1, D("4000")))
        check("PO hold: counted", b["po_hold_count"] == 1)
        a = buckets(b)
        check("aging: not yet due (a day either side of the edge sorted correctly)", a["current"] == (4, D("6100"), 0, D("0")), str(a["current"]))
        check("aging: 1-30 days late, with its disputed share (the invoices on days 1 and 30 are both in)", a["1-30"] == (5, D("7600"), 1, D("4000")), str(a["1-30"]))
        check("aging: 31-60 days late (days 31 and 60)", a["31-60"] == (2, D("11000"), 0, D("0")), str(a["31-60"]))
        check("aging: 61-90 days late (days 61 and 90)", a["61-90"] == (2, D("15000"), 0, D("0")), str(a["61-90"]))
        check("aging: over 90 days late (day 91)", a["90+"] == (1, D("9000"), 0, D("0")), str(a["90+"]))
        check("the buckets add up to the outstanding total", sum(v[0] for v in a.values()) == b["outstanding_count"] and sum(v[1] for v in a.values()) == D(b["outstanding_amount"]))
        top = b["top_overdue"]
        check("top overdue: largest first, capped at five (the smallest debtor is left out)",
              [t["customer_name"] for t in top] == ["Globex Corp", "Zenith Softlabs", "Acme Traders", "Small Three", "Small Two"], str([t["customer_name"] for t in top]))
        check("... with what each owes past due and how long the oldest has been late",
              (top[0]["overdue_count"], D(top[0]["overdue_amount"]), top[0]["oldest_days_late"]) == (2, D("17000"), 91) and (top[2]["overdue_count"], D(top[2]["overdue_amount"]), top[2]["oldest_days_late"]) == (3, D("12000"), 31), str(top[:3]))

        # ======================================== C. money
        month_start = TODAY.replace(day=1)
        await payment("P-APPLIED", "500", PaymentState.APPLIED)
        await payment("P-UNAPPLIED", "300", PaymentState.UNAPPLIED, unapplied="300")
        await payment("P-PART", "200", PaymentState.PARTIALLY_APPLIED, unapplied="50")
        await payment("P-REJECTED", "999", PaymentState.REJECTED)
        await payment("P-FAILED", "888", PaymentState.FAILED)
        await payment("P-LASTMONTH", "700", PaymentState.APPLIED, on=month_start - timedelta(days=1))
        await payment("P-NOAMOUNT", None, PaymentState.RECEIVED)
        b = (await c.get("/dashboard/receivables", headers=FA)).json()
        check("received this month: not rejected, not failed, not last month's, not one with no amount", (b["received_this_month_count"], D(b["received_this_month_amount"])) == (3, D("1000")), str((b["received_this_month_count"], b["received_this_month_amount"])))
        check("unapplied: what Unapplied and PartiallyApplied payments hold", D(b["unapplied_amount"]) == D("350"), b["unapplied_amount"])
        check("money never changes the invoice figures", b["outstanding_count"] == 14 and D(b["outstanding_amount"]) == D("48700"))

        # ======================================== D. it follows the book
        await invoice(globex, "NEW", "50000", -200)
        b = (await c.get("/dashboard/receivables", headers=FA)).json()
        check("a new, very late invoice lands in 90+ and puts its customer first", buckets(b)["90+"][:2] == (2, D("59000")) and b["top_overdue"][0]["oldest_days_late"] == 200 and D(b["top_overdue"][0]["overdue_amount"]) == D("67000"), str(b["top_overdue"][0]))
        check("money amounts are plain decimals (no floats)", all(isinstance(x["amount"], str) for x in b["aging"]))

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
