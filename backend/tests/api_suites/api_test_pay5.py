"""P5: credit notes. Spending on a remittance's instruction (through the matcher), manual notes,
manual spends and unapplied-to-note conversion, over HTTP where there is an endpoint. No LLM.
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
assert "ar_pay5" in s.database_url, "refusing to run against a non-test database"
results = []
_n = [0]
_note_seq = [0]


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


async def invoice(cid, number, collectible, *, due=date(2026, 10, 31), po_hold=False, status=InvoiceStatus.OPEN, paid="0"):
    async with SessionLocal() as db:
        inv = Invoice(
            customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=due,
            invoice_currency="INR", amount_inr=D(collectible), collectible_total=D(collectible),
            po_hold=po_hold, status=status, amount_paid=D(paid),
        )
        db.add(inv)
        await db.commit()
        return inv.id


async def note(cid, number, amount, *, remaining=None, status=NoteStatus.OPEN):
    async with SessionLocal() as db:
        n = Note(note_number=number, customer_id=cid, amount=D(amount),
                 remaining_amount=D(amount if remaining is None else remaining), status=status, reason="seed")
        db.add(n)
        await db.commit()
        # an old note's remaining must be backed by a spend, or the money check rightly complains
        if remaining is not None and D(remaining) < D(amount):
            used = D(amount) - D(remaining)
            inv_id = await invoice(cid, f"SEED-{number}", str(used), status=InvoiceStatus.PAID, paid=str(used))
            await db.execute(text("insert into payment_allocations (id, note_id, invoice_id, allocated_amount, status) values (gen_random_uuid(), :n, :i, :a, 'Active')"), {"n": n.id, "i": inv_id, "a": used})
            await db.commit()
        return n.id


async def payment(cid, amount, quoted, *, note_ref=None, want_credit=False, ref=None, layer=MatchedLayer.L1):
    async with SessionLocal() as db:
        p = Payment(
            state=PaymentState.MATCHING, customer_id=cid, payment_currency="INR", payment_date=date(2026, 10, 15),
            reference_number=ref, matched_layer=layer, amount_received_inr=D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": a} for n, a in quoted],
            extracted_credit_note_ref=note_ref, credit_note_requested=want_credit,
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
    return (await q("select state::text, unapplied_amount, credited_amount, amount_received_inr, match_reason, review_reasons from payments where id=:i", i=pid))[0]


async def spends(number):
    return await q("select i.invoice_number, a.allocated_amount, a.payment_id, a.tolerance_residual, a.status::text from payment_allocations a join invoices i on i.id=a.invoice_id join notes n on n.id=a.note_id where n.note_number=:n order by a.created_at", n=number)


async def main():
    # ===================================================== spending on the advice's instruction
    c = await customer("Alpha")
    await invoice(c, "A-1", "900")
    await note(c, "CN-T-1", "300")
    p = await payment(c, "600", [("A-1", None)], want_credit=True, ref="R1")
    o = await run(p)
    r = await pay(p)
    check("generic instruction: 600 paid + 300 credit settles a 900 invoice", o.status is Status.APPLIED and (await inv("A-1")) == ("Paid", D("900.00"), D("0.00")), f"{o.status} {await inv('A-1')}")
    check("the note is Closed, remaining 0", (await nt("CN-T-1")) == ("Closed", D("300.00"), D("0.00")), str(await nt("CN-T-1")))
    sp = await spends("CN-T-1")
    check("a note-funded allocation: no payment, no residual, Active", len(sp) == 1 and sp[0][0] == "A-1" and sp[0][1] == D("300.00") and sp[0][2] is None and sp[0][3] is None and sp[0][4] == "Active", str(sp))
    check("the payment itself paid 600 (credit spent is separate from its money)", r[0] == "Applied" and r[3] == D("600.00") and r[1] == 0 and r[2] == 0, str(r[:4]))
    check("the explanation names the note", "CN-T-1" in r[4] and "instructed" in r[4], r[4][:200])
    check("the match step reports the spend", o.credit_spent == [{"note_number": "CN-T-1", "amount": "300.00", "invoice_number": "A-1"}], str(o.credit_spent))

    c = await customer("Beta")
    await invoice(c, "B-1", "900")
    for number, amt in (("CN-B-1", "200"), ("CN-B-2", "350"), ("CN-B-3", "500")):
        await note(c, number, amt)
    p = await payment(c, "600", [("B-1", None)], want_credit=True, ref="R2")
    await run(p)
    check("best fit: the smallest note that covers the 300 left is CN-B-2; it keeps 50", (await nt("CN-B-2")) == ("PartiallyUsed", D("350.00"), D("50.00")) and (await nt("CN-B-1"))[0] == "Open" and (await nt("CN-B-3"))[0] == "Open" and (await inv("B-1"))[0] == "Paid", f"{await nt('CN-B-2')}")

    c = await customer("Gamma")
    await invoice(c, "G-1", "1000")
    for number, amt in (("CN-G-1", "500"), ("CN-G-2", "300"), ("CN-G-3", "200")):
        await note(c, number, amt)
    p = await payment(c, "300", [("G-1", None)], want_credit=True, ref="R3")
    await run(p)
    check("chain: nothing covers 700, so largest first: CN-G-1 500 then CN-G-2 200", (await nt("CN-G-1")) == ("Closed", D("500.00"), D("0.00")) and (await nt("CN-G-2")) == ("PartiallyUsed", D("300.00"), D("100.00")) and (await nt("CN-G-3"))[0] == "Open" and (await inv("G-1"))[0] == "Paid", f"{await nt('CN-G-1')} {await nt('CN-G-2')}")

    c = await customer("Delta")
    await invoice(c, "D-1", "1000")
    await note(c, "CN-D-1", "100")
    await note(c, "CN-D-2", "50")
    p = await payment(c, "300", [("D-1", None)], want_credit=True, ref="R4")
    await run(p)
    check("notes run out: invoice stays PartlyPaid (300 + 150 of 1000)", (await inv("D-1")) == ("PartlyPaid", D("450.00"), D("550.00")) and (await nt("CN-D-1"))[0] == "Closed" and (await nt("CN-D-2"))[0] == "Closed", str(await inv("D-1")))

    c = await customer("Eps")
    await invoice(c, "E-1", "900")
    await note(c, "CN-E-1", "900")  # an exact fit, but the advice quotes the other one
    await note(c, "CN-E-2", "400")
    p = await payment(c, "600", [("E-1", None)], note_ref="CN-E-2", ref="R5")
    await run(p)
    check("specific: the quoted CN-E-2 is used (300 of its 400), the better-fitting CN-E-1 is untouched", (await nt("CN-E-2")) == ("PartiallyUsed", D("400.00"), D("100.00")) and (await nt("CN-E-1"))[0] == "Open" and (await inv("E-1"))[0] == "Paid", f"{await nt('CN-E-2')}")

    c = await customer("Zeta")
    await invoice(c, "Z-1", "1000")
    await note(c, "CN-Z-1", "120")
    p = await payment(c, "500", [("Z-1", None)], note_ref="cn-z-1", ref="R6")
    await run(p)
    check("specific (case-insensitive), only as far as the note goes: 500 + 120 of 1000, PartlyPaid", (await inv("Z-1")) == ("PartlyPaid", D("620.00"), D("380.00")) and (await nt("CN-Z-1"))[0] == "Closed", str(await inv("Z-1")))

    # ---- instructions that cannot be followed -> review, nothing applied
    c = await customer("Eta")
    c2 = await customer("EtaOther")
    await invoice(c, "T-1", "900")
    await note(c2, "CN-OTHER", "500")
    await note(c, "CN-T-VOID", "500", status=NoteStatus.VOIDED)
    for label, ref in (("an unknown number", "CN-NOPE"), ("another customer's note", "CN-OTHER"), ("a Voided note", "CN-T-VOID")):
        p = await payment(c, "600", [("T-1", None)], note_ref=ref, ref=f"RV-{ref}")
        o = await run(p)
        r = await pay(p)
        check(f"specific instruction naming {label}: review, nothing applied", o.status is Status.REVIEW and r[0] == "NeedsReview" and (await inv("T-1"))[0] == "Open", f"{o.status} {r[5]}")
    p = await payment(c, "600", [("T-1", None)], want_credit=True, ref="RV-GEN")
    o = await run(p)
    r = await pay(p)
    check("generic instruction but no open note at all: review, nothing applied", o.status is Status.REVIEW and "no open credit note" in r[5][0] and (await inv("T-1"))[0] == "Open" and r[1] == 0, str(r[5]))

    # ---- no instruction: nothing spent, the credit is mentioned
    c = await customer("Theta")
    await invoice(c, "H-1", "900")
    await note(c, "CN-H-1", "300")
    p = await payment(c, "600", [("H-1", None)], ref="R7")
    o = await run(p)
    r = await pay(p)
    check("no instruction: nothing spent, invoice PartlyPaid with the credit hint", o.status is Status.APPLIED and (await inv("H-1")) == ("PartlyPaid", D("600.00"), D("300.00")) and (await nt("CN-H-1")) == ("Open", D("300.00"), D("300.00")) and "CN-H-1" in r[4] and "did not say to use" in r[4], r[4][:220])

    # ---- an instruction the payment does not need
    c = await customer("Iota")
    await invoice(c, "I-1", "900")
    await note(c, "CN-I-1", "300")
    p = await payment(c, "900", [("I-1", None)], want_credit=True, ref="R8")
    await run(p)
    check("the payment already pays in full: the credit is left alone", (await inv("I-1"))[0] == "Paid" and (await nt("CN-I-1")) == ("Open", D("300.00"), D("300.00")))

    # ---- several invoices share one note, oldest first
    c = await customer("Kappa")
    await invoice(c, "K-1", "400", due=date(2026, 10, 20))
    await invoice(c, "K-2", "400", due=date(2026, 10, 25))
    await note(c, "CN-K-1", "500")
    p = await payment(c, "300", [("K-1", None), ("K-2", None)], want_credit=True, ref="R9")
    await run(p)
    # 300 pays K-1 (400) up to 300 -> leftover K-1 100; K-2 untouched (the short fill stops at K-1) ... then the note covers K-1's 100
    check("leftover of the invoice the payment reached is paid from the note (K-1 Paid, note keeps 400)", (await inv("K-1"))[0] == "Paid" and (await nt("CN-K-1")) == ("PartiallyUsed", D("500.00"), D("400.00")), f"{await inv('K-1')} {await nt('CN-K-1')}")

    # ---- two payments racing for one note
    c = await customer("Lambda")
    await invoice(c, "L-1", "900")
    await invoice(c, "L-2", "900")
    await note(c, "CN-L-1", "300")
    p1 = await payment(c, "600", [("L-1", None)], want_credit=True, ref="RACE-A")
    p2 = await payment(c, "600", [("L-2", None)], want_credit=True, ref="RACE-B")
    o1, o2 = await asyncio.gather(run(p1), run(p2))
    got = sorted([o1.status.value, o2.status.value])
    check("two payments both asking for the same single note: one spends it, the other goes to review", got == ["applied", "review"], str(got))
    check("the note was spent exactly once", (await nt("CN-L-1")) == ("Closed", D("300.00"), D("0.00")) and len(await spends("CN-L-1")) == 1, str(await nt("CN-L-1")))

    # ===================================================== the endpoints
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa Two", "fa.two@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            assert (await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}

        c = await customer("Mu")
        other = await customer("MuOther")
        m1 = await invoice(c, "M-1", "1000")
        m2 = await invoice(c, "M-2", "300")
        o1_ = await invoice(other, "MO-1", "100")
        mh = await invoice(c, "M-H", "100", po_hold=True)
        mp = await invoice(c, "M-P", "100", status=InvoiceStatus.PAID, paid="100")
        async with SessionLocal() as db:
            pr = Payment(state=PaymentState.APPLIED, customer_id=c, payment_currency="INR", amount_received_inr=D("100"), reference_number="PRIOR-MP", matched_layer=MatchedLayer.L1)
            db.add(pr)
            await db.flush()
            await db.execute(text("insert into payment_allocations (id, payment_id, invoice_id, allocated_amount, status) values (gen_random_uuid(), :p, :i, 100, 'Active')"), {"p": pr.id, "i": mp})
            await db.commit()

        body = {"customer_id": str(c), "amount": "500", "reason": "Goodwill for the late delivery"}
        check("create a note: FA -> 403 (needs notes.create)", (await hc.post("/notes", headers=FA, json=body)).status_code == 403)
        check("create a note: no token -> 401", (await hc.post("/notes", json=body)).status_code == 401)
        r = await hc.post("/notes", headers=FTM, json=body)
        d = r.json()
        check("FTM creates a note: 201, Open, 500, a CN number", r.status_code == 201 and d["status"] == "Open" and D(str(d["remaining_amount"])) == 500 and d["note_number"].startswith("CN-2026-") and d["customer_name"] == "Mu Ltd", r.text[:200])
        N = d["note_id"]
        check("the new note's detail: no spends, timeline has its creation, money identity ok", d["spends"] == [] and d["timeline"][0]["to_state"] == "Open" and d["money_identity_ok"] and "Ftm One" in d["reason"], str(d["timeline"]))
        for name, b, code in (
            ("zero amount", {**body, "amount": "0"}, 422),
            ("no reason", {**body, "reason": ""}, 422),
            ("unknown customer", {**body, "customer_id": "00000000-0000-0000-0000-000000000009"}, 404),
            ("an invoice of another customer", {**body, "linked_invoice_id": str(o1_)}, 422),
        ):
            check(f"create refused: {name}", (await hc.post("/notes", headers=FTM, json=b)).status_code == code)
        r = await hc.post("/notes", headers=FTM, json={**body, "amount": "50", "linked_invoice_id": str(m2)})
        check("a note linked to the customer's own invoice", r.status_code == 201 and r.json()["linked_invoice_id"] == str(m2) and r.json()["linked_invoice_number"] == "M-2", r.text[:140])

        # listing
        r = await hc.get("/notes", headers=FA)
        check("FA can view the list (payments.view)", r.status_code == 200 and len(r.json()) >= 2)
        check("filter by customer and status", all(x["customer_id"] == str(c) for x in (await hc.get(f"/notes?customer_id={c}&status=Open", headers=FA)).json()) and len((await hc.get(f"/notes?customer_id={c}&status=Closed", headers=FA)).json()) == 0)
        check("search by note number", [x["note_id"] for x in (await hc.get(f"/notes?search={d['note_number']}", headers=FA)).json()] == [N])
        check("unknown note -> 404", (await hc.get("/notes/00000000-0000-0000-0000-000000000000", headers=FA)).status_code == 404)

        # spend
        sp = lambda **kw: hc.post(f"/notes/{N}/spend", headers=FTM, json={"reason": "Applying the goodwill credit", **kw})
        check("spend: FA -> 403", (await hc.post(f"/notes/{N}/spend", headers=FA, json={"invoice_id": str(m1), "reason": "x"})).status_code == 403)
        check("spend on another customer's invoice -> 422", (await sp(invoice_id=str(o1_))).status_code == 422)
        check("spend on an invoice on PO hold -> 409", (await sp(invoice_id=str(mh))).status_code == 409)
        check("spend on a Paid invoice -> 409", (await sp(invoice_id=str(mp))).status_code == 409)
        check("spend more than the note holds -> 422", (await sp(invoice_id=str(m1), amount="900")).status_code == 422)
        check("spend more than the invoice owes -> 422", (await sp(invoice_id=str(m2), amount="400")).status_code == 422)
        check("an unknown invoice -> 404", (await sp(invoice_id="00000000-0000-0000-0000-000000000008")).status_code == 404)
        r = await sp(invoice_id=str(m1), amount="200")
        check("spend 200 on M-1: PartlyPaid, note PartiallyUsed with 300 left", r.status_code == 200 and r.json()["invoice_status"] == "PartlyPaid" and r.json()["note_status"] == "PartiallyUsed" and D(str(r.json()["note_remaining"])) == 300 and D(str(r.json()["invoice_balance"])) == 800, r.text[:200])
        r = await sp(invoice_id=str(m2))
        check("spend with no amount: the most that fits (the invoice owes 300, the note has 300 left): Paid, note Closed", r.status_code == 200 and D(str(r.json()["spent"])) == 300 and r.json()["invoice_status"] == "Paid" and r.json()["note_status"] == "Closed", r.text[:200])
        r = await sp(invoice_id=str(m1))
        check("a Closed note cannot be spent again -> 409", r.status_code == 409, r.text[:100])
        d = (await hc.get(f"/notes/{N}", headers=FA)).json()
        check("note detail lists both spends and the timeline Open -> PartiallyUsed -> Closed", [x["invoice_number"] for x in d["spends"]] == ["M-1", "M-2"] and [t["to_state"] for t in d["timeline"]] == ["Open", "PartiallyUsed", "Closed"] and d["money_identity_ok"], f"{[x['invoice_number'] for x in d['spends']]} {[t['to_state'] for t in d['timeline']]}")
        inv_d = (await hc.get(f"/invoices/{m1}", headers=FA)).json()
        check("the invoice detail shows the credit-note payment", any(x["funded_by"] == "credit_note" and x["note_number"] == d["note_number"] for x in inv_d["payments"]), str(inv_d["payments"]))

        # two people spend the same note at once
        c3 = await customer("Nu")
        n1 = await invoice(c3, "N-1", "900")
        n2 = await invoice(c3, "N-2", "900")
        r = await hc.post("/notes", headers=FTM, json={"customer_id": str(c3), "amount": "500", "reason": "race"})
        RN = r.json()["note_id"]
        a, b = await asyncio.gather(
            hc.post(f"/notes/{RN}/spend", headers=FTM, json={"invoice_id": str(n1), "amount": "400", "reason": "a"}),
            hc.post(f"/notes/{RN}/spend", headers=ADM, json={"invoice_id": str(n2), "amount": "400", "reason": "b"}),
        )
        codes = sorted([a.status_code, b.status_code])
        rem = (await nt((await hc.get(f"/notes/{RN}", headers=FA)).json()["note_number"]))
        check("two spends of 400 from a 500 note at once: one 200, one 422; the note never goes negative", codes == [200, 422] and rem[2] == D("100.00"), f"{codes} {rem}")

        # convert unapplied
        c4 = await customer("Xi")
        async with SessionLocal() as db:
            up = Payment(state=PaymentState.UNAPPLIED, customer_id=c4, payment_currency="INR", amount_received_inr=D("800"), unapplied_amount=D("800"), reference_number="UTR-CONV", matched_layer=MatchedLayer.L4)
            db.add(up)
            await db.commit()
            UP = up.id
        r = await hc.post(f"/payments/{UP}/convert-unapplied", headers=FA, json={"reason": "x"})
        check("convert: FA -> 403", r.status_code == 403)
        r = await hc.post(f"/payments/{UP}/convert-unapplied", headers=FTM, json={"amount": "900", "reason": "too much"})
        check("convert more than is unapplied -> 422", r.status_code == 422)
        r = await hc.post(f"/payments/{UP}/convert-unapplied", headers=FTM, json={"amount": "300", "reason": "customer asked for credit"})
        row = await pay(UP)
        check("convert 300: payment still Unapplied with 500 unapplied and 300 credited", r.status_code == 200 and row[0] == "Unapplied" and row[1] == D("500.00") and row[2] == D("300.00"), r.text[:200])
        check("the note is linked to the payment, Open, 300", len(await q("select 1 from notes where linked_payment_id=:p and amount=300 and status='Open'", p=UP)) == 1)
        r = await hc.post(f"/payments/{UP}/convert-unapplied", headers=FTM, json={"reason": "the rest"})
        row = await pay(UP)
        check("convert the rest: payment becomes Applied (everything disposed of), unapplied 0, credited 800", r.status_code == 200 and row[0] == "Applied" and row[1] == 0 and row[2] == D("800.00"), r.text[:200])
        r = await hc.post(f"/payments/{UP}/convert-unapplied", headers=FTM, json={"reason": "again"})
        check("nothing left to convert -> 409", r.status_code == 409)
        pa = await payment(c4, "100", [], ref="NOTUNAPP")
        r = await hc.post(f"/payments/{pa}/convert-unapplied", headers=FTM, json={"reason": "x"})
        check("a payment that is not Unapplied / PartiallyApplied -> 409", r.status_code == 409)

        # convert a partially applied payment's unapplied part
        c5 = await customer("Omicron")
        q1 = await invoice(c5, "O-1", "400")
        pp = await payment(c5, "1000", [("O-1", None)], ref="UTR-PART")
        # the matcher leaves 600 unapplied only when an unknown number was quoted; seed that shape
        async with SessionLocal() as db:
            await db.execute(text("update payments set extracted_invoice_numbers=:j where id=:i"), {"j": '[{"invoice_number": "O-1", "amount": null}, {"invoice_number": "O-404", "amount": null}]', "i": pp})
            await db.commit()
        o = await run(pp)
        row = await pay(pp)
        check("setup: a payment with an unknown quoted invoice is PartiallyApplied with 600 unapplied", row[0] == "PartiallyApplied" and row[1] == D("600.00"), str(row[:3]))
        r = await hc.post(f"/payments/{pp}/convert-unapplied", headers=FTM, json={"reason": "keep it as credit"})
        row = await pay(pp)
        check("converting a PartiallyApplied payment's unapplied part: now Applied", r.status_code == 200 and row[0] == "Applied" and row[1] == 0 and row[2] == D("600.00"), r.text[:200])

        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("money identities hold across invoices, payments and notes", found == [], str([str(v) for v in found][:4]))
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
