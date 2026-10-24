"""PO review: the name read from the purchase order can be remembered as an alias of the customer the reviewer picked.
Real database, no model, no mail. Proves: nothing is saved unless asked; the name is saved once (not again when it is
already an alias; not when it is the customer's own name); it needs customers.alias.confirm; a name that is another
customer's own name is refused and nothing at all is saved (the PO stays pending); the next PO under that name then
matches the customer by itself; invoices have no such option."""

import asyncio
import sys
import uuid

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, PendingInvoiceChange, SourceDocument
from data.models.postgres.enums import DocKind, PendingChangeReason, UploadedVia

s = get_settings()
assert "ar_poalias" in s.database_url, "refusing to run against a non-test database"
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def customer(name):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=name, contact="C", email=f"ap{_n[0]}@poalias.test", gstin=f"29ABCDE{_n[0]:04d}F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


async def po_review(number, name, by):
    """A PO the reader could not match to a customer: waiting for a person."""
    async with SessionLocal() as db:
        doc = SourceDocument(doc_kind=DocKind.PO, uploaded_via=UploadedVia.UPLOAD, file_name=f"{number}.pdf", file_hash=uuid.uuid4().hex, created_by=by)
        db.add(doc)
        await db.flush()
        item = PendingInvoiceChange(
            reason=PendingChangeReason.EXTRACTION_REVIEW, created_by=by, source_document_id=doc.id,
            proposed_snapshot={
                "kind": "purchase_order",
                "reasons": [f"No customer matches '{name}'"],
                "extraction": {"po_number": number, "po_date": "2026-10-01", "customer_name": name, "currency": "INR",
                               "total_amount": "1000.00", "line_items": [{"description": "Widgets", "quantity": "10", "unit_price": "100.00", "line_total": "1000.00"}],
                               "confidence": {}},
            },
        )
        db.add(item)
        await db.commit()
        return item.id


async def aliases(cid):
    return [r[0] for r in await q("select alias_value from customer_aliases where customer_id=:c order by alias_value", c=cid)]


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Ftm One", "ftm.poalias@example.com", "FTM"), ("Fa One", "fa.poalias@example.com", "FA")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FTM = {"Authorization": f"Bearer {(await login('ftm.poalias@example.com', 'pass12345')).json()['access_token']}"}
        FA = {"Authorization": f"Bearer {(await login('fa.poalias@example.com', 'pass12345')).json()['access_token']}"}
        ftm_id = (await q("select id from users where email='ftm.poalias@example.com'"))[0][0]
        fa_id = (await q("select id from users where email='fa.poalias@example.com'"))[0][0]
        approve = lambda rid, cid, H=FTM, **kw: c.post(f"/purchase-orders/reviews/{rid}/approve", headers=H, json={"reason": "checked", "customer_id": str(cid), **kw})

        acme = await customer("Acme Industries Pvt Ltd")
        other = await customer("Bharat Traders Ltd")

        # 1. not asked: nothing saved
        r1 = await po_review("PO-A1", "ACME Industries", ftm_id)
        r = await approve(r1, acme)
        check("approve without asking: 200 and no alias saved", r.status_code == 200 and r.json()["alias_saved"] is None and await aliases(acme) == [], r.text[:200])

        # 2. asked: the PO's name becomes the customer's alias
        r2 = await po_review("PO-A2", "  ACME   Industries  ", ftm_id)
        r = await approve(r2, acme, save_alias=True)
        check("approve with save_alias: 200 and the tidied name is saved", r.status_code == 200 and r.json()["alias_saved"] == "ACME Industries" and await aliases(acme) == ["ACME Industries"], r.text[:200])
        src = await q("select source::text from customer_aliases where customer_id=:c", c=acme)
        check("the alias is marked as a person's decision", src == [("human_review",)], str(src))
        diff = await q("select diff from pending_invoice_changes where id=:i", i=r2)
        check("the review item records which name was saved", diff[0][0].get("alias_saved") == "ACME Industries", str(diff))

        # 3. already an alias (even in other letter case): not saved again, still approved
        r3 = await po_review("PO-A3", "acme industries", ftm_id)
        r = await approve(r3, acme, save_alias=True)
        check("a name that is already an alias is not saved again; the PO is still approved", r.status_code == 200 and r.json()["alias_saved"] is None and await aliases(acme) == ["ACME Industries"], r.text[:200])

        # 4. the customer's own name: nothing to remember
        r4 = await po_review("PO-A4", "Acme  Industries Pvt Ltd", ftm_id)
        r = await approve(r4, acme, save_alias=True)
        check("the customer's own name is not saved as an alias", r.status_code == 200 and r.json()["alias_saved"] is None and await aliases(acme) == ["ACME Industries"], r.text[:200])

        # 5. another customer's own name: refused, and the PO stays pending (nothing saved)
        r5 = await po_review("PO-A5", "Bharat Traders Ltd", ftm_id)
        r = await approve(r5, acme, save_alias=True)
        pending = await q("select status::text from pending_invoice_changes where id=:i", i=r5)
        check("another customer's name -> 409, nothing saved, the PO stays pending", r.status_code == 409 and "Bharat Traders Ltd" in r.text and pending == [("Pending",)] and await aliases(acme) == ["ACME Industries"] and (await q("select count(*) from purchase_orders where po_number='PO-A5'"))[0][0] == 0, f"{r.status_code} {r.text[:160]} {pending}")

        # 6. the alias works: the next PO under that name needs no review
        async with SessionLocal() as db:
            from core.services.customers.resolution import resolve_customer
            m = await resolve_customer(db, gstin=None, name="ACME Industries", our_gstin=s.our_gstin, our_company_name=s.our_company_name)
        check("the next PO under that name matches the customer by alias", m.matched and m.customer.id == acme and m.method == "alias", f"{m.method} {m.issues}")

        # 7. permission: a Finance Associate does not hold customers.alias.confirm, so cannot ask for it (approving alone is fine)
        r6 = await po_review("PO-A6", "Acme Alt Name", fa_id)
        r = await approve(r6, acme, FA, save_alias=True)
        check("without customers.alias.confirm: asking to save an alias -> 403, nothing saved, the PO stays pending",
              r.status_code == 403 and await aliases(acme) == ["ACME Industries"] and (await q("select status::text from pending_invoice_changes where id=:i", i=r6)) == [("Pending",)], f"{r.status_code} {r.text[:120]}")
        r = await approve(r6, acme, FA)
        check("... and the same person can still approve the PO without it", r.status_code == 200 and r.json()["alias_saved"] is None, r.text[:160])


    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
