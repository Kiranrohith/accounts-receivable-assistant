"""P3: the matching and apply engine on a real (throwaway) database. No LLM: payments are seeded in
state Matching, exactly as the intake step leaves them, and run_matching decides and applies them.
"""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
from sqlalchemy import text

import core.services.payments.engine as engine
from core.services.payments.apply import Contention
from core.services.payments.engine import Status, run_matching
from core.services.payments.identities import verify_money_identities
from core.services.payments.parking import queue_parked_payments
from config.settings import get_settings
from data.clients.db import SessionLocal, engine as db_engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_pay2" in s.database_url, "refusing to run against a non-test database"
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


async def invoice(cid, number, collectible, *, due=date(2026, 10, 31), currency="INR", rate=None,
                  po_hold=False, disputed=False, status=InvoiceStatus.OPEN, paid="0"):
    async with SessionLocal() as db:
        db.add(Invoice(
            customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=due,
            invoice_currency=currency, amount_inr=D(collectible), collectible_total=D(collectible),
            amount_foreign=None if currency == "INR" else D(collectible) / D(rate),
            fx_rate_invoice=None if currency == "INR" else D(rate), po_hold=po_hold, is_disputed=disputed,
            status=status, amount_paid=D(paid),
        ))
        await db.commit()


async def payment(cid, amount, *, currency="INR", quoted=(), layer=MatchedLayer.L1, ref=None, paid_on=date(2026, 10, 15)):
    async with SessionLocal() as db:
        p = Payment(
            state=PaymentState.MATCHING, customer_id=cid, payment_currency=currency, payment_date=paid_on,
            reference_number=ref, matched_layer=layer,
            amount_received_inr=D(amount) if currency == "INR" else None,
            amount_received_foreign=None if currency == "INR" else D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": a} for n, a in quoted],
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
            "select state::text, unapplied_amount, held_amount, credited_amount, amount_received_inr, "
            "confidence_score, matched_layer::text, match_reason, review_reasons, rematch_count "
            "from payments where id=:i"), {"i": pid})).first()
        al = (await db.execute(text(
            "select i.invoice_number, a.allocated_amount, a.tolerance_applied::text, a.tolerance_residual, a.fx_rate_used, "
            "a.status::text, a.matched_layer::text from payment_allocations a join invoices i on i.id=a.invoice_id "
            "where a.payment_id=:i order by i.invoice_number"), {"i": pid})).all()
        return r, al


async def inv(number):
    async with SessionLocal() as db:
        return (await db.execute(text(
            "select status::text, amount_paid, collectible_balance from invoices where invoice_number=:n"), {"n": number})).first()


async def notes(cid):
    async with SessionLocal() as db:
        return (await db.execute(text(
            "select note_number, amount, remaining_amount, status::text, linked_payment_id from notes where customer_id=:c"), {"c": cid})).all()


async def states_log(entity, eid):
    async with SessionLocal() as db:
        return (await db.execute(text("select from_state, to_state, reason from state_log where entity_type=:t and entity_id=:i order by occurred_at"), {"t": entity, "i": eid})).all()


