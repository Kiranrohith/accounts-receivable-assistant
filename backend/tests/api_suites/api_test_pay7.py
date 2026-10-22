"""The rule updates of 2026-10-21 on a real (throwaway) database: tolerance classes (INR vs foreign),
invoice numbers compared normalized, and payments in another currency than the invoice. No LLM:
payments are seeded in state Matching and run_matching decides them."""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from config.settings import get_settings
from core.services.payments.engine import Status, run_matching
from core.services.payments.identities import verify_money_identities
from core.services.payments.identity import gather_facts
from data.clients.db import SessionLocal, engine as db_engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, PaymentState
from data.repositories.invoices import invoices_repo
from schemas.payments.extraction import ExtractedPayment, PaidInvoice

D = Decimal
s = get_settings()
assert "ar_pay7" in s.database_url, "refusing to run against a non-test database"
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


async def invoice(cid, number, collectible, *, currency="INR", rate=None, due=date(2026, 10, 31)):
    async with SessionLocal() as db:
        db.add(Invoice(
            customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=due,
            invoice_currency=currency, amount_inr=D(collectible), collectible_total=D(collectible),
            amount_foreign=None if currency == "INR" else D(collectible) / D(rate),
            fx_rate_invoice=None if currency == "INR" else D(rate),
        ))
        await db.commit()


async def payment(cid, amount, quoted, *, currency="INR", layer=MatchedLayer.L1, ref=None):
    async with SessionLocal() as db:
        p = Payment(
            state=PaymentState.MATCHING, customer_id=cid, payment_currency=currency, payment_date=date(2026, 10, 15),
            reference_number=ref, matched_layer=layer,
            amount_received_inr=D(amount) if currency == "INR" else None,
            amount_received_foreign=None if currency == "INR" else D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": None} for n in quoted],
        )
        db.add(p)
        await db.commit()
        return p.id


async def run(pid):
    async with SessionLocal() as db:
        return await run_matching(db, pid, None)


async def pay(pid):
    async with SessionLocal() as db:
        r = (await db.execute(text(
            "select state::text, unapplied_amount, credited_amount, amount_received_inr, matched_layer::text, "
            "match_reason, review_reasons from payments where id=:i"), {"i": pid})).first()
        al = (await db.execute(text(
            "select i.invoice_number, a.allocated_amount, a.tolerance_applied::text, a.tolerance_residual "
            "from payment_allocations a join invoices i on i.id=a.invoice_id where a.payment_id=:i order by i.invoice_number"),
            {"i": pid})).all()
        return r, al


async def inv(number):
    async with SessionLocal() as db:
        return (await db.execute(text(
            "select status::text, amount_paid, collectible_balance from invoices where invoice_number=:n"), {"n": number})).first()


async def notes_of(cid):
    async with SessionLocal() as db:
        return (await db.execute(text("select count(*) from notes where customer_id=:c"), {"c": cid})).scalar()


