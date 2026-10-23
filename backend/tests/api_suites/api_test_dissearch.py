"""GET /disputes/search: the disputes page's list. Real database, no model, no mail. Proves: one page with the total of
everything that matches, the filters (status, open only, category, customer, owner, unassigned, overdue), the text search
(summary, customer name, invoice number, with % and _ taken literally), every sort in both directions, the paging, and the
permission (a role without the dashboard permission is refused)."""

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
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Dispute, Invoice
from data.models.postgres.enums import DisputeCategory, DisputeStatus

D = Decimal
s = get_settings()
assert "ar_dissearch" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
TODAY = date.today()
NOW = datetime.now(UTC)
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


async def invoice(cid, number):
    async with SessionLocal() as db:
        inv = Invoice(customer_id=cid, invoice_number=number, invoice_date=TODAY, due_date=TODAY + timedelta(days=30), invoice_currency="INR",
                      amount_inr=D(100), collectible_total=D(100))
        db.add(inv)
        await db.commit()
        return inv.id


async def dispute(cid, inv, summary, *, status=DisputeStatus.ASSIGNED, category=DisputeCategory.PRICING, owner=None, days_ago=0, first_due=None, resolution_due=None):
    async with SessionLocal() as db:
        d = Dispute(customer_id=cid, invoice_id=inv, summary=summary, status=status, category=category, assigned_to=owner,
                    received_at=NOW - timedelta(days=days_ago), first_response_due=first_due, resolution_due=resolution_due,
                    first_response_at=NOW if status is DisputeStatus.RESOLVED else None)
        db.add(d)
        await db.commit()
        return d.id


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa One", "fa.ds@example.com", "FA"), ("Fa Two", "fa2.ds@example.com", "FA")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.ds@example.com', 'pass12345')).json()['access_token']}"}
        async with SessionLocal() as db:
            ids = {n: i for n, i in (await db.execute(text("select username, id from users"))).all()}
        one, two = ids["Fa One"], ids["Fa Two"]

        acme = await customer("Acme Traders")
        zenith = await customer("Zenith Softlabs")
        ia = await invoice(acme, "AC-001")
        iz = await invoice(zenith, "ZN-10%")
        iz2 = await invoice(zenith, "ZN_11")
        yesterday, tomorrow = TODAY - timedelta(days=1), TODAY + timedelta(days=5)
        await dispute(acme, ia, "Goods arrived damaged", owner=one, days_ago=6, first_due=tomorrow, resolution_due=tomorrow)
        await dispute(acme, ia, "Price differs from the quote", category=DisputeCategory.QUALITY, owner=two, days_ago=5, first_due=yesterday, resolution_due=tomorrow)  # first response overdue
        await dispute(zenith, iz, "Wrong item delivered", category=DisputeCategory.DELIVERY, status=DisputeStatus.ESCALATED, owner=one, days_ago=4, first_due=tomorrow, resolution_due=yesterday)  # resolution overdue
        await dispute(zenith, iz2, "Tax shown twice", status=DisputeStatus.RESOLVED, category=DisputeCategory.TAX_DISCREPANCY, owner=two, days_ago=3, first_due=yesterday, resolution_due=yesterday)  # resolved: never overdue
        await dispute(zenith, None, "Who is this invoice for?", status=DisputeStatus.RAISED, category=None, days_ago=2)  # in the classify queue, unassigned
        await dispute(None, None, "Mystery complaint", status=DisputeStatus.RAISED, category=None, days_ago=1)  # no customer at all

        async def page(**params):
            r = await c.get("/disputes/search", headers=FA, params=params)
            return r, [i["summary"] for i in r.json()["items"]] if r.status_code == 200 else []

        check("no token -> 401", (await c.get("/disputes/search")).status_code == 401)
        r, names = await page()
        check("everything, newest received first, with the total", r.status_code == 200 and r.json()["total"] == 6 and names[0] == "Mystery complaint", str(names))
        check("an item has the list fields", {"dispute_id", "status", "category", "customer_name", "invoice_number", "assigned_to_name", "age_days", "first_response_overdue", "resolution_overdue"} <= set(r.json()["items"][0]))

        r, names = await page(limit=2, offset=0)
        r2, names2 = await page(limit=2, offset=4)
        check("a page of 2 still reports the total of 6", r.json()["total"] == 6 and len(names) == 2)
        check("the last page holds the rest", len(names2) == 2 and not set(names) & set(names2), str(names2))
        check("limit above 100 -> 422", (await c.get("/disputes/search", headers=FA, params={"limit": 101})).status_code == 422)

        _, names = await page(open_only="true")
        check("open only leaves out the resolved one", len(names) == 5 and "Tax shown twice" not in names, str(names))
        _, names = await page(status="Raised")
        check("status Raised (the classify queue)", sorted(names) == ["Mystery complaint", "Who is this invoice for?"], str(names))
        _, names = await page(status=["Escalated", "Resolved"])
        check("several statuses at once", sorted(names) == ["Tax shown twice", "Wrong item delivered"], str(names))
        _, names = await page(category="Delivery")
        check("category", names == ["Wrong item delivered"], str(names))
        _, names = await page(customer_id=str(zenith))
        check("one customer's disputes", sorted(names) == ["Tax shown twice", "Who is this invoice for?", "Wrong item delivered"], str(names))
        _, names = await page(assigned_to=str(one))
        check("one owner's disputes", sorted(names) == ["Goods arrived damaged", "Wrong item delivered"], str(names))
        _, names = await page(unassigned="true")
        check("unassigned", sorted(names) == ["Mystery complaint", "Who is this invoice for?"], str(names))
        r, names = await page(overdue="true")
        check("overdue: a missed first response or resolution on an open dispute (a resolved one never is)",
              sorted(names) == ["Price differs from the quote", "Wrong item delivered"] and r.json()["total"] == 2, str(names))
        _, names = await page(customer_id=str(acme), open_only="true", assigned_to=str(two))
        check("filters combine", names == ["Price differs from the quote"], str(names))
        check("an unknown status -> 422", (await c.get("/disputes/search", headers=FA, params={"status": "Bogus"})).status_code == 422)
        check("an unknown category -> 422", (await c.get("/disputes/search", headers=FA, params={"category": "Bogus"})).status_code == 422)

        _, names = await page(q="damaged")
        check("search by summary, any case", names == ["Goods arrived damaged"], str(names))
        _, names = await page(q="ZENITH")
        check("search by customer name", sorted(names) == ["Tax shown twice", "Who is this invoice for?", "Wrong item delivered"], str(names))
        _, names = await page(q="ac-001")
        check("search by invoice number", sorted(names) == ["Goods arrived damaged", "Price differs from the quote"], str(names))
        _, names = await page(q="10%")
        check("a % in the search matches itself, not everything", names == ["Wrong item delivered"], str(names))
        _, names = await page(q="N_1")
        check("a _ in the search matches itself, not any character", names == ["Tax shown twice"], str(names))
        r, names = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", r.status_code == 200 and names == [] and r.json()["total"] == 0)
        _, names = await page(q="zenith", open_only="true", status="Escalated")
        check("search and filter together", names == ["Wrong item delivered"], str(names))

        for key, asc_first, desc_first in (
            ("customer", "Goods arrived damaged", "Wrong item delivered"),
            ("invoice", "Goods arrived damaged", "Wrong item delivered"),
            ("category", "Goods arrived damaged", "Tax shown twice"),
        ):
            _, up = await page(sort=key, dir="asc")
            _, down = await page(sort=key, dir="desc")
            check(f"sort by {key} runs in both directions and puts the dispute with nothing to sort on last",
                  len(up) == 6 and len(down) == 6 and up[-1] in ("Mystery complaint", "Who is this invoice for?") and down[-1] in ("Mystery complaint", "Who is this invoice for?"), f"{up} / {down}")
        _, up = await page(sort="received_at", dir="asc")
        check("sort by received ascending puts the oldest first", up[0] == "Goods arrived damaged", str(up))
        _, up = await page(sort="resolution_due", dir="asc")
        check("sort by resolution date runs", len(up) == 6)
        _, up = await page(sort="status", dir="asc")
        check("sort by status runs", len(up) == 6)
        check("an unknown sort -> 422", (await c.get("/disputes/search", headers=FA, params={"sort": "colour"})).status_code == 422)

        r = await c.get("/disputes", headers=FA)
        check("GET /disputes still answers a plain array", r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 6)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
