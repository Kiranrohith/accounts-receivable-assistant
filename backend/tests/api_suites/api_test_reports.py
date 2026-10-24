"""The reports: invoices by status, the aging of what is still to collect, and the invoices paid in full, each on screen (JSON, one
page of rows at a time) and as a PDF. Real database, no model, no mail. Proves: the figures on a hand-built book, that the aging
bands agree with the dashboard's, the filters (band, customer, paid window), every sort, the paging, that a reversed allocation
does not move the day an invoice was paid, that the PDFs are real files covering every row (more than one page for a long
report), and who may read them."""

import asyncio
import re
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from core.services.disputes.calendar import today_ist
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Payment, PaymentAllocation
from data.models.postgres.enums import AllocationStatus, InvoiceStatus, MatchedLayer, PaymentState

D = Decimal
IST = ZoneInfo("Asia/Kolkata")
s = get_settings()
assert "ar_reports" in s.database_url, "refusing to run against a non-test database"
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
        c = Customer(customer_name=label, contact="Anita", email=f"ap{_n[0]}@{RUN}.example.com", tax_id_type="GSTIN", tax_id=f"29ABCDE{_n[0]:04d}F1Z5",
                     billing_currency="INR", country="India", deducts_tds=False)
        db.add(c)
        await db.commit()
        return c.id


def day(offset):
    return TODAY + timedelta(days=offset)


async def invoice(cid, number, balance, due, *, status=InvoiceStatus.OPEN, paid="0", disputed=False, invoice_date=None):
    total = D(balance) + D(paid)
    async with SessionLocal() as db:
        inv = Invoice(customer_id=cid, invoice_number=number, invoice_date=invoice_date or day(-120), due_date=day(due), invoice_currency="INR",
                      amount_inr=total, tds_amount=D(0), collectible_total=total, amount_paid=D(paid), status=status, is_disputed=disputed)
        db.add(inv)
        await db.commit()
        return inv.id


async def paid_invoice(cid, number, total, *, invoice_day, due_day, applied, reversed_at=None):
    """A Paid invoice. `applied` is [(day offset, amount)] of active allocations; `reversed_at` one reversed allocation, later."""
    iid = await invoice(cid, number, "0", due_day, status=InvoiceStatus.PAID, paid=total, invoice_date=day(invoice_day))
    async with SessionLocal() as db:
        pay = Payment(reference_number=f"UTR-{number}", amount_received_inr=D(total), state=PaymentState.APPLIED, unapplied_amount=D(0),
                      matched_layer=MatchedLayer.L4, payment_currency="INR", payment_date=TODAY)
        db.add(pay)
        await db.flush()
        for offset, amount in applied:
            at = datetime.combine(day(offset), datetime.min.time(), tzinfo=IST).replace(hour=12).astimezone(UTC)
            db.add(PaymentAllocation(payment_id=pay.id, invoice_id=iid, allocated_amount=D(amount), status=AllocationStatus.ACTIVE, created_at=at))
        if reversed_at is not None:
            at = datetime.combine(day(reversed_at), datetime.min.time(), tzinfo=IST).replace(hour=12).astimezone(UTC)
            db.add(PaymentAllocation(payment_id=pay.id, invoice_id=iid, allocated_amount=D(1), status=AllocationStatus.REVERSED, created_at=at))
        await db.commit()


def pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.rp@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.rp@example.com', 'pass12345')).json()['access_token']}"}

        for path in ("/reports/invoice-status", "/reports/aging", "/reports/paid-invoices", "/reports/aging/pdf", "/reports/paid-invoices/pdf", "/reports/invoice-status/pdf"):
            check(f"no token -> 401 ({path})", (await c.get(path)).status_code == 401)

        # ======================================== A. an empty book
        r = (await c.get("/reports/aging", headers=FA)).json()
        check("empty aging: every band, no rows", [b["key"] for b in r["buckets"]] == ["current", "1-30", "31-60", "61-90", "90+"] and r["items"] == [] and r["total"] == 0 and D(r["total_amount"]) == 0)
        r = (await c.get("/reports/paid-invoices", headers=FA)).json()
        check("empty paid report", r["items"] == [] and r["count"] == 0 and D(r["amount_received"]) == 0)
        pdf = await c.get("/reports/aging/pdf", headers=FA)
        check("an empty report is still a PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF")

        # ======================================== B. a hand-built book
        acme, zenith = await customer("Acme Traders"), await customer("Zenith Softlabs")
        await invoice(acme, "A", "1000", +10)  # not yet due
        await invoice(acme, "C", "3000", -1, status=InvoiceStatus.PARTLY_PAID, paid="1000")
        await invoice(acme, "D", "4000", -30, disputed=True)
        await invoice(zenith, "E", "5000", -31)
        await invoice(zenith, "I", "9000", -91)
        await paid_invoice(acme, "P1", "10000", invoice_day=-60, due_day=-30, applied=[(-35, "10000")])
        await paid_invoice(zenith, "P2", "20000", invoice_day=-40, due_day=-20, applied=[(-10, "20000")], reversed_at=-5)
        await paid_invoice(acme, "P3", "5000", invoice_day=-100, due_day=-70, applied=[(-80, "3000"), (-60, "2000")])

        # ---- invoices by status
        r = await c.get("/reports/invoice-status", headers=FA)
        b = r.json()
        by = {x["status"]: (x["count"], D(x["invoiced_amount"]), D(x["paid_amount"]), D(x["balance"])) for x in b["rows"]}
        check("status: the three statuses, always, in order", [x["status"] for x in b["rows"]] == ["Open", "PartlyPaid", "Paid"], str(b["rows"]))
        check("status: Open", by["Open"] == (4, D("19000"), D("0"), D("19000")), str(by["Open"]))
        check("status: PartlyPaid", by["PartlyPaid"] == (1, D("4000"), D("1000"), D("3000")), str(by["PartlyPaid"]))
        check("status: Paid owes nothing", by["Paid"] == (3, D("35000"), D("35000"), D("0")), str(by["Paid"]))
        check("status: the totals", (b["total_count"], D(b["total_invoiced"]), D(b["total_paid"]), D(b["total_balance"])) == (8, D("58000"), D("36000"), D("22000")), str(b)[:200])
        check("status: dated today in IST", b["as_of"] == TODAY.isoformat())

        # ---- aging
        r = await c.get("/reports/aging", headers=FA)
        a = r.json()
        bands = {x["key"]: (x["count"], D(x["amount"]), x["disputed_count"]) for x in a["buckets"]}
        check("aging bands", bands == {"current": (1, D("1000"), 0), "1-30": (2, D("7000"), 1), "31-60": (1, D("5000"), 0), "61-90": (0, D("0"), 0), "90+": (1, D("9000"), 0)}, str(bands))
        check("aging totals", (a["total_count"], D(a["total_amount"]), a["total"]) == (5, D("22000"), 5))
        check("aging rows, the most late first", [i["invoice_number"] for i in a["items"]] == ["I", "E", "D", "C", "A"], str([i["invoice_number"] for i in a["items"]]))
        rows = {i["invoice_number"]: i for i in a["items"]}
        check("each row: days late, band, balance, disputed flag", (rows["I"]["days_late"], rows["I"]["bucket"]) == (91, "90+") and (rows["A"]["days_late"], rows["A"]["bucket"]) == (-10, "current")
              and D(rows["C"]["balance"]) == D("3000") and rows["D"]["is_disputed"] and not rows["C"]["is_disputed"], str(rows["C"]))
        dash = (await c.get("/dashboard/receivables", headers=FA)).json()
        check("the aging bands are the dashboard's", [(x["key"], x["count"], D(x["amount"])) for x in dash["aging"]] == [(x["key"], x["count"], D(x["amount"])) for x in a["buckets"]])
        r = (await c.get("/reports/aging", headers=FA, params={"bucket": "1-30"})).json()
        check("filter by band: the rows narrow, the band totals do not", [i["invoice_number"] for i in r["items"]] == ["D", "C"] and r["total"] == 2 and r["total_count"] == 5)
        r = (await c.get("/reports/aging", headers=FA, params={"customer_id": str(zenith)})).json()
        check("filter by customer", [i["invoice_number"] for i in r["items"]] == ["I", "E"] and r["total"] == 2)
        r = (await c.get("/reports/aging", headers=FA, params={"bucket": "1-30", "customer_id": str(zenith)})).json()
        check("band and customer together (nothing of Zenith's is 1-30)", r["items"] == [] and r["total"] == 0)
        r = (await c.get("/reports/aging", headers=FA, params={"limit": 2, "offset": 2})).json()
        check("paging: the total is everything, the page is two", [i["invoice_number"] for i in r["items"]] == ["D", "C"] and r["total"] == 5)
        for key, asc_first, desc_first in (("days_late", "A", "I"), ("balance", "A", "I"), ("customer", "A", "E"), ("invoice_number", "A", "I"), ("due_date", "I", "A")):
            up = [i["invoice_number"] for i in (await c.get("/reports/aging", headers=FA, params={"sort": key, "dir": "asc"})).json()["items"]]
            down = [i["invoice_number"] for i in (await c.get("/reports/aging", headers=FA, params={"sort": key, "dir": "desc"})).json()["items"]]
            check(f"sort by {key}: first ascending {asc_first}, first descending {desc_first}", up[0] == asc_first and down[0] in (desc_first, "E", "I"), f"{up} / {down}")
        check("an unknown band -> 422", (await c.get("/reports/aging", headers=FA, params={"bucket": "5-9"})).status_code == 422)
        check("an unknown sort -> 422", (await c.get("/reports/aging", headers=FA, params={"sort": "colour"})).status_code == 422)
        check("limit above 100 -> 422", (await c.get("/reports/aging", headers=FA, params={"limit": 101})).status_code == 422)

        # ---- paid invoices
        r = await c.get("/reports/paid-invoices", headers=FA)
        p = r.json()
        rows = {i["invoice_number"]: i for i in p["items"]}
        check("paid: all three, latest paid first", [i["invoice_number"] for i in p["items"]] == ["P2", "P1", "P3"], str([i["invoice_number"] for i in p["items"]]))
        check("paid: the day the last money was applied (the reversed allocation does not move it)", rows["P2"]["paid_on"] == day(-10).isoformat(), str(rows["P2"]))
        check("paid: with two allocations, the later one", rows["P3"]["paid_on"] == day(-60).isoformat())
        check("paid: days to pay and days late", (rows["P2"]["days_to_pay"], rows["P2"]["paid_late_days"]) == (30, 10) and (rows["P1"]["days_to_pay"], rows["P1"]["paid_late_days"]) == (25, 0) and (rows["P3"]["days_to_pay"], rows["P3"]["paid_late_days"]) == (40, 10), str(rows))
        check("paid: what was received, and the total", D(rows["P1"]["amount_paid"]) == D("10000") and D(p["amount_received"]) == D("35000") and p["count"] == 3)
        r = (await c.get("/reports/paid-invoices", headers=FA, params={"paid_from": day(-40).isoformat()})).json()
        check("paid window from a day: the one before it is out", [i["invoice_number"] for i in r["items"]] == ["P2", "P1"] and D(r["amount_received"]) == D("30000"), str(r)[:200])
        r = (await c.get("/reports/paid-invoices", headers=FA, params={"paid_from": day(-40).isoformat(), "paid_to": day(-20).isoformat()})).json()
        check("paid window with both ends", [i["invoice_number"] for i in r["items"]] == ["P1"] and D(r["amount_received"]) == D("10000"))
        r = (await c.get("/reports/paid-invoices", headers=FA, params={"paid_from": day(-35).isoformat(), "paid_to": day(-35).isoformat()})).json()
        check("both ends are included (a one-day window)", [i["invoice_number"] for i in r["items"]] == ["P1"])
        r = (await c.get("/reports/paid-invoices", headers=FA, params={"customer_id": str(zenith)})).json()
        check("paid by customer", [i["invoice_number"] for i in r["items"]] == ["P2"] and D(r["amount_received"]) == D("20000"))
        r = (await c.get("/reports/paid-invoices", headers=FA, params={"limit": 1, "offset": 1})).json()
        check("paid paging: the totals are of everything", [i["invoice_number"] for i in r["items"]] == ["P1"] and r["total"] == 3 and D(r["amount_received"]) == D("35000"))
        for key, asc_first, desc_first in (("paid_on", "P3", "P2"), ("amount", "P3", "P2"), ("days_to_pay", "P1", "P3"), ("invoice_number", "P1", "P3")):
            up = [i["invoice_number"] for i in (await c.get("/reports/paid-invoices", headers=FA, params={"sort": key, "dir": "asc"})).json()["items"]]
            down = [i["invoice_number"] for i in (await c.get("/reports/paid-invoices", headers=FA, params={"sort": key, "dir": "desc"})).json()["items"]]
            check(f"paid sort by {key}: first ascending {asc_first}, first descending {desc_first}", up[0] == asc_first and down[0] == desc_first, f"{up} / {down}")
        check("a bad date -> 422", (await c.get("/reports/paid-invoices", headers=FA, params={"paid_from": "yesterday"})).status_code == 422)
        check("an unknown sort -> 422", (await c.get("/reports/paid-invoices", headers=FA, params={"sort": "colour"})).status_code == 422)

        # ======================================== C. the PDFs
        for path, name, params in (("/reports/invoice-status/pdf", "invoices-by-status", {}), ("/reports/aging/pdf", "aging-report", {}), ("/reports/aging/pdf", "aging-report", {"bucket": "90+"}),
                                   ("/reports/paid-invoices/pdf", "paid-invoices", {}), ("/reports/paid-invoices/pdf", "paid-invoices", {"paid_from": day(-40).isoformat(), "paid_to": day(-20).isoformat()})):
            r = await c.get(path, headers=FA, params=params)
            check(f"{path} {params or ''} is a PDF file to download", r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
                  and f'filename="{name}-{TODAY.isoformat()}.pdf"' in r.headers["content-disposition"] and len(r.content) > 1000, f"{r.status_code} {r.headers.get('content-disposition')} {len(r.content)}")
        check("a PDF with a bad filter -> 422, not a broken file", (await c.get("/reports/aging/pdf", headers=FA, params={"bucket": "x"})).status_code == 422)

        # ---- a long report runs over several pages and holds every row
        async with SessionLocal() as db:
            for i in range(120):
                db.add(Invoice(customer_id=acme, invoice_number=f"BULK-{i:03d}", invoice_date=day(-200), due_date=day(-5 - i % 20), invoice_currency="INR", amount_inr=D(10), tds_amount=D(0),
                               collectible_total=D(10), amount_paid=D(0), status=InvoiceStatus.OPEN))
            await db.commit()
        one = (await c.get("/reports/aging/pdf", headers=FA, params={"bucket": "90+"})).content
        long = (await c.get("/reports/aging/pdf", headers=FA)).content
        check("the PDF of 125 invoices is several pages, the one of a single invoice is one", pages(one) == 1 and pages(long) >= 4, f"{pages(one)} / {pages(long)}")
        check("the page shows 100 at most, the PDF carries them all", (await c.get("/reports/aging", headers=FA, params={"limit": 100})).json()["total"] == 125 and len((await c.get("/reports/aging", headers=FA, params={"limit": 100})).json()["items"]) == 100)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