async def main():
    # ---- 1. L1 exact: Applied, invoice Paid ----------------------------------------------------
    c = await customer("Alpha")
    await invoice(c, "A-1", "900")
    p = await payment(c, "900", quoted=[("A-1", None)], ref="R-A1")
    o = await run(p)
    r, al = await pay(p)
    check("L1 exact: Applied", o.status is Status.APPLIED and r[0] == "Applied", f"{o.status} {r[0]}")
    check("L1 exact: invoice Paid, amount_paid 900, balance 0", (await inv("A-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("A-1")))
    check("L1 exact: one T1 allocation of 900", len(al) == 1 and al[0][1] == D("900.00") and al[0][2] == "T1" and al[0][5] == "Active" and al[0][6] == "L1", str(al))
    check("L1 exact: INR amount set, buckets zero, score clamped at 100, layer L1", r[4] == D("900.00") and r[1] == 0 and r[2] == 0 and r[3] == 0 and r[5] == 100 and r[6] == "L1", str(r[:7]))
    check("L1 exact: state logged on payment and invoice", len(await states_log("payment", p)) >= 1 and any(x[1] == "Paid" for x in await states_log("invoice", (await invoice_id("A-1")))), "")

    # ---- 2. within the rounding tolerance -----------------------------------------------------
    c = await customer("Beta")
    await invoice(c, "B-1", "900")
    p = await payment(c, "900.60", quoted=[("B-1", None)])
    await run(p)
    r, al = await pay(p)
    check("T1 over by 0.60: Paid, residual +0.60, identity", (await inv("B-1"))[0] == "Paid" and al[0][3] == D("0.60") and r[4] == D("900.60"), str(al))
    c = await customer("BetaB")
    await invoice(c, "B-2", "900")
    p = await payment(c, "899.40", quoted=[("B-2", None)])
    await run(p)
    r, al = await pay(p)
    check("T1 short by 0.60: Paid, residual -0.60", (await inv("B-2"))[0] == "Paid" and al[0][3] == D("-0.60"), str(al))

    # ---- 3. short from a certain identity: bypass, Partly paid, then completed --------------------
    c = await customer("Gamma")
    await invoice(c, "G-1", "900")
    await invoice(c, "G-2", "5000")
    p = await payment(c, "600", quoted=[("G-1", None)])
    o = await run(p)
    r, al = await pay(p)
    check("L1 short: PartlyPaid, 600 applied (T4)", o.status is Status.APPLIED and (await inv("G-1")) == ("PartlyPaid", D("600.00"), D("300.00")) and al[0][2] == "T4", str(await inv("G-1")))
    check("L1 short: payment Applied (nothing left over), reason says short", r[0] == "Applied" and "short" in r[7].lower(), r[7][:120])
    p2 = await payment(c, "300", quoted=[("G-1", None)])
    await run(p2)
    check("second payment completes it: Paid, 900", (await inv("G-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("G-1")))

    # ---- 4. overpayment: credit note when the score allows, review when it does not -------------
    c = await customer("Delta")
    await invoice(c, "D-1", "900")
    p = await payment(c, "1000", quoted=[("D-1", None)])
    o = await run(p)
    r, al = await pay(p)
    nts = await notes(c)
    check("L1 over, single open invoice: Applied, invoice Paid, 100 credited", r[0] == "Applied" and r[3] == D("100.00") and (await inv("D-1"))[0] == "Paid", f"{r[0]} {r[3]} {r[5]}")
    check("credit note: CN number, 100 Open, linked to the payment", len(nts) == 1 and nts[0][0].startswith("CN-2026-") and nts[0][1] == D("100.00") and nts[0][2] == D("100.00") and nts[0][3] == "Open" and nts[0][4] == p, str(nts))
    check("overpayment allocation is T3 for the invoice's whole balance", al[0][1] == D("900.00") and al[0][2] == "T3" and al[0][3] is None, str(al))
    c = await customer("DeltaB")
    await invoice(c, "D-2", "900")
    await invoice(c, "D-3", "800", due=date(2027, 3, 1))
    p = await payment(c, "1000", quoted=[("D-2", None)])
    o = await run(p)
    r, _ = await pay(p)
    check("L1 over with a second open invoice: identity is certain, so the score gate is bypassed, D-2 Paid, 100 credited", o.status is Status.APPLIED and r[0] == "Applied" and (await inv("D-2"))[0] == "Paid" and r[3] == D("100.00") and len(await notes(c)) == 1, f"{o.status} {r[0]} {r[3]}")
    check("the other open invoice is untouched", (await inv("D-3"))[0] == "Open")

    # ---- 4b. the reference-pattern +3 is judged on NORMALIZED numbers (2026-10-21) ----------------
    c = await customer("ShapeCo")
    await invoice(c, "INV-2026-0042", "900", due=date(2027, 12, 1))
    await invoice(c, "ZZ-7", "500", due=date(2027, 12, 1))
    p = await payment(c, "900", quoted=[("INV2026-0042", None)])
    await run(p)
    r, al = await pay(p)
    check("quoted INV2026-0042 vs open INV-2026-0042: same invoice, +3 earned (60+35+3 = 98, no date or single-invoice bonus)", r[5] == 98 and al and al[0][0] == "INV-2026-0042", f"{r[5]} {al}")

    # ---- 5. several invoices ------------------------------------------------------------------
    c = await customer("Eps")
    await invoice(c, "E-1", "400")
    await invoice(c, "E-2", "500")
    p = await payment(c, "900", quoted=[("E-1", None), ("E-2", None)])
    await run(p)
    r, al = await pay(p)
    check("L1 two invoices, exact total: both Paid, two allocations", (await inv("E-1"))[0] == "Paid" and (await inv("E-2"))[0] == "Paid" and len(al) == 2, str(al))
    c = await customer("EpsB")
    await invoice(c, "F-1", "400")
    await invoice(c, "F-2", "500")
    p = await payment(c, "900", quoted=[("F-1", "400"), ("F-2", "500")])
    await run(p)
    r, al = await pay(p)
    check("stated split that adds up: each invoice on its own amount", r[0] == "Applied" and [x[1] for x in al] == [D("400.00"), D("500.00")], str(al))
    c = await customer("EpsC")
    await invoice(c, "H-1", "400")
    await invoice(c, "H-2", "500")
    p = await payment(c, "900", quoted=[("H-1", "400"), ("H-2", "400")])
    o = await run(p)
    r, _ = await pay(p)
    check("stated split that does not add up to the total: review, nothing applied", o.status is Status.REVIEW and "does not add up" in r[8][0] and (await inv("H-1"))[0] == "Open", str(r[8]))
    c = await customer("EpsD")
    await invoice(c, "K-1", "400")
    await invoice(c, "K-2", "500")
    await invoice(c, "K-3", "800")
    p = await payment(c, "700", quoted=[("K-1", None), ("K-2", None)])
    await run(p)
    r, al = await pay(p)
    check("short over a set: oldest due first (K-1 400 full, K-2 300)", [(x[0], x[1]) for x in al] == [("K-1", D("400.00")), ("K-2", D("300.00"))] and (await inv("K-2"))[0] == "PartlyPaid", str(al))

    # ---- 6. a number we do not have (C2) --------------------------------------------------------
    c = await customer("Zeta")
    await invoice(c, "Z-1", "900")
    p = await payment(c, "1200", quoted=[("Z-1", None), ("Z-404", None)])
    o = await run(p)
    r, al = await pay(p)
    check("quoted + unknown number: known invoice Paid, 300 stays unapplied, PartiallyApplied", r[0] == "PartiallyApplied" and r[1] == D("300.00") and r[3] == 0 and (await inv("Z-1"))[0] == "Paid" and await notes(c) == [], f"{r[0]} {r[1]} {r[3]}")
    check("unknown number named in the reason", "Z-404" in r[7], r[7][:160])

    # ---- 7. already paid ------------------------------------------------------------------------
    c = await customer("Eta")
    await invoice(c, "T-1", "900", status=InvoiceStatus.PAID, paid="900")
    async with SessionLocal() as db:  # a real earlier payment behind that Paid invoice
        prior = Payment(state=PaymentState.APPLIED, customer_id=c, payment_currency="INR", amount_received_inr=D("900"),
                        reference_number="PRIOR-T1", matched_layer=MatchedLayer.L1)
        db.add(prior)
        await db.flush()
        await db.execute(text("insert into payment_allocations (id, payment_id, invoice_id, allocated_amount, status) "
                              "select gen_random_uuid(), :p, id, 900, 'Active' from invoices where invoice_number='T-1'"), {"p": prior.id})
        await db.commit()
    await invoice(c, "T-2", "700")
    p = await payment(c, "900", quoted=[("T-1", None), ("T-2", None)])
    o = await run(p)
    r, _ = await pay(p)
    check("a quoted invoice is already Paid: whole payment to review, nothing applied", o.status is Status.REVIEW and "already paid" in r[8][0] and (await inv("T-2"))[0] == "Open", str(r[8]))

    # ---- 8. L2 / L3 -----------------------------------------------------------------------------
    c = await customer("Theta")
    await invoice(c, "L-1", "900")
    await invoice(c, "L-2", "450")
    p = await payment(c, "450", layer=MatchedLayer.L2)
    o = await run(p)
    r, al = await pay(p)
    check("L2 exact for one of two open invoices (45+35+5=85): applied to that one", o.status is Status.APPLIED and [x[0] for x in al] == ["L-2"] and (await inv("L-1"))[0] == "Open" and r[5] == 85, f"{r[5]} {al}")
    c = await customer("Iota")
    await invoice(c, "M-1", "900")
    await invoice(c, "M-2", "900")
    p = await payment(c, "900", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("two equally good invoices: tie, review", o.status is Status.REVIEW and "equally well" in r[8][0] and (await inv("M-1"))[0] == "Open" and (await inv("M-2"))[0] == "Open", str(r[8]))
    c = await customer("Kappa")
    await invoice(c, "N-1", "900")
    await invoice(c, "N-2", "450")
    p = await payment(c, "450", layer=MatchedLayer.L3)
    o = await run(p)
    r, _ = await pay(p)
    check("L3 exact (35+35+5=75): below 85, review", o.status is Status.REVIEW and "scored 75" in r[8][0], str(r[8]))
    c = await customer("Lambda")
    await invoice(c, "P-1", "900")
    p = await payment(c, "500", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("L2 short against the customer's ONLY open invoice: no exact fit, so Unapplied cash, invoice stays Open (2026-10-21 rule)", r[0] == "Unapplied" and (await inv("P-1"))[0] == "Open", f"{r[0]} {await inv('P-1')}")
    c = await customer("Mu")
    await invoice(c, "Q-1", "900")
    await invoice(c, "Q-2", "700")
    p = await payment(c, "500", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("L2 short with two open invoices: no exact fit, Unapplied cash, both invoices stay Open", r[0] == "Unapplied" and (await inv("Q-1"))[0] == "Open" and (await inv("Q-2"))[0] == "Open", f"{r[0]}")
    c = await customer("Nu")
    await invoice(c, "S-1", "400")
    await invoice(c, "S-2", "500")
    await invoice(c, "S-3", "1000")
    p = await payment(c, "900", layer=MatchedLayer.L2)
    o = await run(p)
    r, al = await pay(p)
    check("L2 combination (S-1 + S-2 = 900) beats the single invoices", o.status is Status.APPLIED and [x[0] for x in al] == ["S-1", "S-2"], f"{o.status} {r[8]} {al}")
    c = await customer("Xi")
    await invoice(c, "U-1", "400")
    await invoice(c, "U-2", "500")
    await invoice(c, "U-3", "300")
    await invoice(c, "U-4", "600")
    p = await payment(c, "900", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("C6: two equal subsets (400+500 vs 300+600) tie: review", o.status is Status.REVIEW and "equally well" in r[8][0], str(r[8]))
    c = await customer("Omicron")
    p = await payment(c, "900", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("customer with no open invoice: review", o.status is Status.REVIEW and "no open invoice" in r[8][0], str(r[8]))

    # ---- 9. foreign payments --------------------------------------------------------------------
    c = await customer("Pi")
    await invoice(c, "USD-1", "99600", currency="USD", rate="83")
    p = await payment(c, "1200", currency="USD", quoted=[("USD-1", None)])
    o = await run(p)
    r, al = await pay(p)
    check("USD payment at the invoice's frozen rate: Paid, INR value 99600", o.status is Status.APPLIED and (await inv("USD-1"))[0] == "Paid" and r[4] == D("99600.00"), f"{r[0]} {r[4]}")
    check("allocation records the rate used (83)", al[0][4] == D("83.00000000"), str(al))
    c = await customer("Rho")
    await invoice(c, "USD-2", "99600", currency="USD", rate="83")
    p = await payment(c, "1185", currency="USD", quoted=[("USD-2", None)])
    o = await run(p)
    r, al = await pay(p)
    check("USD short by 1.25% (inside the FX band): T2, Paid, signed residual -1245", o.status is Status.APPLIED and al[0][2] == "T2" and al[0][3] == D("-1245.00") and (await inv("USD-2"))[0] == "Paid", str(al))
    check("T2: received INR = allocation + residual", r[4] == D("98355.00") and D("99600.00") + D("-1245.00") == r[4], str(r[4]))
    c = await customer("Sigma")
    await invoice(c, "USD-3", "83000", currency="USD", rate="83")
    await invoice(c, "USD-4", "85000", currency="USD", rate="85")
    p = await payment(c, "2000", currency="USD", quoted=[("USD-3", None), ("USD-4", None)])
    o = await run(p)
    r, _ = await pay(p)
    check("quoted invoices frozen at different rates: review", o.status is Status.REVIEW and "different exchange rates" in r[8][0], str(r[8]))
    c = await customer("Tau")
    await invoice(c, "USD-5", "83000", currency="USD", rate="83")
    p = await payment(c, "1000", currency="EUR", layer=MatchedLayer.L2)
    o = await run(p)
    r, _ = await pay(p)
    check("C12: a payment in a third currency: review", o.status is Status.REVIEW and "no frozen EUR rate is on file" in r[8][0] and "is in USD" in r[8][0], str(r[8]))
    c = await customer("Upsilon")
    await invoice(c, "USD-6", "83000", currency="USD", rate="83")
    p = await payment(c, "83000", quoted=[("USD-6", None)])  # an INR payment against a USD invoice
    o = await run(p)
    r, _ = await pay(p)
    check("INR payment against a USD invoice is NEVER auto-applied (C12, 2026-10-21): review with the indicative USD value",
          o.status is Status.REVIEW and (await inv("USD-6"))[0] == "Open" and "about USD 1000.00" in r[8][0], f"{o.status} {r[8]}")

    # ---- 10. parked on a PO hold, then released -------------------------------------------------
    c = await customer("Phi")
    await invoice(c, "PH-1", "900", po_hold=True)
    p = await payment(c, "900", quoted=[("PH-1", None)])
    o = await run(p)
    r, _ = await pay(p)
    check("target invoice on PO hold: parked (still Matching), nothing applied", o.status is Status.PARKED and r[0] == "Matching" and r[7].startswith("Parked:") and (await inv("PH-1"))[0] == "Open", f"{o.status} {r[7][:80]}")
    async with SessionLocal() as db:
        await db.execute(text("update invoices set po_hold=false where invoice_number='PH-1'"))
        # a source document is needed to queue the job: give the payment one
        await db.execute(text("insert into source_documents (id, file_name, doc_type, uploaded_via, doc_kind, file_hash) values (gen_random_uuid(), 'p.pdf', 'pdf', 'upload', 'payment', 'ph1hash')"))
        await db.execute(text("update payments set source_document_id=(select id from source_documents where file_hash='ph1hash') where id=:i"), {"i": p})
        queued = await queue_parked_payments(db, c, None)
        await db.commit()
    check("releasing the hold queues a re-match job for the parked payment", queued == 1, str(queued))
    async with SessionLocal() as db:
        jt = (await db.execute(text("select job_type, state::text from jobs where job_type='payment_rematch'"))).all()
    check("a payment_rematch job is Queued", jt == [("payment_rematch", "Queued")], str(jt))
    o = await run(p)
    check("matched again after the release: Applied, invoice Paid", o.status is Status.APPLIED and (await inv("PH-1"))[0] == "Paid", f"{o.status}")

    # ---- 11. disputed ---------------------------------------------------------------------------
    c = await customer("Chi")
    await invoice(c, "DI-1", "900", disputed=True)
    async with SessionLocal() as db:  # a dispute that holds money (uncategorised): the slice is HELD (D6)
        from datetime import UTC, datetime
        from data.models.postgres import Dispute
        iid = (await db.execute(text("select id from invoices where invoice_number='DI-1'"))).scalar()
        db.add(Dispute(customer_id=c, invoice_id=iid, received_at=datetime.now(UTC), summary="held"))
        await db.commit()
    p = await payment(c, "900", quoted=[("DI-1", None)])
    o = await run(p)
    r, _ = await pay(p)
    check("target invoice has a holding dispute: the payment is Held (D6)", o.status is Status.APPLIED and r[0] == "Held" and r[2] == D("900.00"), str(r[:3]))

    # ---- 12. no customer / not waiting / idempotent --------------------------------------------
    async with SessionLocal() as db:
        nc = Payment(state=PaymentState.MATCHING, payment_currency="INR", amount_received_inr=D("10"))
        db.add(nc)
        await db.commit()
        ncid = nc.id
    o = await run(ncid)
    check("payment without a customer: review", o.status is Status.REVIEW, str(o.reasons))
    first = (await pay(p))[1]
    o = await run(p)
    check("running a decided payment again does nothing", o.status is Status.NOT_WAITING, str(o.status))
    c = await customer("Psi")
    await invoice(c, "I-1", "900")
    pi = await payment(c, "900", quoted=[("I-1", None)])
    await run(pi)
    a1 = (await pay(pi))[1]
    await run(pi)
    check("applying twice never double-pays", (await pay(pi))[1] == a1 and (await inv("I-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("I-1")))

    # ---- 13. concurrency: two payments, one invoice --------------------------------------------
    outcomes = []
    for k in range(4):
        c = await customer(f"Race{k}")
        await invoice(c, f"RC-{k}", "900")
        p1 = await payment(c, "900", quoted=[(f"RC-{k}", None)], ref=f"X{k}a")
        p2 = await payment(c, "900", quoted=[(f"RC-{k}", None)], ref=f"X{k}b")
        o1, o2 = await asyncio.gather(run(p1), run(p2))
        got = sorted([o1.status.value, o2.status.value])
        stt = await inv(f"RC-{k}")
        outcomes.append(got)
        check(f"race {k}: exactly one applies, the other goes to review; invoice paid once", got == ["applied", "review"] and stt == ("Paid", D("900.00"), D("0.00")), f"{got} {stt}")
    # ---- 14. contention that never clears -------------------------------------------------------
    c = await customer("Never")
    await invoice(c, "NV-1", "900")
    pn = await payment(c, "900", quoted=[("NV-1", None)])
    real_apply, real_backoff = engine.apply, engine.CONTENTION_BACKOFF_SECONDS
    calls = []

    async def always_contended(*a, **k):
        calls.append(1)
        raise Contention("forced")

    engine.apply, engine.CONTENTION_BACKOFF_SECONDS = always_contended, (0, 0, 0)
    try:
        o = await run(pn)
    finally:
        engine.apply, engine.CONTENTION_BACKOFF_SECONDS = real_apply, real_backoff
    r, _ = await pay(pn)
    check("contention that never clears: review after the cap, nothing applied", o.status is Status.REVIEW and r[0] == "NeedsReview" and r[9] == 4 and len(calls) == 4 and (await inv("NV-1"))[0] == "Open", f"{o.status} rematch={r[9]} calls={len(calls)}")

    # ---- the whole book ------------------------------------------------------------------------
    async with SessionLocal() as db:
        found = await verify_money_identities(db)
        npay = (await db.execute(text("select count(*) from payments"))).scalar()
        stuck = (await db.execute(text("select count(*) from payments where state='Matching' and (match_reason is null or match_reason not like 'Parked:%')"))).scalar()
    check(f"money identities hold across all {npay} payments", found == [], str([str(v) for v in found][:5]))
    check("no payment is left half-way in Matching (except the one with no customer)", stuck <= 0, str(stuck))
    await db_engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


async def invoice_id(number):
    async with SessionLocal() as db:
        return (await db.execute(text("select id from invoices where invoice_number=:n"), {"n": number})).scalar()


asyncio.run(main())
