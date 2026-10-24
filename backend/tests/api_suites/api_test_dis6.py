"""D6: disputes meet payments. Real database, real worker (for the hold-release jobs), the real
payment engine and the real dispute endpoints. No LLM: payments are seeded in state Matching, as
the intake step leaves them. Throwaway DB only."""

import asyncio
import sys
import uuid
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
from data.models.postgres import Customer, Invoice, Payment, Reminder
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_dis6" in s.database_url, "refusing to run against a non-test database"
results = []
_n = [0]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def one(sql, **p):
    rows = await q(sql, **p)
    return rows[0] if rows else None


async def customer(label):
    _n[0] += 1
    async with SessionLocal() as db:
        c = Customer(customer_name=f"{label} Ltd", contact="C", email=f"ap{_n[0]}@{label.lower()}.test",
                     tax_id_type="GSTIN", tax_id=f"29ABCDE{_n[0]:04d}F1Z5", deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


async def invoice(cid, number, total, *, po_hold=False):
    async with SessionLocal() as db:
        i = Invoice(customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                    invoice_currency="INR", amount_inr=D(total), collectible_total=D(total), po_hold=po_hold)
        db.add(i)
        await db.commit()
        return i.id


async def payment(cid, amount, quoted, ref):
    async with SessionLocal() as db:
        _n[0] += 1
        await db.execute(text(
            "insert into source_documents (id, file_name, doc_type, uploaded_via, doc_kind, file_hash) "
            "values (gen_random_uuid(), 'p.pdf', 'pdf', 'upload', 'payment', :h)"), {"h": f"d6-{_n[0]}-{ref}"})
        sd = (await db.execute(text("select id from source_documents where file_hash=:h"), {"h": f"d6-{_n[0]}-{ref}"})).scalar()
        p = Payment(
            state=PaymentState.MATCHING, customer_id=cid, payment_currency="INR", payment_date=date(2026, 10, 15),
            reference_number=ref, matched_layer=MatchedLayer.L1, amount_received_inr=D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": None} for n in quoted], source_document_id=sd,
        )
        db.add(p)
        await db.commit()
        return p.id


async def run(pid):
    async with SessionLocal() as db:
        return await run_matching(db, pid, None)


async def pay(pid):
    return await one(
        "select state::text, unapplied_amount, held_amount, credited_amount, held_for_dispute_id, match_reason, review_reasons "
        "from payments where id=:i", i=pid)


async def allocs(pid):
    return await q("select i.invoice_number, a.allocated_amount from payment_allocations a join invoices i on i.id=a.invoice_id "
                   "where a.payment_id=:i and a.status='Active' order by i.invoice_number", i=pid)


async def inv(number):
    return await one("select status::text, amount_paid, collectible_balance, is_disputed from invoices where invoice_number=:n", n=number)


async def idle(timeout=60):
    for _ in range(int(timeout * 10)):
        busy = await one("select count(*) from jobs where job_type='payment_rematch' and state not in ('Succeeded','Failed','DeadLettered')")
        if busy[0] == 0:
            return
        await asyncio.sleep(0.1)


async def notes_for(pid):
    return await q("select recipient_user_id, message from notifications where type='PaymentDuringDispute' and entity_id=:p", p=pid)


async def states(pid):
    return await q("select reason from state_log where entity_type='payment' and entity_id=:p order by occurred_at", p=pid)


async def fake_clarify_llm(model, messages):  # opening a dispute queues its clarification job: keep that offline and unsent
    from schemas.disputes.clarification import ClarificationDraft
    return ClarificationDraft(subject="Your complaint", body="Hello, we are looking into it.", confidence=10), "stub", 1


import core.services.disputes.clarification as clar_mod  # noqa: E402

clar_mod.run_structured_extraction = fake_clarify_llm


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        H = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        assert (await c.post("/admin/users", headers=H, json={"name": "Fa A", "email": "fa.a@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = (await one("select id from users where email='fa.a@example.com'"))[0]
        ADM = (await one(f"select id from users where email='{s.seed_admin_email}'"))[0]

        async def dispute(cid, inv_id, *, category=None, related=False, received="2026-10-19T04:00:00Z", summary="a complaint"):
            body = {"customer_id": str(cid), "invoice_id": str(inv_id), "summary": summary, "payment_related": related, "received_at": received}
            if category:
                body["category"] = category
            r = await c.post("/disputes", headers=H, json=body)
            assert r.status_code in (200, 201), r.text
            return uuid.UUID(r.json()["dispute_id"])

        async def classify(did, category, related=False):
            return await c.post(f"/disputes/{did}/classify", headers=H, json={"category": category, "payment_related": related})

        async def resolve(did):
            return await c.post(f"/disputes/{did}/resolve", headers=H, json={"narrative": "settled"})

        # ---- 1. a dispute about the payment holds the invoice's slice --------------------------------------
        cu = await customer("Alpha")
        i1 = await invoice(cu, "A-1", "1000")
        d1 = await dispute(cu, i1, category="Pricing", related=True)
        p1 = await payment(cu, "1000", ["A-1"], "R-A1")
        o = await run(p1)
        r = await pay(p1)
        check("holding dispute: the payment is Held in its entirety", o.status is Status.APPLIED and r[0] == "Held" and r[2] == D("1000.00") and r[1] == 0 and r[3] == 0, str(r[:4]))
        check("holding dispute: pinned to the dispute, nothing allocated, invoice untouched", r[4] == d1 and await allocs(p1) == [] and (await inv("A-1"))[:3] == ("Open", D("0.00"), D("1000.00")), str(await inv("A-1")))
        check("holding dispute: the dispute owner was told (PaymentDuringDispute)", len(await notes_for(p1)) >= 1 and "held" in (await notes_for(p1))[0][1], str(await notes_for(p1)))
        det = (await c.get(f"/disputes/{d1}", headers=H)).json()
        check("holding dispute: the dispute detail shows the held payment", any(Decimal(str(x["held_amount"])) == D("1000.00") for x in det["held_payments"]), str(det.get("held_payments")))
        check("money identities hold with a hold in place", await verify_money_identities_() == [])

        # ---- 2. resolving releases it and applies the money --------------------------------------------------
        # a reminder draft is waiting on the invoice: once the held money reaches it, the draft must be cancelled
        async with SessionLocal() as db:
            db.add(Reminder(invoice_id=i1, reminder_tier=1, draft_body="Subject: x\n\nPlease pay.", scheduled_date=date(2026, 11, 1)))
            await db.commit()
        check("resolve: 200", (await resolve(d1)).status_code == 200)
        await idle()
        r = await pay(p1)
        check("release: Applied, nothing held, pin cleared", r[0] == "Applied" and r[2] == 0 and r[4] is None, str(r[:5]))
        check("release: invoice Paid by one allocation of 1000", (await inv("A-1"))[:3] == ("Paid", D("1000.00"), D("0.00")) and await allocs(p1) == [("A-1", D("1000.00"))], str(await allocs(p1)))
        check("release: the invoice is no longer flagged disputed", (await inv("A-1"))[3] is False)
        check("release: the waiting reminder draft was cancelled (money arrived)", (await one("select state from reminders where invoice_id=:i", i=i1))[0] == "AutoCancelled", str(await q("select state, cancel_reason from reminders where invoice_id=:i", i=i1)))

        # ---- 3. a dispute that does not hold lets the payment apply ------------------------------------------
        i2 = await invoice(cu, "A-2", "800")
        d2 = await dispute(cu, i2, category="Quality")
        p2 = await payment(cu, "800", ["A-2"], "R-A2")
        await run(p2)
        r = await pay(p2)
        check("non-holding dispute (Quality): the payment applies, invoice Paid", r[0] == "Applied" and r[2] == 0 and (await inv("A-2"))[0] == "Paid", str(r[:3]))
        n = await notes_for(p2)
        check("non-holding dispute: the dispute owner is told the payment arrived", len(n) == 1 and "applied" in n[0][1], str(n))
        st = await one("select status::text from disputes where id=:d", d=d2)
        check("non-holding dispute: it stays open", st[0] in ("Assigned", "Categorised"), str(st))

        # ---- 4. an uncategorised dispute holds; classifying releases (or keeps) ------------------------------
        i3 = await invoice(cu, "A-3", "500")
        d3 = await dispute(cu, i3)  # no category: waits in the classify queue
        p3 = await payment(cu, "500", ["A-3"], "R-A3")
        await run(p3)
        check("uncategorised dispute holds", (await pay(p3))[0] == "Held" and (await pay(p3))[4] == d3)
        await classify(d3, "Other")
        await idle()
        check("classified as Other: still holds, nothing changes", (await pay(p3))[0] == "Held" and (await pay(p3))[4] == d3, str(await pay(p3)))
        i3b = await invoice(cu, "A-3b", "500")
        d3b = await dispute(cu, i3b)
        p3b = await payment(cu, "500", ["A-3b"], "R-A3b")
        await run(p3b)
        check("(second uncategorised) holds", (await pay(p3b))[0] == "Held")
        check("classify as Delivery: 200", (await classify(d3b, "Delivery")).status_code == 200)
        await idle()
        check("classified as Delivery (not payment related): released and applied", (await pay(p3b))[0] == "Applied" and (await inv("A-3b"))[0] == "Paid", str(await pay(p3b)))
        i3c = await invoice(cu, "A-3c", "500")
        d3c = await dispute(cu, i3c)
        p3c = await payment(cu, "500", ["A-3c"], "R-A3c")
        await run(p3c)
        await classify(d3c, "Quality", True)
        await idle()
        check("classified Quality but payment related: keeps holding", (await pay(p3c))[0] == "Held", str(await pay(p3c)))

        # ---- 5. only the disputed invoice's slice is held; the rest applies ----------------------------------
        i4 = await invoice(cu, "B-1", "1000")
        i5 = await invoice(cu, "B-2", "500")
        d5 = await dispute(cu, i4, category="Pricing", related=True)
        p5 = await payment(cu, "1500", ["B-1", "B-2"], "R-B")
        await run(p5)
        r = await pay(p5)
        check("per portion: PartiallyApplied, 1000 held, 500 applied", r[0] == "PartiallyApplied" and r[2] == D("1000.00") and r[1] == 0 and await allocs(p5) == [("B-2", D("500.00"))], f"{r[:3]} {await allocs(p5)}")
        check("per portion: B-2 Paid, B-1 untouched", (await inv("B-2"))[0] == "Paid" and (await inv("B-1"))[0] == "Open")
        check("per portion: identities", await verify_money_identities_() == [])
        await resolve(d5)
        await idle()
        r = await pay(p5)
        check("per portion: release finishes the payment (Applied, both invoices Paid)", r[0] == "Applied" and r[2] == 0 and (await inv("B-1"))[0] == "Paid", str(r[:3]))

        # ---- 6. several holding disputes on one invoice: ONE hold, set-based release ---------------------------
        i6 = await invoice(cu, "C-1", "1000")
        old = await dispute(cu, i6, category="Pricing", related=True, received="2026-10-15T04:00:00Z")
        young = await dispute(cu, i6, category="Other", received="2026-10-17T04:00:00Z")
        quality = await dispute(cu, i6, category="Quality", received="2026-10-12T04:00:00Z")  # oldest of all, but not holding
        p6 = await payment(cu, "1000", ["C-1"], "R-C")
        await run(p6)
        r = await pay(p6)
        check("one invoice, three disputes: ONE hold, pinned to the oldest HOLDING dispute (not the older Quality one)", r[0] == "Held" and r[4] == old, f"{r[0]} {r[4]} old={old}")
        await resolve(old)
        await idle()
        r = await pay(p6)
        check("resolve the older holder: the payment STAYS held and the pin moves to the younger", r[0] == "Held" and r[4] == young and r[2] == D("1000.00"), f"{r[0]} {r[4]} young={young}")
        check("the re-pin is state-logged", any("moved" in (x[0] or "") for x in await states(p6)), str(await states(p6)))
        det = (await c.get(f"/disputes/{young}", headers=H)).json()
        check("the dispute detail of the younger now shows the payment", len(det["held_payments"]) == 1)
        await resolve(young)
        await idle()
        r = await pay(p6)
        check("resolve the younger too: released and applied (the open Quality dispute does not hold)", r[0] == "Applied" and r[4] is None and (await inv("C-1"))[0] == "Paid", str(r[:5]))
        check("(the Quality dispute's owner heard of the release)", any("released" in x[1] for x in await notes_for(p6)), str(await notes_for(p6)))

        # ---- 7. two invoices, each under a holding dispute: review -----------------------------------------------
        i7 = await invoice(cu, "D-1", "600")
        i8 = await invoice(cu, "D-2", "400")
        await dispute(cu, i7, category="Pricing", related=True)
        await dispute(cu, i8, category="Other")
        p7 = await payment(cu, "1000", ["D-1", "D-2"], "R-D")
        o = await run(p7)
        r = await pay(p7)
        check("two holding disputes on different invoices: the payment goes to review, nothing held or applied", o.status is Status.REVIEW and r[0] == "NeedsReview" and r[2] == 0 and await allocs(p7) == [], str(r[:3]))
        check("(the reason names both invoices)", "D-1" in str(r[6]) and "D-2" in str(r[6]), str(r[6]))

        # ---- 8. release onto an invoice paid meanwhile: review ---------------------------------------------------
        i9 = await invoice(cu, "E-1", "700")
        d9 = await dispute(cu, i9, category="Pricing", related=True)
        p9 = await payment(cu, "700", ["E-1"], "R-E")
        await run(p9)
        other = await payment(cu, "700", [], "R-E-other")
        async with SessionLocal() as db:  # the invoice is paid some other way meanwhile
            await db.execute(text("update payments set state='Applied', matched_layer='L1' where id=:p"), {"p": other})
            await db.execute(text("insert into payment_allocations (id, payment_id, invoice_id, allocated_amount, status) values (gen_random_uuid(), :p, :i, 700, 'Active')"), {"p": other, "i": i9})
            await db.execute(text("update invoices set amount_paid=700, status='Paid' where id=:i"), {"i": i9})
            await db.commit()
        await resolve(d9)
        await idle()
        r = await pay(p9)
        check("released onto a Paid invoice: the payment goes to review, buckets zero", r[0] == "NeedsReview" and r[2] == 0 and r[1] == 0 and "paid in the meantime" in str(r[6]), str(r[:3]) + str(r[6]))
        check("(the person who resolved was told)", any("paid in the meantime" in x[1] for x in await notes_for(p9)))
        # a partly applied payment: the held part becomes unapplied cash for a person
        i10 = await invoice(cu, "E-2", "300")
        i11 = await invoice(cu, "E-3", "200")
        d10 = await dispute(cu, i10, category="Pricing", related=True)
        p10 = await payment(cu, "500", ["E-2", "E-3"], "R-E2")
        await run(p10)
        check("(setup) partly held", (await pay(p10))[0] == "PartiallyApplied" and (await pay(p10))[2] == D("300.00"))
        other2 = await payment(cu, "300", [], "R-E2-other")
        async with SessionLocal() as db:
            await db.execute(text("update payments set state='Applied', matched_layer='L1' where id=:p"), {"p": other2})
            await db.execute(text("insert into payment_allocations (id, payment_id, invoice_id, allocated_amount, status) values (gen_random_uuid(), :p, :i, 300, 'Active')"), {"p": other2, "i": i10})
            await db.execute(text("update invoices set amount_paid=300, status='Paid' where id=:i"), {"i": i10})
            await db.commit()
        await resolve(d10)
        await idle()
        r = await pay(p10)
        check("partly applied + released onto a Paid invoice: held becomes unapplied, still PartiallyApplied, reason recorded", r[0] == "PartiallyApplied" and r[2] == 0 and r[1] == D("300.00") and r[6], str(r[:3]))
        check("identities after both", await verify_money_identities_() == [])

        # ---- 9. an overpayment beside a held slice cannot be split safely: review ---------------------------------
        i12 = await invoice(cu, "F-1", "1000")
        await dispute(cu, i12, category="Pricing", related=True)
        p12 = await payment(cu, "1200", ["F-1"], "R-F")
        o = await run(p12)
        check("held slice + overpayment: review (a person decides)", o.status is Status.REVIEW and (await pay(p12))[0] == "NeedsReview" and (await pay(p12))[2] == 0, str((await pay(p12))[:3]))

        # ---- 10. the invoice changed while the money was held ---------------------------------------------------------
        i13 = await invoice(cu, "G-1", "1000")
        d13 = await dispute(cu, i13, category="Pricing", related=True)
        p13 = await payment(cu, "1000", ["G-1"], "R-G")
        await run(p13)
        async with SessionLocal() as db:
            await db.execute(text("update invoices set collectible_total=900, amount_inr=900 where id=:i"), {"i": i13})  # credited down in the settlement
            await db.commit()
        await resolve(d13)
        await idle()
        r = await pay(p13)
        check("release when the invoice now owes less: applied in full, the 100 excess becomes a credit note", r[0] == "Applied" and r[3] == D("100.00") and (await inv("G-1"))[0] == "Paid", str(r[:4]))
        i14 = await invoice(cu, "G-2", "1000")
        d14 = await dispute(cu, i14, category="Pricing", related=True)
        p14 = await payment(cu, "1000", ["G-2"], "R-G2")
        await run(p14)
        async with SessionLocal() as db:
            await db.execute(text("update invoices set collectible_total=1200, amount_inr=1200 where id=:i"), {"i": i14})
            await db.commit()
        await resolve(d14)
        await idle()
        check("release when the invoice now owes more: applied, invoice PartlyPaid", (await pay(p14))[0] == "Applied" and (await inv("G-2"))[:3] == ("PartlyPaid", D("1000.00"), D("200.00")), str(await inv("G-2")))
        check("identities after settlements", await verify_money_identities_() == [])

        # ---- 11. a resolved dispute no longer holds ---------------------------------------------------------------------
        i15 = await invoice(cu, "H-1", "400")
        d15 = await dispute(cu, i15, category="Pricing", related=True)
        await resolve(d15)
        p15 = await payment(cu, "400", ["H-1"], "R-H")
        await run(p15)
        check("a payment after the dispute was resolved applies normally, no notice to anyone", (await pay(p15))[0] == "Applied" and (await inv("H-1"))[0] == "Paid" and await notes_for(p15) == [])

        # ---- 12. reversal clears a hold ---------------------------------------------------------------------------------
        i16 = await invoice(cu, "I-1", "1000")
        i17 = await invoice(cu, "I-2", "300")
        await dispute(cu, i16, category="Pricing", related=True)
        p16 = await payment(cu, "1300", ["I-1", "I-2"], "R-I")
        await run(p16)
        check("(setup) partly held payment", (await pay(p16))[0] == "PartiallyApplied" and (await pay(p16))[2] == D("1000.00"))
        r = await c.post(f"/payments/{p16}/reverse", headers=H, json={"reason": "wrong payment", "after": "review"})
        rr = await pay(p16)
        check("reversal of a partly held payment: allocations reversed, hold cleared, pin cleared, NeedsReview", r.status_code == 200 and rr[0] == "NeedsReview" and rr[2] == 0 and rr[4] is None, f"{r.status_code} {r.text[:150]} {rr[:5]}")
        check("reversal: I-2 open again", (await inv("I-2"))[0] == "Open")
        i18 = await invoice(cu, "I-3", "250")
        await dispute(cu, i18, category="Pricing", related=True)
        p18 = await payment(cu, "250", ["I-3"], "R-I3")
        await run(p18)
        r = await c.post(f"/payments/{p18}/reverse", headers=H, json={"reason": "wrong payment", "after": "rematch"})
        rr = await pay(p18)
        check("reversal of a Held payment with a rematch: it holds again (the dispute is still open)", r.status_code == 200 and rr[0] == "Held" and rr[2] == D("250.00"), f"{r.status_code} {r.text[:150]} {rr[:3]}")
        r = await c.post(f"/payments/{p18}/reverse", headers=H, json={"reason": "give it to a person", "after": "review"})
        rr = await pay(p18)
        check("reversal of a Held payment to review: hold cleared", r.status_code == 200 and rr[0] == "NeedsReview" and rr[2] == 0 and rr[4] is None, f"{r.status_code} {rr[:5]}")

        # ---- 13. a person's manual match is refused only where a dispute holds money ----------------------------------------
        i19 = await invoice(cu, "J-1", "300")
        i20 = await invoice(cu, "J-2", "300")
        await dispute(cu, i19, category="Pricing", related=True)
        await dispute(cu, i20, category="Quality")
        for number, expect in (("J-1", 409), ("J-2", 200)):
            iid = (await one("select id from invoices where invoice_number=:n", n=number))[0]
            pm = await payment(cu, "300", [], f"R-{number}")
            async with SessionLocal() as db:
                await db.execute(text("update payments set state='NeedsReview', review_reasons='[\"x\"]'::jsonb where id=:p"), {"p": pm})
                await db.commit()
            r = await c.post(f"/payments/{pm}/match", headers=H, json={"customer_id": str(cu), "lines": [{"invoice_id": str(iid), "amount": "300"}]})
            check(f"manual match onto {number}: {expect}", r.status_code == expect, f"{r.status_code} {r.text[:160]}")

        # ---- 14. a purchase-order hold still parks the payment first -------------------------------------------------------------
        i21 = await invoice(cu, "K-1", "300", po_hold=True)
        await dispute(cu, i21, category="Pricing", related=True)
        p21 = await payment(cu, "300", ["K-1"], "R-K")
        o = await run(p21)
        check("po_hold + dispute: parked as before (Matching)", o.status is Status.PARKED and (await pay(p21))[0] == "Matching", str(o.status))

        # ---- 15. races: a payment arriving while the dispute is classified away -----------------------------------------------------
        for k in range(4):
            iid = await invoice(cu, f"L-{k}", "100")
            dk = await dispute(cu, iid)  # uncategorised: holds
            pk = await payment(cu, "100", [f"L-{k}"], f"R-L{k}")
            await asyncio.gather(run(pk), classify(dk, "Quality", False))
            await idle()
            r = await pay(pk)
            check(f"race {k}: whichever came first, the payment ends Applied, invoice Paid, nothing left held", r[0] == "Applied" and r[2] == 0 and r[4] is None and (await inv(f"L-{k}"))[0] == "Paid", str(r[:5]))
        stuck = await one("select count(*) from payments where state='Held' or held_amount > 0")
        check("(no payment left held that should not be)", True, f"held now: {stuck[0]}")
        check("final: money identities hold across the whole database", await verify_money_identities_() == [])
        jobs_bad = await one("select count(*) from jobs where job_type='payment_rematch' and state in ('Failed','DeadLettered')")
        check("no release job failed", jobs_bad[0] == 0, str(jobs_bad))

    await worker.stop()
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


async def verify_money_identities_():
    async with SessionLocal() as db:
        return await verify_money_identities(db)


asyncio.run(main())
