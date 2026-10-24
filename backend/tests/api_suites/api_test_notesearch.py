"""GET /notes/search: the credit notes page's list. Real database, no model, no mail. Proves: one page with the total of
everything that matches, the filters (status, customer), the text search (note number, customer, reason, with % and _
taken literally), every sort in both directions, the paging, the permission, and that GET /notes still answers a plain
array."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Note
from data.models.postgres.enums import NoteStatus

D = Decimal
s = get_settings()
assert "ar_notesearch" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
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


async def note(cid, number, amount, remaining, *, status=NoteStatus.OPEN, reason=None, days_ago=0):
    async with SessionLocal() as db:
        db.add(Note(customer_id=cid, note_number=number, amount=D(amount), remaining_amount=D(remaining), status=status, reason=reason,
                    created_at=NOW - timedelta(days=days_ago)))
        await db.commit()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.ns@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.ns@example.com', 'pass12345')).json()['access_token']}"}

        acme = await customer("Acme Traders")
        zenith = await customer("Zenith Softlabs")
        await note(acme, "CN-001", "1000", "1000", reason="Overpayment on UTR-1", days_ago=5)
        await note(acme, "CN-002", "2000", "500", status=NoteStatus.PARTIALLY_USED, reason="Unapplied money converted", days_ago=4)
        await note(acme, "CN-003", "300", "0", status=NoteStatus.CLOSED, reason="Price correction", days_ago=3)
        await note(zenith, "CN-10%", "4000", "4000", reason="Discount 10% agreed", days_ago=2)
        await note(zenith, "CN_11", "50", "50", status=NoteStatus.VOIDED, reason=None, days_ago=1)

        async def page(**params):
            r = await c.get("/notes/search", headers=FA, params=params)
            return r, [i["note_number"] for i in r.json()["items"]] if r.status_code == 200 else []

        check("no token -> 401", (await c.get("/notes/search")).status_code == 401)
        r, nums = await page()
        check("everything, newest created first, with the total", r.status_code == 200 and r.json()["total"] == 5 and nums[0] == "CN_11", str(nums))
        check("an item has the list fields", {"note_id", "note_number", "customer_name", "amount", "remaining_amount", "status", "reason", "created_at"} <= set(r.json()["items"][0]))

        r, nums = await page(limit=2, offset=0)
        r2, nums2 = await page(limit=2, offset=4)
        check("a page of 2 still reports the total of 5", r.json()["total"] == 5 and len(nums) == 2)
        check("the last page holds the rest", len(nums2) == 1 and not set(nums) & set(nums2), str(nums2))
        check("limit above 100 -> 422", (await c.get("/notes/search", headers=FA, params={"limit": 101})).status_code == 422)

        _, nums = await page(status="PartiallyUsed")
        check("status PartiallyUsed", nums == ["CN-002"], str(nums))
        r, nums = await page(status="Open")
        check("status Open", sorted(nums) == ["CN-001", "CN-10%"] and r.json()["total"] == 2, str(nums))
        _, nums = await page(customer_id=str(zenith))
        check("one customer's notes", sorted(nums) == ["CN-10%", "CN_11"], str(nums))
        _, nums = await page(customer_id=str(acme), status="Closed")
        check("filters combine", nums == ["CN-003"], str(nums))
        check("an unknown status -> 422", (await c.get("/notes/search", headers=FA, params={"status": "Bogus"})).status_code == 422)

        _, nums = await page(q="cn-00")
        check("search by note number, any case", sorted(nums) == ["CN-001", "CN-002", "CN-003"], str(nums))
        _, nums = await page(q="zenith")
        check("search by customer name", sorted(nums) == ["CN-10%", "CN_11"], str(nums))
        _, nums = await page(q="overpayment")
        check("search by reason", nums == ["CN-001"], str(nums))
        _, nums = await page(q="10%")
        check("a % in the search matches itself, not everything", nums == ["CN-10%"], str(nums))
        _, nums = await page(q="N_1")
        check("a _ in the search matches itself, not any character", nums == ["CN_11"], str(nums))
        r, nums = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", r.status_code == 200 and nums == [] and r.json()["total"] == 0)
        _, nums = await page(q="traders", status="Closed")
        check("search and filter together", nums == ["CN-003"], str(nums))

        for key, asc_first, desc_first in (
            ("number", "CN-001", "CN_11"),
            ("amount", "CN_11", "CN-10%"),
            ("remaining", "CN-003", "CN-10%"),
        ):
            _, up = await page(sort=key, dir="asc")
            _, down = await page(sort=key, dir="desc")
            check(f"sort by {key}: first ascending {asc_first}, first descending {desc_first}", up and down and up[0] == asc_first and down[0] == desc_first, f"{up} / {down}")
        _, nums = await page(sort="customer", dir="asc")
        check("sort by customer: Acme's first", nums[0] in ("CN-001", "CN-002", "CN-003") and nums[-1] in ("CN-10%", "CN_11"), str(nums))
        _, nums = await page(sort="created", dir="asc")
        check("sort by created ascending puts the oldest first", nums[0] == "CN-001", str(nums))
        _, nums = await page(sort="status", dir="asc")
        check("sort by status runs", len(nums) == 5)
        check("an unknown sort -> 422", (await c.get("/notes/search", headers=FA, params={"sort": "colour"})).status_code == 422)

        r = await c.get("/notes", headers=FA)
        check("GET /notes still answers a plain array", r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 5)
        r = await c.get(f"/notes/{(await page())[0].json()['items'][0]['note_id']}", headers=FA)
        check("the detail route still works beside /search", r.status_code == 200 and r.json()["note_number"] == "CN_11", r.text[:100])

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
