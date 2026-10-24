"""P6: reversing a payment's application. Over HTTP for the endpoint, engine/DB seeding otherwise.
No LLM.
"""

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
assert "ar_pay6" in s.database_url, "refusing to run against a non-test database"
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def customer(label):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="C", email=f"ap{_n[0]}@{label.lower()}.test",
                     gstin=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=False, billing_currency="INR", country="India")
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


async def setpaid(number, amount):
    async with SessionLocal() as db:
        await db.execute(text("update invoices set amount_paid=:a where invoice_number=:n"), {"a": amount, "n": number})
        await db.commit()


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
        for name, email, role in (("Fa Two", "fa.two@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            assert (await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}
        rev = lambda pid, H=FTM, **kw: hc.post(f"/payments/{pid}/reverse", headers=H, json={"reason": "Applied to the wrong customer's invoice", **kw})

        # ---- 1. a plain reversal, left for a person --------------------------------------------
        c = await customer("Alpha")
        a1 = await invoice(c, "A-1", "900")
        a2 = await invoice(c, "A-2", "900", due=date(2026, 12, 31))
        p = await payment(c, "900", [("A-1", None)], ref="R-1")
        await run(p)
        assert (await inv("A-1"))[0] == "Paid"
        r = await rev(p, after="review")
        d = r.json()
        check("reverse (after=review): 200, payment waits for a person", r.status_code == 200 and d["state"] == "NeedsReview", r.text[:200])
        check("the invoice reopens with its money back", (await inv("A-1")) == ("Open", D("0.00"), D("900.00")), str(await inv("A-1")))
        check("the allocation is Reversed (kept for the record), not deleted", await allocs(p) == [("A-1", D("900.00"), "Reversed", "payment")], str(await allocs(p)))
        row = await pay(p)
        check("the payment holds no money while it waits, and says who reversed it", row[0] == "NeedsReview" and row[1] == 0 and row[2] == 0 and row[3] == 0 and "Ftm One" in row[5][0] and "wrong customer" in row[5][0], str(row))
        check("the response lists what was undone", d["reversed"] == [{"invoice_number": "A-1", "amount": "900.00", "funded_by": "payment", "note_number": None, "invoice_status_after": "Open"}], str(d["reversed"]))
        check("the INR amount is kept for the next match", row[4] == D("900.00"))
        tl = (await hc.get(f"/payments/{p}", headers=FA)).json()["timeline"]
        check("the payment's timeline shows the reversal", tl[-1]["from_state"] == "Applied" and tl[-1]["to_state"] == "NeedsReview" and "reversed" in tl[-1]["reason"].lower(), str(tl[-1]))
        log = await q("select reason from state_log where entity_type='invoice' and entity_id=(select id from invoices where invoice_number='A-1') order by occurred_at")
        check("the invoice's log shows the money taken back", any("taken back" in x[0] for x in log), str(log))
        # then a person puts it on the right invoice
        r = await hc.post(f"/payments/{p}/match", headers=FTM, json={"lines": [{"invoice_id": str(a2), "amount": "900"}]})
        check("then a reviewer applies it to the right invoice: A-2 Paid, A-1 stays Open", r.status_code == 200 and (await inv("A-2"))[0] == "Paid" and (await inv("A-1"))[0] == "Open", r.text[:160])
        check("allocations: the reversed one and the new one", [(x[0], x[2]) for x in await allocs(p)] == [("A-1", "Reversed"), ("A-2", "Active")], str(await allocs(p)))
        r = await rev(p, after="review")
        check("it can be reversed again after being re-applied", r.status_code == 200 and (await inv("A-2"))[0] == "Open")
        r = await rev(p)
        check("a payment already waiting in review cannot be reversed again -> 409", r.status_code == 409, r.text[:120])

        # ---- 2. rematch straight away ----------------------------------------------------------
        c = await customer("Beta")
        b1 = await invoice(c, "B-1", "900")
        p = await payment(c, "900", [("B-1", None)], ref="R-2")
        await run(p)
        r = await rev(p)  # default: rematch
        d = r.json()
        check("reverse (default rematch): the matcher runs again and applies it", r.status_code == 200 and d["state"] == "Applied" and [x["invoice_number"] for x in d["new_allocations"]] == ["B-1"], r.text[:240])
        check("the invoice is Paid once (not twice) after the re-match", (await inv("B-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("B-1")))
        check("history kept: one Reversed allocation and one Active", [(x[0], x[2]) for x in await allocs(p)] == [("B-1", "Reversed"), ("B-1", "Active")], str(await allocs(p)))

        # ---- 3. an overpayment's credit note is voided when unspent ---------------------------------
        c = await customer("Gamma")
        g1 = await invoice(c, "G-1", "900")
        p = await payment(c, "1000", [("G-1", None)], ref="R-3")
        await run(p)
        born = await q("select note_number, status::text from notes where linked_payment_id=:p", p=p)
        assert len(born) == 1 and born[0][1] == "Open" and (await pay(p))[3] == D("100.00")
        r = await rev(p, after="review")
        d = r.json()
        check("reversal voids the unspent credit note it created", r.status_code == 200 and d["voided_notes"] == [born[0][0]] and (await nt(born[0][0]))[0] == "Voided", r.text[:200])
        row = await pay(p)
        check("its credited amount returns to zero; invoice reopens", row[3] == 0 and (await inv("G-1"))[0] == "Open", str(row[:4]))
        nd = (await hc.get(f"/notes/{(await q('select id from notes where note_number=:n', n=born[0][0]))[0][0]}", headers=FA)).json()
        check("the voided note's timeline says why", nd["status"] == "Voided" and "reversed" in nd["timeline"][-1]["reason"].lower() and nd["money_identity_ok"], str(nd["timeline"][-1]))
        r = await hc.post(f"/notes/{nd['note_id']}/spend", headers=FTM, json={"invoice_id": str(g1), "reason": "x"})
        check("a Voided note can no longer be spent -> 409", r.status_code == 409, r.text[:100])

        # ---- 4. blocked: the credit it created was already used ----------------------------------
        c = await customer("Delta")
        d1 = await invoice(c, "D-1", "900")
        p = await payment(c, "1000", [("D-1", None)], ref="R-4")
        await run(p)
        d2 = await invoice(c, "D-2", "500")  # created after, so the overpayment had a single open invoice
        cn = (await q("select id, note_number from notes where linked_payment_id=:p", p=p))[0]
        r = await hc.post(f"/notes/{cn[0]}/spend", headers=FTM, json={"invoice_id": str(d2), "amount": "40", "reason": "use part of it"})
        assert r.status_code == 200
        before = (await pay(p), await allocs(p), await inv("D-1"), await nt(cn[1]))
        r = await rev(p, after="review")
        check("credit partly used -> 409 naming the note and 'already consumed'", r.status_code == 409 and cn[1] in r.text and "already consumed" in r.text, r.text[:240])
        after = (await pay(p), await allocs(p), await inv("D-1"), await nt(cn[1]))
        check("a blocked reversal changes nothing", before == after, f"{before} vs {after}")
        await hc.post(f"/notes/{cn[0]}/spend", headers=FTM, json={"invoice_id": str(d2), "reason": "the rest"})
        r = await rev(p, after="review")
        check("credit fully used -> still 409", r.status_code == 409 and "already consumed" in r.text, r.text[:160])

        # ---- 5. the spends its own instruction caused are undone, so a re-match cannot spend twice -------
        c = await customer("Eps")
        e1 = await invoice(c, "E-1", "900")
        await note(c, "CN-E-1", "300")
        p = await payment(c, "600", [("E-1", None)], want_credit=True, ref="R-5")
        await run(p)
        assert (await inv("E-1"))[0] == "Paid" and (await nt("CN-E-1"))[0] == "Closed"
        r = await rev(p, after="review")
        d = r.json()
        check("reversal also undoes the credit spend its instruction caused", r.status_code == 200 and d["credit_restored"] == ["CN-E-1"] and len(d["reversed"]) == 2, r.text[:260])
        check("the note reopens with its 300 back; the invoice is fully reopened", (await nt("CN-E-1")) == ("Open", D("300.00"), D("300.00")) and (await inv("E-1")) == ("Open", D("0.00"), D("900.00")), f"{await nt('CN-E-1')} {await inv('E-1')}")
        check("both allocations (payment and note-funded) are Reversed", sorted((x[2], x[3]) for x in await allocs(p)) == [("Reversed", "note"), ("Reversed", "payment")], str(await allocs(p)))
        p2 = await payment(c, "600", [("E-1", None)], want_credit=True, ref="R-5b")
        await run(p2)
        r = await rev(p2)  # rematch: the instruction spends the credit again, exactly once
        check("reverse + rematch spends the credit again exactly once (note Closed, never negative)", r.status_code == 200 and (await nt("CN-E-1")) == ("Closed", D("300.00"), D("0.00")) and (await inv("E-1"))[0] == "Paid", f"{await nt('CN-E-1')} {await inv('E-1')} {r.text[:120]}")

        # ---- 6. an invoice keeps its OTHER payments; manual spends are untouched -------------------------
        c = await customer("Zeta")
        z1 = await invoice(c, "Z-1", "1000")
        await note(c, "CN-Z-M", "100")
        pa = await payment(c, "400", [("Z-1", None)], ref="R-6a")
        await run(pa)
        pb = await payment(c, "600", [("Z-1", None)], ref="R-6b")
        await run(pb)
        assert (await inv("Z-1")) == ("Paid", D("1000.00"), D("0.00"))
        r = await rev(pa, after="review")
        check("reversing one of two payments leaves the invoice PartlyPaid with the other's money", r.status_code == 200 and (await inv("Z-1")) == ("PartlyPaid", D("600.00"), D("400.00")), str(await inv("Z-1")))
        r = await rev(pb, after="review")
        check("reversing the other as well reopens it completely", r.status_code == 200 and (await inv("Z-1")) == ("Open", D("0.00"), D("1000.00")), str(await inv("Z-1")))
        c = await customer("Zeta2")
        y1 = await invoice(c, "Y-1", "1000")
        cm = await note(c, "CN-Y-M", "100")
        assert (await hc.post(f"/notes/{cm}/spend", headers=FTM, json={"invoice_id": str(y1), "reason": "manual credit"})).status_code == 200
        py = await payment(c, "900", [("Y-1", None)], ref="R-6c")
        await run(py)
        assert (await inv("Y-1"))[0] == "Paid"
        r = await rev(py, after="review")
        check("a manual credit spend on the same invoice is NOT undone by the reversal", r.status_code == 200 and (await inv("Y-1")) == ("PartlyPaid", D("100.00"), D("900.00")) and (await nt("CN-Y-M")) == ("Closed", D("100.00"), D("0.00")), f"{await inv('Y-1')} {await nt('CN-Y-M')}")

        # ---- 7. several invoices, an unapplied remainder, an Unapplied payment, foreign ---------------------
        c = await customer("Eta")
        h1 = await invoice(c, "H-1", "400", due=date(2026, 10, 20))
        h2 = await invoice(c, "H-2", "500", due=date(2026, 10, 25))
        p = await payment(c, "900", [("H-1", None), ("H-2", None)], ref="R-7")
        await run(p)
        r = await rev(p, after="review")
        check("a two-invoice payment: both reopen, two reversed lines", r.status_code == 200 and (await inv("H-1"))[0] == "Open" and (await inv("H-2"))[0] == "Open" and len(r.json()["reversed"]) == 2, r.text[:200])
        c = await customer("Theta")
        t1 = await invoice(c, "T-1", "400")
        p = await payment(c, "1000", [("T-1", None), ("T-404", None)], ref="R-8")
        await run(p)
        assert (await pay(p))[0] == "PartiallyApplied" and (await pay(p))[1] == D("600.00")
        r = await rev(p, after="review")
        row = await pay(p)
        check("a PartiallyApplied payment: its unapplied part is cleared too", r.status_code == 200 and row[0] == "NeedsReview" and row[1] == 0 and (await inv("T-1"))[0] == "Open", str(row[:4]))
        c = await customer("Iota")
        pu = await payment(c, "800", [], ref="R-9", state=PaymentState.UNAPPLIED, unapplied="800", layer=MatchedLayer.L4)
        r = await rev(pu, after="review")
        row = await pay(pu)
        check("an Unapplied payment (no allocations) can be reopened for a decision", r.status_code == 200 and row[0] == "NeedsReview" and row[1] == 0 and r.json()["reversed"] == [], r.text[:160])
        c = await customer("Kappa")
        k1 = await invoice(c, "K-1", "83000", currency="USD", rate="83")
        p = await payment(c, "1000", [("K-1", None)], currency="USD", ref="R-10")
        await run(p)
        assert (await inv("K-1"))[0] == "Paid" and (await pay(p))[4] == D("83000.00")
        r = await rev(p, after="review")
        check("a foreign payment: the invoice reopens, the INR value is kept", r.status_code == 200 and (await inv("K-1"))[0] == "Open" and (await pay(p))[4] == D("83000.00"), r.text[:160])

        # ---- 8. refused states, permissions, validation --------------------------------------------
        c = await customer("Lambda")
        for label, state in (("NeedsReview", PaymentState.NEEDS_REVIEW), ("Rejected", PaymentState.REJECTED), ("Matching", PaymentState.MATCHING)):
            px = await payment(c, "10", [], ref=f"S-{label}", state=state)
            r = await rev(px)
            check(f"a {label} payment cannot be reversed -> 409", r.status_code == 409, str(r.status_code))
        check("an unknown payment -> 404", (await rev("00000000-0000-0000-0000-000000000000")).status_code == 404)
        c = await customer("Mu")
        m1 = await invoice(c, "M-1", "900")
        pm = await payment(c, "900", [("M-1", None)], ref="R-11")
        await run(pm)
        check("FA cannot reverse (needs payments.correction.rerun) -> 403", (await rev(pm, FA)).status_code == 403)
        check("no token -> 401", (await hc.post(f"/payments/{pm}/reverse", json={"reason": "x"})).status_code == 401)
        check("a reason is required -> 422", (await hc.post(f"/payments/{pm}/reverse", headers=FTM, json={"reason": " "})).status_code == 422)
        check("an unknown 'after' -> 422", (await rev(pm, after="delete")).status_code == 422)
        check("nothing happened to the payment in all those refusals", (await pay(pm))[0] == "Applied" and (await inv("M-1"))[0] == "Paid")
        r = await rev(pm, ADM, after="review")
        check("ADM can reverse", r.status_code == 200 and "Admin" in (await pay(pm))[5][0], r.text[:120])

        # ---- 9. two people reverse at once ------------------------------------------------------------
        c = await customer("Nu")
        n1 = await invoice(c, "N-1", "900")
        pn = await payment(c, "900", [("N-1", None)], ref="R-12")
        await run(pn)
        r1, r2 = await asyncio.gather(rev(pn, FTM, after="review"), rev(pn, ADM, after="review"))
        codes = sorted([r1.status_code, r2.status_code])
        check("two simultaneous reversals: one 200, one 409", codes == [200, 409], str(codes))
        check("the money was taken back exactly once (never below zero)", (await inv("N-1")) == ("Open", D("0.00"), D("900.00")), str(await inv("N-1")))

        # ---- 10. an invoice whose paid total no longer covers the payment: a clear 409, nothing changed -----
        c = await customer("Xi")
        x1 = await invoice(c, "X-1", "900")
        px = await payment(c, "900", [("X-1", None)], ref="R-13")
        await run(px)
        await setpaid("X-1", 500)  # changed some other way
        r = await rev(px, after="review")
        check("invoice paid total below the payment's allocation: 409 (not a server error), says why", r.status_code == 409 and "X-1" in r.text and "Nothing was changed" in r.text, f"{r.status_code} {r.text[:200]}")
        check("... and nothing changed: the payment is still Applied with its allocation Active", (await pay(px))[0] == "Applied" and await allocs(px) == [("X-1", D("900.00"), "Active", "payment")], str(await allocs(px)))
        await setpaid("X-1", 900)  # put it back for the money check

        # ---- the whole book ----------------------------------------------------------------------------
        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("money identities hold after every reversal", found == [], str([str(v) for v in found][:5]))
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
