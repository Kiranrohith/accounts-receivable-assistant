"""Purchase-order numbers are compared without case, spaces or punctuation (PO-123 = PO 123 = po123).
Real database, no model, no mail. Proves: the generated columns hold the normalized number; an invoice quoting a PO
under another spelling is linked to it when it arrives after the PO (po_first) and when the PO arrives after it
(invoice_first); a different number is still not linked; the same number of ANOTHER customer is never linked and is
still reported as theirs; a customer cannot hold two POs that differ only by spelling (the database refuses the
second), yet two customers can each hold the same number; a number of nothing but punctuation matches nothing."""

import asyncio
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from config.settings import get_settings
from core.services.purchase_orders import fulfillment
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, InvoiceLineItem, PurchaseOrder, PurchaseOrderLineItem
from data.repositories.purchase_orders import purchase_orders_repo as po_repo

s = get_settings()
assert "ar_ponorm" in s.database_url, "refusing to run against a non-test database"
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
        c = Customer(customer_name=name, contact="C", email=f"ap{_n[0]}@ponorm.test", gstin=f"29ABCDE{_n[0]:04d}F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.commit()
        return c.id


async def purchase_order(customer_id, number):
    async with SessionLocal() as db:
        po = PurchaseOrder(customer_id=customer_id, po_number=number, po_date=date(2026, 10, 1), currency="INR", total_amount=Decimal("1000"))
        db.add(po)
        await db.flush()
        db.add(PurchaseOrderLineItem(po_id=po.id, description="Widgets", normalized_description="widgets", quantity=Decimal("10")))
        await db.commit()
        return po.id


async def invoice(customer_id, number, po_number):
    async with SessionLocal() as db:
        inv = Invoice(customer_id=customer_id, invoice_number=number, invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                      invoice_currency="INR", po_number=po_number, amount_inr=Decimal("1000"), collectible_total=Decimal("1000"))
        db.add(inv)
        await db.flush()
        db.add(InvoiceLineItem(invoice_id=inv.id, description="Widgets", quantity=Decimal("4")))
        await db.commit()
        return inv.id


async def linked_to(invoice_id):
    rows = await q("select po_id, match_direction from po_invoice_matches where invoice_id=:i", i=invoice_id)
    return rows[0] if rows else None


async def po_first(invoice_id):
    """The invoice arrives: look for the customer's PO (the real service call), commit."""
    async with SessionLocal() as db:
        inv = await db.get(Invoice, invoice_id)
        result = await fulfillment.link_new_invoice(db, inv, None)
        await db.commit()
        return result


async def invoice_first(po_id):
    """The PO arrives: link the customer's waiting invoices (the real service call), commit."""
    async with SessionLocal() as db:
        po = await db.get(PurchaseOrder, po_id)
        out = await fulfillment.retro_link(db, po, None)
        await db.commit()
        return [i.id for i, _ in out]


async def main():
    acme = await customer("Acme Traders")
    other = await customer("Other Buyers")

    po = await purchase_order(acme, "PO-123")
    norm = (await q("select po_number_norm from purchase_orders where id=:i", i=po))[0][0]
    check("the generated column: PO-123 is stored as PO123", norm == "PO123", norm)

    # PO first: an invoice arrives quoting the number another way
    for n, quoted in enumerate(("PO 123", "po123", "P.O./123", " PO-123 "), start=1):
        i = await invoice(acme, f"INV-A{n}", quoted)
        result = await po_first(i)
        link = await linked_to(i)
        check(f"po_first: {quoted!r} links to PO-123", result is not None and link is not None and link[0] == po and link[1] == "po_first", str(link))
    stored = (await q("select po_number_norm from invoices where invoice_number='INV-A2'"))[0][0]
    check("the invoice column holds the same normalized number", stored == "PO123", stored)

    i = await invoice(acme, "INV-B1", "PO-1234")
    check("po_first: a different number (PO-1234) is not linked", await po_first(i) is None and await linked_to(i) is None)
    i = await invoice(acme, "INV-B2", "123")
    check("po_first: the bare digits (123) are not PO-123", await po_first(i) is None and await linked_to(i) is None)
    i = await invoice(acme, "INV-B3", None)
    check("po_first: an invoice without a PO number links nothing", await po_first(i) is None)

    # the same number belongs to ANOTHER customer: never linked, and still named as theirs
    theirs = await invoice(other, "INV-C1", "PO-123")
    check("po_first: another customer's invoice is not linked to Acme's PO", await po_first(theirs) is None and await linked_to(theirs) is None)
    async with SessionLocal() as db:
        owner = await po_repo.owner_of_number_elsewhere(db, other, "po 123")
        check("the number is reported as Acme's, whatever the spelling", owner == "Acme Traders", str(owner))
        check("and not as the owner's own", await po_repo.owner_of_number_elsewhere(db, acme, "po 123") is None)

    # invoice first: invoices wait, then the PO arrives under another spelling
    w1 = await invoice(acme, "INV-D1", "PO/777")
    w2 = await invoice(acme, "INV-D2", "po 777")
    w3 = await invoice(acme, "INV-D3", "PO-7777")
    w4 = await invoice(other, "INV-D4", "PO 777")
    po777 = await purchase_order(acme, "PO-777")
    got = await invoice_first(po777)
    check("invoice_first: PO-777 links the two waiting spellings, not PO-7777, not the other customer's", set(got) == {w1, w2}, str(got))
    check("they are linked invoice_first", (await linked_to(w1))[1] == "invoice_first" and await linked_to(w3) is None and await linked_to(w4) is None)

    # the lookup itself
    async with SessionLocal() as db:
        found = await po_repo.get_by_customer_number(db, acme, "Po-777")
        check("get_by_customer_number ignores case and punctuation", found is not None and found.id == po777)
        check("it stays inside the customer", await po_repo.get_by_customer_number(db, other, "PO-777") is None)
        check("punctuation alone names nothing", await po_repo.get_by_customer_number(db, acme, "---") is None)
        check("nor does it find invoices", await po_repo.unlinked_invoices(db, acme, "///") == [])

    # one customer cannot hold two POs that differ only by spelling; two customers can hold the same number
    refused = False
    try:
        await purchase_order(acme, "po 123")
    except IntegrityError:
        refused = True
    check("a second PO 'po 123' for Acme is refused by the database", refused)
    await purchase_order(other, "PO-123")
    check("the other customer can hold PO-123 itself", (await q("select count(*) from purchase_orders where po_number_norm='PO123'"))[0][0] == 2)

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
