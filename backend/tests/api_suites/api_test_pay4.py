"""P4: the payments read API and the review queue, over HTTP, on a throwaway database. No LLM.

Payments are seeded as the pipeline leaves them (NeedsReview with reasons); the reviewer then acts
through the real endpoints with real role tokens.
"""

import asyncio
import sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

import core.services.payments.review as review_module
from api.rest.app import app
from config.settings import get_settings
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import InvoiceStatus, MatchedLayer, PaymentState

D = Decimal
s = get_settings()
assert "ar_pay4" in s.database_url, "refusing to run against a non-test database"
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
                  po_hold=False, status=InvoiceStatus.OPEN, paid="0"):
    async with SessionLocal() as db:
        inv = Invoice(
            customer_id=cid, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=due,
            invoice_currency=currency, amount_inr=D(collectible), collectible_total=D(collectible),
            amount_foreign=None if currency == "INR" else D(collectible) / D(rate),
            fx_rate_invoice=None if currency == "INR" else D(rate), po_hold=po_hold, status=status, amount_paid=D(paid),
        )
        db.add(inv)
        await db.commit()
        return inv.id


async def payment(cid, amount, *, currency="INR", ref=None, payer=None, email=None, quoted=(), reasons=("Needs a person",),
                  state=PaymentState.NEEDS_REVIEW, paid_on=date(2026, 10, 15)):
    async with SessionLocal() as db:
        p = Payment(
            state=state, customer_id=cid, payment_currency=currency, payment_date=paid_on, reference_number=ref,
            payee_name=payer, from_email=email, matched_layer=MatchedLayer.L4,
            amount_received_inr=None if amount is None or currency != "INR" else D(amount),
            amount_received_foreign=None if amount is None or currency == "INR" else D(amount),
            extracted_invoice_numbers=[{"invoice_number": n, "amount": a} for n, a in quoted],
            review_reasons=list(reasons) if reasons else None,
        )
        db.add(p)
        await db.commit()
        return p.id


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def inv(number):
    return (await q("select status::text, amount_paid, collectible_balance from invoices where invoice_number=:n", n=number))[0]


