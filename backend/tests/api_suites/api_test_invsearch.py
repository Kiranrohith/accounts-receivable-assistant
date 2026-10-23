"""GET /invoices/search: the invoices page's list. Real database, no model, no mail. Proves: one page with the total of
everything that matches, the filters (status, customer, overdue, disputed, PO hold), the text search (invoice number,
customer name, PO number, with % and _ taken literally), every sort in both directions, the paging, and the permission."""

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice
from data.models.postgres.enums import InvoiceStatus

D = Decimal
s = get_settings()
assert "ar_invsearch" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
TODAY = date.today()
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


async def invoice(cid, number, total, *, due, status=InvoiceStatus.OPEN, paid="0", disputed=False, hold=False, po=None):
    async with SessionLocal() as db:
        db.add(Invoice(customer_id=cid, invoice_number=number, invoice_date=due - timedelta(days=30), due_date=due, invoice_currency="INR",
                       amount_inr=D(total), tds_amount=D(0), collectible_total=D(total), amount_paid=D(paid), status=status,
                       is_disputed=disputed, po_hold=hold, po_number=po))
        await db.commit()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.is@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.is@example.com', 'pass12345')).json()['access_token']}"}

        acme = await customer("Acme Traders")
        zenith = await customer("Zenith Softlabs")
        await invoice(acme, "AC-001", "1000", due=TODAY - timedelta(days=5))  # overdue
        await invoice(acme, "AC-002", "2000", due=TODAY + timedelta(days=5), disputed=True)
        await invoice(acme, "AC-003", "500", due=TODAY - timedelta(days=9), status=InvoiceStatus.PARTLY_PAID, paid="200", po="PO-77")  # overdue
        await invoice(acme, "AC-PAID", "700", due=TODAY - timedelta(days=40), status=InvoiceStatus.PAID, paid="700")  # past due but paid
        await invoice(zenith, "ZN-10%", "3000", due=TODAY + timedelta(days=20), hold=True, po="PO-88")
        await invoice(zenith, "ZN_11", "4000", due=TODAY + timedelta(days=30))

        async def page(**params):
            r = await c.get("/invoices/search", headers=FA, params=params)
            return r, [i["invoice_number"] for i in r.json()["items"]] if r.status_code == 200 else []

        check("no token -> 401", (await c.get("/invoices/search")).status_code == 401)
        r, nums = await page()
        check("everything, newest created first, with the total", r.status_code == 200 and r.json()["total"] == 6 and nums[0] == "ZN_11", str(nums))
        check("an item has the list fields", {"invoice_id", "customer_name", "status", "collectible_balance", "is_disputed", "po_hold", "due_date"} <= set(r.json()["items"][0]))

        # ---- paging: the total is the whole match, not the page
        r, nums = await page(limit=2, offset=0)
        r2, nums2 = await page(limit=2, offset=4)
        check("a page of 2 still reports the total of 6", r.json()["total"] == 6 and len(nums) == 2)
        check("the last page holds the rest", len(nums2) == 2 and not set(nums) & set(nums2), str(nums2))
        check("limit above 100 -> 422", (await c.get("/invoices/search", headers=FA, params={"limit": 101})).status_code == 422)

        # ---- filters
        _, nums = await page(status="Paid")
        check("status Paid", nums == ["AC-PAID"], str(nums))
        _, nums = await page(status="PartlyPaid")
        check("status PartlyPaid", nums == ["AC-003"], str(nums))
        r, nums = await page(overdue="true")
        check("overdue = still to collect and past its due date (the paid one is not)", sorted(nums) == ["AC-001", "AC-003"] and r.json()["total"] == 2, str(nums))
        _, nums = await page(disputed="true")
        check("disputed", nums == ["AC-002"], str(nums))
        _, nums = await page(po_hold="true")
        check("PO hold", nums == ["ZN-10%"], str(nums))
        _, nums = await page(customer_id=str(zenith))
        check("one customer's invoices", sorted(nums) == ["ZN-10%", "ZN_11"], str(nums))
        _, nums = await page(customer_id=str(acme), overdue="true", status="Open")
        check("filters combine", nums == ["AC-001"], str(nums))
        check("an unknown status -> 422", (await c.get("/invoices/search", headers=FA, params={"status": "Bogus"})).status_code == 422)

        # ---- search
        _, nums = await page(q="ac-00")
        check("search by invoice number, any case", sorted(nums) == ["AC-001", "AC-002", "AC-003"], str(nums))
        _, nums = await page(q="zenith")
        check("search by customer name", sorted(nums) == ["ZN-10%", "ZN_11"], str(nums))
        _, nums = await page(q="po-77")
        check("search by PO number", nums == ["AC-003"], str(nums))
        _, nums = await page(q="10%")
        check("a % in the search matches itself, not everything", nums == ["ZN-10%"], str(nums))
        _, nums = await page(q="N_1")
        check("a _ in the search matches itself, not any character", nums == ["ZN_11"], str(nums))
        r, nums = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", r.status_code == 200 and nums == [] and r.json()["total"] == 0)
        _, nums = await page(q="traders", status="Paid")
        check("search and filter together", nums == ["AC-PAID"], str(nums))

        # ---- sorting
        for key, field, asc_first, desc_first in (
            ("invoice_number", None, "AC-001", "ZN_11"),
            ("customer", None, "AC-", "ZN"),
            ("due_date", None, "AC-PAID", "ZN_11"),
            ("amount", None, "AC-003", "ZN_11"),
            ("balance", None, "AC-PAID", "ZN_11"),
        ):
            _, up = await page(sort=key, dir="asc")
            _, down = await page(sort=key, dir="desc")
            check(f"sort by {key}: first ascending {asc_first}, first descending {desc_first}",
                  up and down and up[0].startswith(asc_first) and down[0].startswith(desc_first), f"{up} / {down}")
        _, nums = await page(sort="created", dir="asc")
        check("sort by created ascending puts the oldest first", nums[0] == "AC-001", str(nums))
        _, nums = await page(sort="status", dir="asc")
        check("sort by status runs", len(nums) == 6)
        check("an unknown sort -> 422", (await c.get("/invoices/search", headers=FA, params={"sort": "colour"})).status_code == 422)

        # ---- the old list is unchanged
        r = await c.get("/invoices", headers=FA)
        check("GET /invoices still answers a plain array", r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 6)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