async def main():
    # ---- 1. numbers are compared normalized ---------------------------------------------------------
    c = await customer("Norm")
    await invoice(c, "INV-1176", "1000")
    await invoice(c, "INV-1157", "2000")
    await invoice(c, "INV-1104", "500")
    await invoice(c, "INV-1105", "700")
    p = await payment(c, "1000", ["INV1176"], ref="R-54")
    o = await run(p)
    r, al = await pay(p)
    check("P54: 'INV1176' on the advice, 'INV-1176' on file -> L1, Applied, invoice Paid",
          o.status is Status.APPLIED and r[0] == "Applied" and r[4] == "L1" and al[0][0] == "INV-1176" and (await inv("INV-1176"))[0] == "Paid", f"{r[0]} {r[4]} {al}")
    p = await payment(c, "2000", ["inv 1157"], ref="R-60")
    await run(p)
    r, al = await pay(p)
    check("P60: 'inv 1157' -> L1 on INV-1157", r[0] == "Applied" and r[4] == "L1" and al[0][0] == "INV-1157", f"{r[0]} {r[4]} {al}")
    # P33: a bare number names nothing. The amount still fits INV-1104 (L2 by amount), but never as L1.
    p = await payment(c, "500", ["1104"], layer=MatchedLayer.L2, ref="R-33")
    await run(p)
    r, al = await pay(p)
    check("P33: a bare '1104' does not match INV-1104 as a quoted number (the layer stays L2)", r[4] == "L2" and "Matched by L1" not in (r[5] or ""), f"{r[4]} {r[5]}")
    async with SessionLocal() as db:
        got = await invoices_repo.get_by_numbers(db, ["1104"])
        check("P33: and the lookup finds nothing for '1104'", got == [])
        got = await invoices_repo.get_by_numbers(db, ["inv/1105", "INV 1105", "zzz"])
        check("lookup: two spellings of one number find that invoice once", [i.invoice_number for i in got] == ["INV-1105"])
        check("duplicate check: 'inv 1105' exists", await invoices_repo.invoice_number_exists(db, "inv 1105"))
        facts = await gather_facts(db, ExtractedPayment(amount=D("700"), currency="INR", invoices=[PaidInvoice(invoice_number="inv-1105", amount=None), PaidInvoice(invoice_number="INV-9", amount=None)]))
        check("identity: a differently-spelled quoted number still names its customer; the unknown one is reported",
              [i.invoice_number for i in facts.quoted_invoices] == ["INV-1105"] and facts.unknown_numbers == ["INV-9"], str(facts.unknown_numbers))
    # the database refuses a second spelling of the same invoice
    async with SessionLocal() as db:
        try:
            db.add(Invoice(customer_id=c, invoice_number="inv1105", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("5"), collectible_total=D("5")))
            await db.flush()
            refused = False
        except IntegrityError:
            refused = True
        await db.rollback()
    check("the unique index on the normalized number refuses 'inv1105' beside 'INV-1105'", refused)
    async with SessionLocal() as db:
        db.add(Invoice(customer_id=c, invoice_number="DN-2001", type="DEBIT_NOTE", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D("5"), collectible_total=D("5")))
        await db.commit()
        norm = (await db.execute(text("select invoice_number_norm from invoices where invoice_number='DN-2001'"))).scalar()
    check("the generated column: DN-2001 is stored as DN2001", norm == "DN2001", norm)

    # ---- 2. INR: T1 is +/- 1.00 inclusive; there is no band ---------------------------------------------
    c = await customer("Inr")
    cases = [
        ("T-56", "10000", "9999.00", "Applied", "Paid", "T1"),  # INR 1.00 short
        ("T-59", "10000", "10000.75", "Applied", "Paid", "T1"),  # INR 0.75 over
        ("T-57", "10000", "9998.50", "PartiallyApplied", "PartlyPaid", "T4"),  # INR 1.50 short: T4
        ("T-58", "10000", "9920.00", "PartiallyApplied", "PartlyPaid", "T4"),  # 0.8% short: still T4
    ]
    for number, balance, received, state, status, tol in cases:
        await invoice(c, number, balance)
        p = await payment(c, received, [number], ref=f"R-{number}")
        await run(p)
        r, al = await pay(p)
        stat = (await inv(number))[0]
        extra = f"{r[0]} {stat} {al}"
        check(f"INR {number}: received {received} on {balance} -> {tol}, invoice {status}",
              al and al[0][2] == tol and stat == status, extra)
    # an overpayment needs the customer's ONLY open invoice to reach the 85 gate (an over amount is not a short bypass)
    o1 = await customer("InrOver")
    await invoice(o1, "T-OV", "10000")
    p = await payment(o1, "10001.01", ["T-OV"], ref="R-OV")
    await run(p)
    r, al = await pay(p)
    check("INR 1.01 over (T3): Paid, and a credit note for the 1.01 excess", al and al[0][2] == "T3" and (await inv("T-OV"))[0] == "Paid" and r[2] == D("1.01") and await notes_of(o1) == 1, f"{r[0]} {al} credited {r[2]}")
    o2 = await customer("InrBand")
    await invoice(o2, "T-BAND", "10000")
    p = await payment(o2, "10150", ["T-BAND"], ref="R-BAND")  # 1.5% over an INR invoice: no band, so T3
    await run(p)
    r, al = await pay(p)
    check("INR 1.5% over is T3 with a note of 150, never T2", al and al[0][2] == "T3" and r[2] == D("150.00") and await notes_of(o2) == 1, f"{al} {r[2]}")

    # ---- 3. foreign, same currency: exact T1; within 1.5% T2 (signed residual, no note); beyond T3/T4 ------------------
    f = await customer("Usd")
    foreign = [
        ("F-0", "1000", "T1", None, "Paid"),
        ("F-1", "991", "T2", "-747.00", "Paid"),  # P17: 0.9% short
        ("F-2", "1012", "T2", "996.00", "Paid"),  # P17b: 1.2% over
        ("F-3", "985", "T2", "-1245.00", "Paid"),  # P17c: exactly 1.5% short
        ("F-4", "984", "T4", None, "PartlyPaid"),  # P17d: 1.6% short
    ]
    check("foreign: before the loop no note exists", await notes_of(f) == 0)
    for number, usd, tol, residual, status in foreign:
        await invoice(f, number, "83000", currency="USD", rate="83")
        p = await payment(f, usd, [number], currency="USD", ref=f"R-{number}")
        o = await run(p)
        r, al = await pay(p)
        ok = al and al[0][2] == tol and (await inv(number))[0] == status
        if residual is not None:
            ok = ok and al[0][3] == D(residual)
        check(f"foreign {number}: USD {usd} on USD 1000 -> {tol}" + (f", residual {residual}" if residual else ""), ok, f"{o.status} {r[0]} {al}")
    check("foreign: the T2 payments (short and over) made no credit note", await notes_of(f) == 0)
    f2 = await customer("UsdOver")
    await invoice(f2, "F-5", "83000", currency="USD", rate="83")
    p = await payment(f2, "1016", ["F-5"], currency="USD", ref="R-F-5")  # 1.6% over: beyond the band
    await run(p)
    r, al = await pay(p)
    check("foreign 1.6% over (T3): Paid + a credit note for the 1328 excess", al and al[0][2] == "T3" and (await inv("F-5"))[0] == "Paid" and r[2] == D("1328.00") and await notes_of(f2) == 1, f"{al} {r[2]}")
    r, al = await pay(await first_payment("R-F-4"))
    check("foreign T4: Partly paid with the short-payment allocation of exactly what was received (USD 984 = 81672)", al[0][1] == D("81672.00"), str(al))

    # ---- 4. a payment in another currency than the invoice: review with an indicative conversion ---------------------
    g = await customer("Mix")
    await invoice(g, "G-USD", "83000", currency="USD", rate="83")
    await invoice(g, "G-INR", "5000")
    p = await payment(g, "83000", ["G-USD"], ref="R-G1")  # a foreign invoice paid in INR
    o = await run(p)
    r, al = await pay(p)
    check("C12: an INR payment quoting a USD invoice -> review, nothing applied", o.status is Status.REVIEW and r[0] == "NeedsReview" and not al and (await inv("G-USD"))[0] == "Open", f"{r[0]} {al}")
    check("C12: the reason names both currencies and shows the indicative conversion (INR 83000 about USD 1000)",
          "in INR" in r[6][0] and "USD" in r[6][0] and "about USD 1000.00" in r[6][0], str(r[6]))
    p = await payment(g, "10", ["G-INR"], currency="USD", ref="R-G2")  # a USD payment quoting an INR invoice
    await run(p)
    r, al = await pay(p)
    check("C12: a USD payment quoting an INR invoice -> review; the USD rate on file (83) gives the indication",
          r[0] == "NeedsReview" and not al and "about INR 830.00" in r[6][0], str(r[6]))
    h = await customer("Mix2")
    await invoice(h, "H-EUR", "90000", currency="EUR", rate="90")
    p = await payment(h, "100", ["H-EUR"], currency="USD", ref="R-H1")  # third currency, no USD rate known
    await run(p)
    r, al = await pay(p)
    check("C12: a third currency with no frozen USD rate on file says so instead of inventing a conversion",
          r[0] == "NeedsReview" and "no frozen USD rate is on file" in r[6][0], str(r[6]))
    # an INR payment without a quoted number: foreign invoices are left out of the candidates
    p = await payment(g, "5000", [], layer=MatchedLayer.L2, ref="R-G3")
    await run(p)
    r, al = await pay(p)
    check("an INR payment with no quoted number applies to the INR invoice and leaves the USD one alone",
          al and al[0][0] == "G-INR" and (await inv("G-USD"))[0] == "Open", f"{r[0]} {al}")
    k = await customer("OnlyUsd")
    await invoice(k, "K-USD", "83000", currency="USD", rate="83")
    p = await payment(k, "83000", [], layer=MatchedLayer.L2, ref="R-K1")
    o = await run(p)
    r, al = await pay(p)
    check("an INR payment, no quoted number, the customer has ONLY a USD invoice -> review with the conversion",
          o.status is Status.REVIEW and not al and "about USD 1000.00" in r[6][0], str(r[6]))

    async with SessionLocal() as db:
        found = await verify_money_identities(db)
    check("the money checker holds after everything", found == [], str(found))
    await db_engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


async def first_payment(ref):
    async with SessionLocal() as db:
        return (await db.execute(text("select id from payments where reference_number=:r"), {"r": ref})).scalar()


asyncio.run(main())
