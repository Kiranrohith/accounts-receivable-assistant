"""GET /purchase-orders/search: the purchase orders page's list. Real database, no model, no mail. Proves: one page with
the total of everything that matches, the filters (customer, fulfilment as the quantity ledger says it, on hold), the text
search (PO number, customer name, with % and _ taken literally), every sort in both directions, the paging, that the
fulfilment filter agrees with the fulfilment each row reports, and the permission."""

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
from data.models.postgres import Customer, Invoice, PoFulfillment, PoInvoiceMatch, PurchaseOrder, PurchaseOrderLineItem
from data.models.postgres.enums import InvoiceStatus

D = Decimal
s = get_settings()
assert "ar_posearch" in s.database_url, "refusing to run against a non-test database"
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


async def po(cid, number, lines, *, total=None, days_ago=0, invoiced=(), hold=False):
    """lines: quantities ordered; invoiced: quantity consumed per line (same order, 0 for none)."""
    async with SessionLocal() as db:
        order = PurchaseOrder(customer_id=cid, po_number=number, po_date=TODAY - timedelta(days=days_ago), currency="INR", total_amount=D(total) if total else None)
        db.add(order)
        await db.flush()
        rows = []
        for i, q in enumerate(lines):
            line = PurchaseOrderLineItem(po_id=order.id, description=f"Item {i}", normalized_description=f"item {i}", quantity=None if q is None else D(q))
            db.add(line)
            rows.append(line)
        await db.flush()
        if any(D(x) for x in invoiced) or hold:
            inv = Invoice(customer_id=cid, invoice_number=f"INV-{number}", invoice_date=TODAY, due_date=TODAY + timedelta(days=30), invoice_currency="INR",
                          amount_inr=D(100), tds_amount=D(0), collectible_total=D(100), amount_paid=D(0), status=InvoiceStatus.OPEN, po_hold=hold)
            db.add(inv)
            await db.flush()
            db.add(PoInvoiceMatch(po_id=order.id, invoice_id=inv.id, match_direction="po_first"))
            for line, done in zip(rows, invoiced):
                if D(done):
                    db.add(PoFulfillment(po_line_item_id=line.id, invoice_id=inv.id, quantity_invoiced=D(done)))
        await db.commit()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.po@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.po@example.com', 'pass12345')).json()['access_token']}"}

        acme = await customer("Acme Traders")
        zenith = await customer("Zenith Softlabs")
        await po(acme, "PO-001", ["10", "5"], total="1000", days_ago=6)  # nothing invoiced
        await po(acme, "PO-002", ["10", "5"], total="2000", days_ago=5, invoiced=("4", "0"))  # partly
        await po(acme, "PO-003", ["10", "5"], total="500", days_ago=4, invoiced=("10", "5"))  # fully
        await po(zenith, "PO-10%", ["3"], total="3000", days_ago=3, invoiced=("5",), hold=True)  # over, and a linked invoice on hold
        await po(zenith, "PO_11", ["4"], total="4000", days_ago=2, invoiced=("1",))  # partly
        await po(zenith, "PO-NOQTY", [None], total="100", days_ago=1)  # no quantity to judge: not invoiced

        async def page(**params):
            r = await c.get("/purchase-orders/search", headers=FA, params=params)
            return r, [i["po_number"] for i in r.json()["items"]] if r.status_code == 200 else []

        check("no token -> 401", (await c.get("/purchase-orders/search")).status_code == 401)
        r, nums = await page()
        check("everything, newest created first, with the total", r.status_code == 200 and r.json()["total"] == 6 and nums[0] == "PO-NOQTY", str(nums))
        check("an item has the list fields", {"purchase_order_id", "customer_name", "fulfillment", "linked_invoices", "invoices_on_hold", "total_amount", "line_count"} <= set(r.json()["items"][0]))

        r, nums = await page(limit=2, offset=0)
        r2, nums2 = await page(limit=2, offset=4)
        check("a page of 2 still reports the total of 6", r.json()["total"] == 6 and len(nums) == 2)
        check("the last page holds the rest", len(nums2) == 2 and not set(nums) & set(nums2), str(nums2))
        check("limit above 100 -> 422", (await c.get("/purchase-orders/search", headers=FA, params={"limit": 101})).status_code == 422)

        # ---- fulfilment: the filter must agree with what each row says
        for verdict, expected in (
            ("Not invoiced", {"PO-001", "PO-NOQTY"}),
            ("Partly invoiced", {"PO-002", "PO_11"}),
            ("Fully invoiced", {"PO-003"}),
            ("Over-invoiced", {"PO-10%"}),
        ):
            r, nums = await page(fulfillment=verdict)
            check(f"fulfilment {verdict}", set(nums) == expected and r.json()["total"] == len(expected), str(nums))
        r, _ = await page()
        reported = {i["po_number"]: i["fulfillment"] for i in r.json()["items"]}
        agree = True
        for verdict in ("Not invoiced", "Partly invoiced", "Fully invoiced", "Over-invoiced"):
            _, nums = await page(fulfillment=verdict)
            agree = agree and all(reported[n] == verdict for n in nums)
        check("every filtered row reports the fulfilment it was filtered by", agree, str(reported))
        check("an unknown fulfilment -> 422", (await c.get("/purchase-orders/search", headers=FA, params={"fulfillment": "Bogus"})).status_code == 422)

        _, nums = await page(on_hold="true")
        check("on hold: a linked invoice does not match", nums == ["PO-10%"], str(nums))
        r, nums = await page(on_hold="false")
        check("not on hold", sorted(nums) == ["PO-001", "PO-002", "PO-003", "PO-NOQTY", "PO_11"] and r.json()["total"] == 5, str(nums))
        _, nums = await page(customer_id=str(zenith))
        check("one customer's purchase orders", sorted(nums) == ["PO-10%", "PO-NOQTY", "PO_11"], str(nums))
        _, nums = await page(customer_id=str(acme), fulfillment="Partly invoiced")
        check("filters combine", nums == ["PO-002"], str(nums))

        # ---- search
        _, nums = await page(q="po-00")
        check("search by PO number, any case", sorted(nums) == ["PO-001", "PO-002", "PO-003"], str(nums))
        _, nums = await page(q="zenith")
        check("search by customer name", sorted(nums) == ["PO-10%", "PO-NOQTY", "PO_11"], str(nums))
        _, nums = await page(q="10%")
        check("a % in the search matches itself, not everything", nums == ["PO-10%"], str(nums))
        _, nums = await page(q="O_1")
        check("a _ in the search matches itself, not any character", nums == ["PO_11"], str(nums))
        r, nums = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", r.status_code == 200 and nums == [] and r.json()["total"] == 0)
        _, nums = await page(q="traders", fulfillment="Fully invoiced")
        check("search and filter together", nums == ["PO-003"], str(nums))

        # ---- sorting
        for key, asc_first, desc_first in (
            ("po_number", "PO-0", "PO_"),
            ("po_date", "PO-001", "PO-NOQTY"),
            ("amount", "PO-NOQTY", "PO_11"),
        ):
            _, up = await page(sort=key, dir="asc")
            _, down = await page(sort=key, dir="desc")
            check(f"sort by {key}: first ascending {asc_first}, first descending {desc_first}",
                  up and down and up[0].startswith(asc_first) and down[0].startswith(desc_first), f"{up} / {down}")
        _, nums = await page(sort="customer", dir="asc")
        check("sort by customer: Acme's first", nums[0] in ("PO-001", "PO-002", "PO-003"), str(nums))
        _, nums = await page(sort="customer", dir="desc")
        check("sort by customer descending: Zenith's first", nums[0] in ("PO-10%", "PO_11", "PO-NOQTY"), str(nums))
        _, nums = await page(sort="created", dir="asc")
        check("sort by created ascending puts the oldest first", nums[0] == "PO-001", str(nums))
        check("an unknown sort -> 422", (await c.get("/purchase-orders/search", headers=FA, params={"sort": "colour"})).status_code == 422)

        r = await c.get("/purchase-orders", headers=FA)
        check("GET /purchase-orders still answers a plain array", r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 6)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