async def pay(pid):
    return (await q("select state::text, unapplied_amount, held_amount, credited_amount, amount_received_inr, customer_id, "
                    "matched_layer::text, confidence_score, match_reason from payments where id=:i", i=pid))[0]


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa Two", "fa.two@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            r = await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})
            assert r.status_code == 201, r.text
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}

        # ================================================================ reads
        A = await customer("Alpha")
        B = await customer("Beta")
        i_a1 = await invoice(A, "A-1", "900")
        i_a2 = await invoice(A, "A-2", "400")
        i_a3 = await invoice(A, "A-3", "500")
        i_b1 = await invoice(B, "B-1", "700")
        p1 = await payment(A, "900", ref="UTR-READ-1", payer="Alpha Traders", email="pay@alpha.test",
                           quoted=[("A-1", "900"), ("NOPE-9", None)], reasons=("Reason one", "Reason two"))
        p2 = await payment(B, "700", ref="UTR-READ-2", payer="Beta Corp")
        p3 = await payment(None, "123", ref="UTR-READ-3", payer="Mystery Ltd")

        check("no token -> 401", (await c.get("/payments")).status_code == 401)
        r = await c.get("/payments", headers=FA)
        check("FA can list payments", r.status_code == 200 and len(r.json()) == 3, str(r.status_code))
        lst = r.json()
        check("newest first", [x["payment_id"] for x in lst] == [str(p3), str(p2), str(p1)], str([x["reference_number"] for x in lst]))
        item = next(x for x in lst if x["payment_id"] == str(p1))
        check("list item fields", item["state"] == "NeedsReview" and item["customer_name"] == "Alpha Ltd" and item["currency"] == "INR" and D(str(item["amount"])) == 900 and item["reasons"] == ["Reason one", "Reason two"] and item["payer_name"] == "Alpha Traders", str(item)[:200])
        check("filter by state", len((await c.get("/payments?state=Applied", headers=FA)).json()) == 0 and len((await c.get("/payments?state=NeedsReview", headers=FA)).json()) == 3)
        check("filter by customer", [x["reference_number"] for x in (await c.get(f"/payments?customer_id={B}", headers=FA)).json()] == ["UTR-READ-2"])
        check("search by reference", [x["reference_number"] for x in (await c.get("/payments?search=read-3", headers=FA)).json()] == ["UTR-READ-3"])
        check("search by payer name", [x["reference_number"] for x in (await c.get("/payments?search=beta%20corp", headers=FA)).json()] == ["UTR-READ-2"])
        check("search by customer name", {x["reference_number"] for x in (await c.get("/payments?search=alpha", headers=FA)).json()} == {"UTR-READ-1"})
        check("limit and offset", [x["reference_number"] for x in (await c.get("/payments?limit=1&offset=1", headers=FA)).json()] == ["UTR-READ-2"])
        check("a bad state is refused", (await c.get("/payments?state=Nonsense", headers=FA)).status_code == 422)

        r = await c.get("/payments/reviews", headers=FA)
        check("/payments/reviews is not shadowed by /{id}; oldest first", r.status_code == 200 and [x["reference_number"] for x in r.json()] == ["UTR-READ-1", "UTR-READ-2", "UTR-READ-3"], str(r.status_code))
        d = (await c.get(f"/payments/{p1}", headers=FA)).json()
        check("detail: quoted invoices, known flag and status", [(x["invoice_number"], x["known"], x["invoice_status"]) for x in d["quoted_invoices"]] == [("A-1", True, "Open"), ("NOPE-9", False, None)], str(d["quoted_invoices"]))
        check("detail: open invoices offered to the reviewer", sorted(x["invoice_number"] for x in d["open_invoices"]) == ["A-1", "A-2", "A-3"], str([x["invoice_number"] for x in d["open_invoices"]]))
        check("detail: money identity ok, no allocations yet, extra fields", d["money_identity_ok"] and d["allocations"] == [] and d["from_email"] == "pay@alpha.test" and d["notes"] == [])
        check("detail of a payment with no customer offers no invoices", (await c.get(f"/payments/{p3}", headers=FA)).json()["open_invoices"] == [])
        check("unknown payment -> 404", (await c.get("/payments/00000000-0000-0000-0000-000000000000", headers=FA)).status_code == 404)
        inv_before = (await c.get(f"/invoices/{i_a1}", headers=FA)).json()
        check("invoice detail: payments empty before", inv_before["payments"] == [], str(inv_before.get("payments")))

        # ================================================================ manual match
        r = await c.post(f"/payments/{p1}/match", headers=FA, json={"lines": [{"invoice_id": str(i_a1), "amount": "900"}]})
        check("FA matches: 200, Applied, invoice Paid", r.status_code == 200 and r.json()["state"] == "Applied" and (await inv("A-1"))[0] == "Paid", r.text[:200])
        row = await pay(p1)
        check("a human decision: layer L4, no score, reason names the user", row[6] == "L4" and row[7] is None and "Fa Two" in row[8], str(row))
        check("allocation in the result", r.json()["allocations"] == [{"invoice_number": "A-1", "allocated": "900.00", "tolerance": "T1", "invoice_status": "Paid"}], str(r.json()["allocations"]))
        d = (await c.get(f"/payments/{p1}", headers=FA)).json()
        check("detail after: allocations listed, open invoices gone, timeline shows the decision", len(d["allocations"]) == 1 and d["allocations"][0]["invoice_number"] == "A-1" and d["open_invoices"] == [] and d["timeline"][-1]["to_state"] == "Applied" and d["money_identity_ok"], str(d["timeline"]))
        inv_after = (await c.get(f"/invoices/{i_a1}", headers=FA)).json()
        check("invoice detail: shows the payment that paid it", len(inv_after["payments"]) == 1 and inv_after["payments"][0]["payment_reference"] == "UTR-READ-1" and inv_after["payments"][0]["funded_by"] == "payment" and D(str(inv_after["payments"][0]["allocated_amount"])) == 900, str(inv_after["payments"]))
        r = await c.post(f"/payments/{p1}/match", headers=FA, json={"lines": [{"invoice_id": str(i_a2), "amount": "400"}]})
        check("matching an Applied payment again -> 409", r.status_code == 409, str(r.status_code))

        # customer-less payment: needs customer_id
        r = await c.post(f"/payments/{p3}/match", headers=FA, json={"lines": [{"invoice_id": str(i_a2), "amount": "123"}]})
        check("no customer on the payment and none given -> 422", r.status_code == 422 and "customer" in r.text.lower(), r.text[:120])
        r = await c.post(f"/payments/{p3}/match", headers=FA, json={"customer_id": str(A), "lines": [{"invoice_id": str(i_a2), "amount": "123"}]})
        row = await pay(p3)
        check("with customer_id: applied (short payment: PartlyPaid), customer set", r.status_code == 200 and row[5] == A and (await inv("A-2")) == ("PartlyPaid", D("123.00"), D("277.00")), r.text[:200])

        # validation
        pv = await payment(A, "1000", ref="UTR-VAL")
        for name, body, code in (
            ("lines beyond the payment", {"lines": [{"invoice_id": str(i_a2), "amount": "800"}, {"invoice_id": str(i_a3), "amount": "300"}]}, 422),
            ("an invoice of another customer", {"lines": [{"invoice_id": str(i_b1), "amount": "100"}]}, 422),
            ("the same invoice twice", {"lines": [{"invoice_id": str(i_a3), "amount": "100"}, {"invoice_id": str(i_a3), "amount": "100"}]}, 422),
            ("an unknown invoice", {"lines": [{"invoice_id": "00000000-0000-0000-0000-000000000001", "amount": "100"}]}, 404),
            ("no lines", {"lines": []}, 422),
            ("a zero amount", {"lines": [{"invoice_id": str(i_a3), "amount": "0"}]}, 422),
        ):
            r = await c.post(f"/payments/{pv}/match", headers=FA, json=body)
            check(f"match refused: {name}", r.status_code == code, f"{r.status_code} {r.text[:100]}")
        row = await pay(pv)
        check("refused matches changed nothing", row[0] == "NeedsReview" and row[1] == 0 and (await inv("A-3"))[0] == "Open", str(row[:3]))
        hold_id = await invoice(A, "A-H", "300", po_hold=True)
        r = await c.post(f"/payments/{pv}/match", headers=FA, json={"lines": [{"invoice_id": str(hold_id), "amount": "300"}]})
        check("an invoice on PO hold -> 409", r.status_code == 409 and "hold" in r.text, r.text[:120])

        # paid invoice needs confirmation
        paid_id = await invoice(A, "A-P", "600", status=InvoiceStatus.PAID, paid="600")
        async with SessionLocal() as db:  # a real earlier payment behind it
            prior = Payment(state=PaymentState.APPLIED, customer_id=A, payment_currency="INR", amount_received_inr=D("600"), reference_number="PRIOR", matched_layer=MatchedLayer.L1)
            db.add(prior)
            await db.flush()
            await db.execute(text("insert into payment_allocations (id, payment_id, invoice_id, allocated_amount, status) values (gen_random_uuid(), :p, :i, 600, 'Active')"), {"p": prior.id, "i": paid_id})
            await db.commit()
        r = await c.post(f"/payments/{pv}/match", headers=FA, json={"lines": [{"invoice_id": str(paid_id), "amount": "1000"}]})
        check("a line on an already-Paid invoice without confirmation -> 409", r.status_code == 409 and "already paid" in r.text, r.text[:140])
        r = await c.post(f"/payments/{pv}/match", headers=FA, json={"confirm_repayment": True, "lines": [{"invoice_id": str(paid_id), "amount": "1000"}]})
        nts = await q("select note_number, amount, remaining_amount, status::text, linked_invoice_id, linked_payment_id from notes where linked_payment_id=:p", p=pv)
        row = await pay(pv)
        check("confirmed re-payment: Applied, 1000 credited, one Open credit note linked to that invoice", r.status_code == 200 and row[0] == "Applied" and row[3] == D("1000.00") and len(nts) == 1 and nts[0][1] == D("1000.00") and nts[0][4] == paid_id and r.json()["credit_note_number"] == nts[0][0], r.text[:200])
        check("the Paid invoice itself did not change", (await inv("A-P")) == ("Paid", D("600.00"), D("0.00")))

        # over an open invoice -> credit note; remainder -> unapplied
        po_ = await payment(A, "700", ref="UTR-OVER")
        r = await c.post(f"/payments/{po_}/match", headers=FTM, json={"lines": [{"invoice_id": str(i_a3), "amount": "700"}]})
        row = await pay(po_)
        check("700 on a 500 invoice: Paid, 200 credit note", r.status_code == 200 and (await inv("A-3"))[0] == "Paid" and row[3] == D("200.00") and row[0] == "Applied", r.text[:160])
        c_id = await customer("Gamma")
        g1 = await invoice(c_id, "G-1", "1000")
        pr = await payment(c_id, "1500", ref="UTR-REM")
        r = await c.post(f"/payments/{pr}/match", headers=FA, json={"lines": [{"invoice_id": str(g1), "amount": "600"}]})
        row = await pay(pr)
        check("600 of 1500 on a 1000 invoice: invoice PartlyPaid, 900 stays unapplied, PartiallyApplied", r.status_code == 200 and (await inv("G-1")) == ("PartlyPaid", D("600.00"), D("400.00")) and row[0] == "PartiallyApplied" and row[1] == D("900.00"), f"{row[:4]} {r.text[:100]}")

        # foreign
        f_c = await customer("Delta")
        f1 = await invoice(f_c, "F-1", "83000", currency="USD", rate="83")
        f_inr = await invoice(f_c, "F-INR", "500")
        pf = await payment(f_c, "1000", currency="USD", ref="UTR-USD")
        r = await c.post(f"/payments/{pf}/match", headers=FA, json={"lines": [{"invoice_id": str(f1), "amount": "900"}]})
        check("foreign: lines must add up to what was paid -> 422", r.status_code == 422 and "add up" in r.text, r.text[:140])
        r = await c.post(f"/payments/{pf}/match", headers=FA, json={"lines": [{"invoice_id": str(f_inr), "amount": "1000"}]})
        check("foreign payment against an INR invoice -> 422", r.status_code == 422, r.text[:140])
        r = await c.post(f"/payments/{pf}/match", headers=FA, json={"lines": [{"invoice_id": str(f1), "amount": "1000"}]})
        row = await pay(pf)
        al = await q("select fx_rate_used, tolerance_applied::text from payment_allocations where payment_id=:p", p=pf)
        check("foreign: USD 1000 at the frozen rate 83 pays the invoice; INR value 83000", r.status_code == 200 and row[4] == D("83000.00") and al == [(D("83.00000000"), "T1")] and (await inv("F-1"))[0] == "Paid", f"{row[4]} {al}")

        # ================================================================ alias
        e_c = await customer("Eps")
        e1 = await invoice(e_c, "E-1", "100")
        pe = await payment(None, "100", ref="UTR-AL", payer="  Epsilon   Traders  ", email="Pay@Epsilon.TEST")
        r = await c.post(f"/payments/{pe}/match", headers=FA, json={"customer_id": str(e_c), "save_alias": "name", "lines": [{"invoice_id": str(e1), "amount": "100"}]})
        check("FA cannot save an alias (no customers.alias.confirm) -> 403, and nothing was applied", r.status_code == 403 and (await pay(pe))[0] == "NeedsReview" and (await inv("E-1"))[0] == "Open", f"{r.status_code} {r.text[:100]}")
        r = await c.post(f"/payments/{pe}/match", headers=FTM, json={"customer_id": str(e_c), "save_alias": "name", "lines": [{"invoice_id": str(e1), "amount": "100"}]})
        al = await q("select alias_type::text, alias_value, source::text from customer_aliases where customer_id=:c", c=e_c)
        check("FTM matches and saves the payer name as an alias (spaces collapsed)", r.status_code == 200 and al == [("name", "Epsilon Traders", "human_review")], f"{r.status_code} {al}")
        pe2 = await payment(e_c, "50", ref="UTR-AL2", payer="Epsilon Traders", email="Pay@Epsilon.TEST")
        r = await c.post(f"/payments/{pe2}/save-alias", headers=FA, json={"alias_type": "email"})
        check("save-alias needs customers.alias.confirm: FA -> 403", r.status_code == 403, str(r.status_code))
        r = await c.post(f"/payments/{pe2}/save-alias", headers=FTM, json={"alias_type": "email"})
        al = await q("select alias_type::text, alias_value from customer_aliases where customer_id=:c order by 1", c=e_c)
        check("save-alias: the sender e-mail is stored lower-case", r.status_code == 201 and ("email", "pay@epsilon.test") in al, f"{r.status_code} {al}")
        r = await c.post(f"/payments/{pe2}/save-alias", headers=FTM, json={"alias_type": "email"})
        check("the same alias again -> 409", r.status_code == 409, str(r.status_code))
        pn = await payment(e_c, "10", ref="UTR-NOEMAIL")
        r = await c.post(f"/payments/{pn}/save-alias", headers=FTM, json={"alias_type": "email"})
        check("an advice with no e-mail and no alias_value -> 422", r.status_code == 422, r.text[:100])
        r = await c.post(f"/payments/{pn}/save-alias", headers=FTM, json={"alias_type": "email", "alias_value": "AP@Epsilon2.test"})
        check("alias_value given explicitly", r.status_code == 201 and r.json()["alias_value"] == "ap@epsilon2.test", r.text[:100])

        # ================================================================ unapplied
        u_c = await customer("Zeta")
        u1 = await invoice(u_c, "U-1", "800")
        pu = await payment(u_c, "800", ref="UTR-UNAP")
        r = await c.post(f"/payments/{pu}/unapplied", headers=FA, json={"reason": "Customer will confirm the invoice later"})
        row = await pay(pu)
        check("unapplied: state Unapplied, whole amount unapplied", r.status_code == 200 and row[0] == "Unapplied" and row[1] == D("800.00") and row[4] == D("800.00"), r.text[:150])
        check("unapplied: still decidable, listed with its open invoices", (await c.get(f"/payments/{pu}", headers=FA)).json()["open_invoices"] != [] and (await c.get(f"/payments?state=Unapplied", headers=FA)).json()[0]["payment_id"] == str(pu))
        r = await c.post(f"/payments/{pu}/reject", headers=FA, json={"reason": "no"})
        check("an Unapplied payment cannot be rejected (it holds money) -> 409", r.status_code == 409)
        r = await c.post(f"/payments/{pu}/match", headers=FA, json={"lines": [{"invoice_id": str(u1), "amount": "800"}]})
        row = await pay(pu)
        check("apply later: the Unapplied payment is matched, invoice Paid, nothing left unapplied", r.status_code == 200 and row[0] == "Applied" and row[1] == 0 and (await inv("U-1"))[0] == "Paid", r.text[:150])
        pu2 = await payment(None, "30", ref="UTR-UNAP2")
        r = await c.post(f"/payments/{pu2}/unapplied", headers=FA, json={"reason": "x"})
        check("unapplied without a customer -> 422", r.status_code == 422, r.text[:100])
        r = await c.post(f"/payments/{pu2}/unapplied", headers=FA, json={"reason": "x", "customer_id": str(u_c)})
        check("unapplied with customer_id given", r.status_code == 200 and (await pay(pu2))[5] == u_c)
        real = review_module.rate_for_invoice

        async def fake_rate(currency, on):
            return SimpleNamespace(inr_per_unit=D("80"))

        review_module.rate_for_invoice = fake_rate
        try:
            pfu = await payment(u_c, "100", currency="USD", ref="UTR-FU")
            r = await c.post(f"/payments/{pfu}/unapplied", headers=FA, json={"reason": "foreign, no invoice yet"})
        finally:
            review_module.rate_for_invoice = real
        row = await pay(pfu)
        check("foreign unapplied is valued at the ECB rate for its date (USD 100 x 80)", r.status_code == 200 and row[4] == D("8000.00") and row[1] == D("8000.00"), r.text[:120])
        # a foreign payment left unapplied is valued at the open invoices' frozen rate when they share one
        calls = []

        async def spy_rate(currency, on):
            calls.append(currency)
            return SimpleNamespace(inr_per_unit=D("80"))

        review_module.rate_for_invoice = spy_rate
        try:
            fz = await customer("Frozen")
            fz1 = await invoice(fz, "FZ-1", "8300", currency="USD", rate="83")
            pfz = await payment(fz, "100", currency="USD", ref="UTR-FZ")
            r = await c.post(f"/payments/{pfz}/unapplied", headers=FA, json={"reason": "waiting"})
            row = await pay(pfz)
            check("unapplied USD, customer's open USD invoices share rate 83: valued 8300, ECB not called",
                  r.status_code == 200 and row[4] == D("8300.00") and row[1] == D("8300.00") and calls == [] and "frozen rate 83" in row[8], f"{row[4]} {calls} {row[8][:120]}")
            mx = await customer("Mixed")
            mx1 = await invoice(mx, "MX-1", "8300", currency="USD", rate="83")
            mx2 = await invoice(mx, "MX-2", "8500", currency="USD", rate="85")
            pmx = await payment(mx, "100", currency="USD", ref="UTR-MX")
            r = await c.post(f"/payments/{pmx}/unapplied", headers=FA, json={"reason": "waiting"})
            row = await pay(pmx)
            check("unapplied USD, open invoices with different rates: falls back to the ECB rate (80)",
                  r.status_code == 200 and row[4] == D("8000.00") and calls == ["USD"] and "ECB" in row[8], f"{row[4]} {calls}")
        finally:
            review_module.rate_for_invoice = real
        r = await c.post(f"/payments/{pmx}/match", headers=FA, json={"lines": [{"invoice_id": str(mx2), "amount": "100"}]})
        row = await pay(pmx)
        check("applying it later replaces the provisional value with the invoice's frozen rate (85): 8500, Paid, nothing unapplied",
              r.status_code == 200 and row[0] == "Applied" and row[4] == D("8500.00") and row[1] == 0 and (await inv("MX-2"))[0] == "Paid", f"{row[:5]} {r.text[:100]}")
        async with SessionLocal() as db:
            from core.services.payments.identities import verify_money_identities as _v
            check("the money identity holds after the replacement", await _v(db) == [])
        pna = await payment(u_c, None, ref="UTR-NOAMT")
        r = await c.post(f"/payments/{pna}/unapplied", headers=FA, json={"reason": "x"})
        check("unapplied needs an amount -> 422", r.status_code == 422 and "amount" in r.text.lower(), r.text[:100])

        # ================================================================ reject
        pr1 = await payment(None, "55", ref="UTR-REJ")
        r = await c.post(f"/payments/{pr1}/reject", headers=FA, json={"reason": ""})
        check("reject needs a reason -> 422", r.status_code == 422)
        r = await c.post(f"/payments/{pr1}/reject", headers=FA, json={"reason": "Not our customer"})
        row = await pay(pr1)
        check("reject: Rejected, every bucket zero, reason kept", r.status_code == 200 and row[0] == "Rejected" and row[1] == 0 and row[2] == 0 and row[3] == 0 and "Not our customer" in row[8], r.text[:120])
        r = await c.post(f"/payments/{pr1}/reject", headers=FA, json={"reason": "again"})
        check("rejecting twice -> 409", r.status_code == 409)

        # ================================================================ amount
        m_c = await customer("Eta")
        m1 = await invoice(m_c, "M-1", "900")
        pm = await payment(m_c, None, ref="UTR-AMT", quoted=[("M-1", None)], reasons=("The advice does not state the amount paid; please find it and enter it",))
        r = await c.post(f"/payments/{pm}/amount", headers=FA, json={"amount": "-5"})
        check("a negative amount -> 422", r.status_code == 422)
        r = await c.post(f"/payments/{pm}/amount", headers=FA, json={"amount": "900"})
        row = await pay(pm)
        log = await q("select reason from state_log where entity_type='payment' and entity_id=:i order by occurred_at", i=pm)
        check("amount entered: logged as entered by the user, matching runs and applies it", r.status_code == 200 and r.json()["state"] == "Applied" and row[0] == "Applied" and (await inv("M-1"))[0] == "Paid", r.text[:200])
        check("the log says who entered the amount", any("entered by Fa Two" in x[0] for x in log), str(log))
        r = await c.post(f"/payments/{pm}/amount", headers=FA, json={"amount": "900"})
        check("a second amount -> 409", r.status_code == 409)
        pm2 = await payment(m_c, "10", ref="UTR-HASAMT")
        r = await c.post(f"/payments/{pm2}/amount", headers=FA, json={"amount": "20"})
        check("amount on a payment that already has one -> 409", r.status_code == 409)
        pm3 = await payment(m_c, None, ref="UTR-NOCUR", currency="INR")
        async with SessionLocal() as db:
            await db.execute(text("update payments set payment_currency=null where id=:i"), {"i": pm3})
            await db.commit()
        r = await c.post(f"/payments/{pm3}/amount", headers=FA, json={"amount": "40"})
        check("no currency on the advice and none given -> 422", r.status_code == 422 and "currency" in r.text.lower(), r.text[:100])
        r = await c.post(f"/payments/{pm3}/amount", headers=FA, json={"amount": "40", "currency": "inr"})
        check("currency supplied: accepted; no open invoice for this customer match -> still NeedsReview or applied", r.status_code == 200, r.text[:140])

        # ================================================================ concurrency
        k_c = await customer("Theta")
        k1 = await invoice(k_c, "K-1", "900")
        pk = await payment(k_c, "900", ref="UTR-RACE1")
        body = {"lines": [{"invoice_id": str(k1), "amount": "900"}]}
        r1, r2 = await asyncio.gather(c.post(f"/payments/{pk}/match", headers=FA, json=body), c.post(f"/payments/{pk}/match", headers=FTM, json=body))
        codes = sorted([r1.status_code, r2.status_code])
        check("two reviewers decide the same payment at once: one 200, one 409", codes == [200, 409], str(codes))
        check("the invoice was paid exactly once", (await inv("K-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("K-1")))
        l_c = await customer("Iota")
        l1 = await invoice(l_c, "L-1", "900")
        pl1 = await payment(l_c, "900", ref="UTR-R2A")
        pl2 = await payment(l_c, "900", ref="UTR-R2B")
        body = {"lines": [{"invoice_id": str(l1), "amount": "900"}]}
        r1, r2 = await asyncio.gather(c.post(f"/payments/{pl1}/match", headers=FA, json=body), c.post(f"/payments/{pl2}/match", headers=FTM, json=body))
        codes = sorted([r1.status_code, r2.status_code])
        check("two payments for one invoice at once: one 200, the other refused (already paid)", codes == [200, 409], f"{codes} {r1.text[:80]} {r2.text[:80]}")
        check("that invoice is paid once, not twice", (await inv("L-1")) == ("Paid", D("900.00"), D("0.00")), str(await inv("L-1")))

        # ================================================================ the whole book
        async with SessionLocal() as db:
            found = await verify_money_identities(db)
        check("money identities hold after every review decision", found == [], str([str(v) for v in found][:4]))
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
