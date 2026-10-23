"""GET /payments/search: the payments page's list. Real database, no model, no mail. Proves: one page with the total of
everything that matches, the filters (state, customer), the text search (reference, payer, customer, with % and _ taken
literally), every sort in both directions, the paging, and the permission."""

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
from data.models.postgres import Customer, Payment
from data.models.postgres.enums import MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_paysearch" in s.database_url, "refusing to run against a non-test database"
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


async def payment(cid, reference, amount, *, payer, state=PaymentState.APPLIED, days_ago=0):
    async with SessionLocal() as db:
        db.add(Payment(customer_id=cid, reference_number=reference, payee_name=payer, amount_received_inr=D(amount), state=state,
                       unapplied_amount=D(amount) if state is PaymentState.UNAPPLIED else D(0), matched_layer=MatchedLayer.L4,
                       payment_currency="INR", payment_date=TODAY - timedelta(days=days_ago)))
        await db.commit()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=ADM, json={"name": "Fa", "email": "fa.ps@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.ps@example.com', 'pass12345')).json()['access_token']}"}

        acme = await customer("Acme Traders")
        zenith = await customer("Zenith Softlabs")
        await payment(acme, "UTR-001", "1000", payer="Acme Traders Pvt Ltd", days_ago=5)
        await payment(acme, "UTR-002", "2000", payer="Acme Accounts", state=PaymentState.UNAPPLIED, days_ago=4)
        await payment(acme, "UTR-003", "500", payer="Acme Traders Pvt Ltd", state=PaymentState.NEEDS_REVIEW, days_ago=3)
        await payment(zenith, "UTR-10%", "3000", payer="Zenith Softlabs", state=PaymentState.HELD, days_ago=2)
        await payment(zenith, "UTR_11", "4000", payer="Z Soft", days_ago=1)
        await payment(None, "UTR-NOBODY", "100", payer="Somebody Else", state=PaymentState.NEEDS_REVIEW, days_ago=0)

        async def page(**params):
            r = await c.get("/payments/search", headers=FA, params=params)
            return r, [i["reference_number"] for i in r.json()["items"]] if r.status_code == 200 else []

        check("no token -> 401", (await c.get("/payments/search")).status_code == 401)
        r, refs = await page()
        check("everything, newest created first, with the total", r.status_code == 200 and r.json()["total"] == 6 and refs[0] == "UTR-NOBODY", str(refs))
        check("an item has the list fields", {"payment_id", "state", "customer_name", "payer_name", "amount_received_inr", "unapplied_amount", "reasons"} <= set(r.json()["items"][0]))

        r, refs = await page(limit=2, offset=0)
        r2, refs2 = await page(limit=2, offset=4)
        check("a page of 2 still reports the total of 6", r.json()["total"] == 6 and len(refs) == 2)
        check("the last page holds the rest", len(refs2) == 2 and not set(refs) & set(refs2), str(refs2))
        check("limit above 100 -> 422", (await c.get("/payments/search", headers=FA, params={"limit": 101})).status_code == 422)

        _, refs = await page(state="Unapplied")
        check("state Unapplied", refs == ["UTR-002"], str(refs))
        r, refs = await page(state="NeedsReview")
        check("state NeedsReview (one with no customer yet)", sorted(refs) == ["UTR-003", "UTR-NOBODY"] and r.json()["total"] == 2, str(refs))
        _, refs = await page(customer_id=str(zenith))
        check("one customer's payments", sorted(refs) == ["UTR-10%", "UTR_11"], str(refs))
        _, refs = await page(customer_id=str(acme), state="Applied")
        check("filters combine", refs == ["UTR-001"], str(refs))
        check("an unknown state -> 422", (await c.get("/payments/search", headers=FA, params={"state": "Bogus"})).status_code == 422)

        _, refs = await page(q="utr-00")
        check("search by reference, any case", sorted(refs) == ["UTR-001", "UTR-002", "UTR-003"], str(refs))
        _, refs = await page(q="z soft")
        check("search by payer name", refs == ["UTR_11"], str(refs))
        _, refs = await page(q="zenith")
        check("search by customer name", sorted(refs) == ["UTR-10%", "UTR_11"], str(refs))
        _, refs = await page(q="10%")
        check("a % in the search matches itself, not everything", refs == ["UTR-10%"], str(refs))
        _, refs = await page(q="R_1")
        check("a _ in the search matches itself, not any character", refs == ["UTR_11"], str(refs))
        r, refs = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", r.status_code == 200 and refs == [] and r.json()["total"] == 0)
        _, refs = await page(q="traders", state="NeedsReview")
        check("search and filter together", refs == ["UTR-003"], str(refs))

        for key, asc_first, desc_first in (
            ("reference", "UTR-", "UTR_"),
            ("payer", "UTR-002", "UTR-10%"),
            ("date", "UTR-001", "UTR-NOBODY"),
            ("amount", "UTR-NOBODY", "UTR_11"),
        ):
            _, up = await page(sort=key, dir="asc")
            _, down = await page(sort=key, dir="desc")
            check(f"sort by {key}: first ascending {asc_first}, first descending {desc_first}",
                  up and down and up[0].startswith(asc_first) and down[0].startswith(desc_first), f"{up} / {down}")
        _, refs = await page(sort="customer", dir="asc")
        check("sort by customer: Acme's first, and a payment with no customer last", refs[0] in ("UTR-001", "UTR-002", "UTR-003") and refs[-1] == "UTR-NOBODY", str(refs))
        _, refs = await page(sort="customer", dir="desc")
        check("... and still last when descending", refs[-1] == "UTR-NOBODY", str(refs))
        _, refs = await page(sort="created", dir="asc")
        check("sort by created ascending puts the oldest first", refs[0] == "UTR-001", str(refs))
        _, refs = await page(sort="state", dir="asc")
        check("sort by state runs", len(refs) == 6)
        check("an unknown sort -> 422", (await c.get("/payments/search", headers=FA, params={"sort": "colour"})).status_code == 422)

        r = await c.get("/payments", headers=FA)
        check("GET /payments still answers a plain array", r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 6)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
