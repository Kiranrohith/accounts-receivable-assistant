"""Decisions 68 + 69: the over-payment bypass is L1-only (a quoted invoice number auto-applies, Paid + credit note, even
at 83). An unquoted over-payment from an identified customer is Unapplied cash in every case (the unapplied queue is the
review surface); the reviewer's apply-with-overage or convert action mints the note. No LLM."""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.payments.engine import Status, run_matching
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Note, Payment
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, NoteStatus, PaymentState

D = Decimal
s = get_settings()
assert "ar_pay8" in s.database_url, "refusing to run against a non-test database"
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def customer(label):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="C", email=f"ap{_n[0]}@{label.lower()}.test",
                     tax_id_type="GSTIN", tax_id=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


async def invoice(cid, number, collectible, *, due=date(2026, 10, 31), currency="INR", rate=None):
    async with SessionLocal() as db:
        inv = Invoice(
            customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=due,
            invoice_currency=currency, amount_inr=D(collectible), collectible_total=D(collectible),
            amount_foreign=None if currency == "INR" else D(collectible) / D(rate),
            fx_rate_invoice=None if currency == "INR" else D(rate),
        )
        db.add(inv)
        await db.commit()
        return inv.id


async def note(cid, number, amount):
    async with SessionLocal() as db:
        n = Note(note_number=number, customer_id=cid, amount=D(amount), remaining_amount=D(amount), status=NoteStatus.OPEN, reason="seed")
        db.add(n)
        await db.commit()
        return n.id


async def payment(cid, amount, quoted, *, want_credit=False, ref=None, currency="INR", layer=MatchedLayer.L1, state=PaymentState.MATCHING, unapplied=None):
    async with SessionLocal() as db:
        p = Payment(
            state=state, customer_id=cid, payment_currency=currency, payment_date=date(2026, 10, 15),
            reference_number=ref, matched_layer=layer,
            amount_received_inr=D(amount) if currency == "INR" else None,
            amount_received_foreign=None if currency == "INR" else D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": a} for n, a in quoted],
            credit_note_requested=want_credit,
            unapplied_amount=D(unapplied) if unapplied else D("0"),
        )
        db.add(p)
        await db.commit()
        return p.id


async def run(pid):
    async with SessionLocal() as db:
        return await run_matching(db, pid, None)


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def inv(number):
    return (await q("select status::text, amount_paid, collectible_balance from invoices where invoice_number=:n", n=number))[0]


async def nt(number):
    return (await q("select status::text, amount, remaining_amount from notes where note_number=:n", n=number))[0]


async def pay(pid):
    return (await q("select state::text, unapplied_amount, held_amount, credited_amount, amount_received_inr, review_reasons, match_reason from payments where id=:i", i=pid))[0]


async def allocs(pid):
    """(invoice, amount, status, funded-by) for the payment's allocations and the spends it caused."""
    return await q("select i.invoice_number, a.allocated_amount, a.status::text, case when a.note_id is null then 'payment' else 'note' end "
                   "from payment_allocations a join invoices i on i.id=a.invoice_id where a.payment_id=:p or a.spent_for_payment_id=:p order by a.created_at, i.invoice_number", p=pid)


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await hc.post("/admin/users", headers=ADM, json={"name": "Ftm One", "email": "ftm.one@example.com", "password": "pass12345", "role": "FTM"})).status_code == 201
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}

        # ---- 1. L1: the customer quoted our invoice number, two open invoices, score 83 -> bypass --------------
        c = await customer("Alpha")
        await invoice(c, "A-1", "900")
        await invoice(c, "A-2", "800", due=date(2027, 3, 1))
        p = await payment(c, "1000", [("A-1", None)], ref="R-A1")
        o = await run(p)
        row = await pay(p)
        check("L1 over at 83 (two open invoices): applied with no human", o.status is Status.APPLIED and row[0] == "Applied", f"{o.status} {row[0]}")
        check("A-1 Paid, 100 credited, one note minted", (await inv("A-1"))[0] == "Paid" and row[3] == D("100.00") and len(await q("select 1 from notes where customer_id=(select customer_id from payments where id=:p)", p=p)) == 1, str(row[:4]))
        check("the score is the documented 83 and the reason says why", (await q("select confidence_score from payments where id=:p", p=p))[0][0] == 83 and "quoted invoice number" in row[6], row[6])
        check("the other open invoice is untouched", (await inv("A-2"))[0] == "Open")

        # ---- 2. decision 69: an unquoted over-payment from an identified customer is UNAPPLIED cash, always ----
        c = await customer("Beta")
        b1 = await invoice(c, "B-1", "900")
        p = await payment(c, "1000", [], layer=MatchedLayer.L2)
        o = await run(p)
        row = await pay(p)
        check("L2 over, ONE open invoice, nothing quoted: Unapplied (not review, not applied)", row[0] == "Unapplied" and (await inv("B-1"))[0] == "Open", f"{o.status} {row[0]}")
        check("all 1000 sits in unapplied_amount (this is what reminder suppression reads), nothing credited, no note", row[1] == D("1000.00") and row[2] == 0 and row[3] == 0 and await q("select 1 from notes where customer_id=:c", c=c) == [], str(row[:4]))
        r = await hc.get("/payments", headers=FTM, params={"state": "Unapplied"})
        check("it is visible in the unapplied queue", r.status_code == 200 and str(p) in r.text, r.text[:160])

        c = await customer("Gamma")
        await invoice(c, "G-1", "900")
        await invoice(c, "G-2", "800", due=date(2027, 3, 1))
        p = await payment(c, "1000", [], layer=MatchedLayer.L3)
        await run(p)
        row = await pay(p)
        check("L3 over, two open invoices, nothing quoted: Unapplied", row[0] == "Unapplied" and row[1] == D("1000.00") and row[3] == 0, str(row[:4]))

        # ---- 3. the reviewer's Apply-to-invoice with the overage mints the note (the human confirmation) -------
        d_c = await customer("Delta")
        d1 = await invoice(d_c, "D-1", "900")
        p = await payment(d_c, "1000", [], layer=MatchedLayer.L2)
        await run(p)
        assert (await pay(p))[0] == "Unapplied"
        r = await hc.post(f"/payments/{p}/match", headers=FTM, json={"lines": [{"invoice_id": str(d1), "amount": "1000"}]})
        j = r.json()
        row = await pay(p)
        check("reviewer applies to the invoice with the overage: 200, invoice Paid, credit note number returned", r.status_code == 200 and (await inv("D-1"))[0] == "Paid" and j.get("credit_note_number"), r.text[:200])
        check("payment Applied, 100 credited, nothing left unapplied", row[0] == "Applied" and row[3] == D("100.00") and row[1] == 0, str(row[:4]))

        # ---- 4. a payment exceeding ALL open invoices: Unapplied, then Convert-to-credit-note for the full amount ---
        e_c = await customer("Epsilon")
        await invoice(e_c, "E-1", "900")
        p = await payment(e_c, "5000", [], layer=MatchedLayer.L2)
        await run(p)
        row = await pay(p)
        check("exceeds every open invoice: Unapplied with the full 5000", row[0] == "Unapplied" and row[1] == D("5000.00"), str(row[:4]))
        r = await hc.post(f"/payments/{p}/convert-unapplied", headers=FTM, json={"reason": "customer over-paid; confirmed"})
        row = await pay(p)
        check("convert action: a credit note for the full 5000, payment Applied, unapplied 0", r.status_code == 200 and row[0] == "Applied" and row[3] == D("5000.00") and row[1] == 0, r.text[:200])

        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("money identities hold after the whole run", not found, str(found)[:200])

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
