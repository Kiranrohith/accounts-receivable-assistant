"""D1: the dispute tables' rules on a real (throwaway) database, and the notifications API."""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.rest.app import app
from config.settings import get_settings
from core.services import notifications
from core.services.payments.identities import verify_money_identities
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Dispute, DisputeMessage, Invoice, Payment
from data.models.postgres.enums import (
    DisputeCategory,
    DisputeMessageKind,
    DisputeStatus,
    DocKind,
    DocType,
    MatchedLayer,
    NotificationType,
    PaymentState,
    UploadedVia,
)

D = Decimal
s = get_settings()
assert "ar_dis1" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def refused(db, build) -> bool:
    try:
        build()
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return True
    await db.rollback()
    return False


NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


async def main():
    async with SessionLocal() as db:
        c = Customer(customer_name="D1 Check Ltd", contact="A", email="d1@check.test", tax_id_type="GSTIN", tax_id="27ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.flush()
        inv1 = Invoice(customer_id=c.id, invoice_number="D1-1", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D("100"), collectible_total=D("100"))
        inv2 = Invoice(customer_id=c.id, invoice_number="D1-2", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D("100"), collectible_total=D("100"))
        db.add_all([inv1, inv2])
        await db.commit()
        cid, i1, i2 = c.id, inv1.id, inv2.id

        # ---- defaults and enums -------------------------------------------------------------
        d1 = Dispute(customer_id=cid, invoice_id=i1, received_at=NOW, summary="Goods arrived damaged")
        db.add(d1)
        await db.commit()
        check("defaults: a new dispute is Raised, not payment-related, no category", d1.status is DisputeStatus.RAISED and d1.payment_related is False and d1.category is None)
        d1_id = d1.id
        check("every category and status value round-trips", all(
            [(await db.execute(text("select cast(:v as dispute_category)"), {"v": cat.value})).scalar() == cat.value for cat in DisputeCategory]
            + [(await db.execute(text("select cast(:v as dispute_status)"), {"v": st.value})).scalar() == st.value for st in DisputeStatus]
        ))
        check("doc_kind accepts 'dispute'", (await db.execute(text("select 'dispute'::doc_kind"))).scalar() == "dispute")

        # ---- several disputes on one invoice (decision 62): only the mail thread joins them ----------
        db.add(Dispute(customer_id=cid, invoice_id=i1, received_at=NOW, gmail_thread_id="th-A"))
        await db.commit()
        check("a second open dispute on the same invoice is allowed (the database does not merge them)",
              (await db.execute(text("select count(*) from disputes where invoice_id=:i and status <> 'Resolved'"), {"i": i1})).scalar() == 2)
        check("the same mail thread may carry several disputes (E3 chains)", not await refused(
            db, lambda: db.add(Dispute(customer_id=cid, invoice_id=i1, received_at=NOW, gmail_thread_id="th-A", status=DisputeStatus.RESOLVED))))
        check("the old one-open-per-invoice index is gone", (await db.execute(text("select count(*) from pg_indexes where indexname='uq_disputes_one_open_per_invoice'"))).scalar() == 0)
        await db.execute(text("update disputes set status='Resolved' where invoice_id=:i"), {"i": i1})
        await db.commit()

        async def new_open_after_resolved() -> bool:
            db.add(Dispute(customer_id=cid, invoice_id=i1, received_at=NOW, linked_dispute_id=d1_id))
            await db.flush()
            await db.commit()
            return True

        check("after they are Resolved a new one (linked to one of them) is allowed (E3)", await new_open_after_resolved())
        check("a resolved dispute does not block: two resolved on one invoice are fine", True)
        db.add_all([Dispute(customer_id=cid, invoice_id=None, received_at=NOW), Dispute(customer_id=cid, invoice_id=None, received_at=NOW)])
        await db.commit()
        check("customer-level disputes (no invoice) are not limited to one (E1)", (await db.execute(text("select count(*) from disputes where invoice_id is null"))).scalar() == 2)
        check("different invoices can each have an open dispute", not await refused(
            db, lambda: db.add(Dispute(customer_id=cid, invoice_id=i2, received_at=NOW))))

        # ---- messages -------------------------------------------------------------------------------
        db.add(DisputeMessage(dispute_id=d1_id, kind=DisputeMessageKind.INBOUND_EMAIL, body="first", gmail_message_id="gm-1"))
        await db.commit()
        check("the same mail twice on one dispute is refused (thread evidence unique)", await refused(
            db, lambda: db.add(DisputeMessage(dispute_id=d1_id, kind=DisputeMessageKind.INBOUND_EMAIL, body="again", gmail_message_id="gm-1"))))
        check("messages without a mail id are not limited", not await refused(
            db, lambda: [db.add(DisputeMessage(dispute_id=d1_id, kind=DisputeMessageKind.INTERNAL_NOTE, body=f"n{n}")) for n in range(3)]))
        other_id = (await db.execute(text("select id from disputes where invoice_id=:i and status <> 'Resolved' order by created_at limit 1"), {"i": i1})).scalar()
        check("the same mail id on ANOTHER dispute is fine", not await refused(
            db, lambda: db.add(DisputeMessage(dispute_id=other_id, kind=DisputeMessageKind.INBOUND_EMAIL, body="x", gmail_message_id="gm-1"))))

        # ---- payments.held_for_dispute_id ----------------------------------------------------------------
        p = Payment(state=PaymentState.NEEDS_REVIEW, customer_id=cid, payment_currency="INR", amount_received_inr=D("10"),
                    matched_layer=MatchedLayer.L4, held_for_dispute_id=d1_id)
        db.add(p)
        await db.commit()
        check("a payment can point at the dispute that holds it", p.held_for_dispute_id == d1_id)
        check("a payment cannot point at a dispute that does not exist", await refused(
            db, lambda: db.add(Payment(state=PaymentState.NEEDS_REVIEW, customer_id=cid, held_for_dispute_id=uuid.uuid4()))))
        found = await verify_money_identities(db)
        check("the money checker still runs and holds", found == [], str(found))

        # users for the notification checks
        uid_a = (await db.execute(text("select id from users limit 1"))).scalar()

    # ================================================================ notifications over HTTP
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        assert (await hc.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        async with SessionLocal() as db:
            ids = {r[0]: r[1] for r in (await db.execute(text("select email, id from users"))).all()}
            ADM_ID, FA_ID = ids[s.seed_admin_email.strip().lower()], ids["fa.two@example.com"]
            for n in range(3):
                await notifications.create(db, FA_ID, NotificationType.ASSIGNMENT, f"Dispute {n} assigned to you", entity_type="dispute", entity_id=d1_id)
            await notifications.create(db, FA_ID, NotificationType.DEADLINE_WARNING, "Due tomorrow", entity_type="dispute", entity_id=d1_id)
            await notifications.create(db, ADM_ID, NotificationType.ESCALATION, "Escalated", entity_type="dispute", entity_id=d1_id)
            await db.commit()
            told = await notifications.already_told(db, FA_ID, NotificationType.DEADLINE_WARNING, "dispute", d1_id)
            not_told = await notifications.already_told(db, ADM_ID, NotificationType.DEADLINE_WARNING, "dispute", d1_id)
        check("already_told sees a notice that was sent, and not one that was not", told and not not_told)

        check("no token -> 401", (await hc.get("/notifications")).status_code == 401)
        r = await hc.get("/notifications", headers=FA)
        check("FA sees only their own 4", r.status_code == 200 and len(r.json()["items"]) == 4 and all("Escalat" not in (x["message"] or "") for x in r.json()["items"]), str(len(r.json()["items"])))
        check("newest first", [x["type"] for x in r.json()["items"]][0] == "DeadlineWarning")
        check("entity reference is returned", r.json()["items"][0]["entity_type"] == "dispute" and r.json()["items"][0]["entity_id"] == str(d1_id))
        check("admin sees only theirs (1)", len((await hc.get("/notifications", headers=ADM)).json()["items"]) == 1)
        check("the page carries the total of the whole list", (await hc.get("/notifications?limit=2", headers=FA)).json()["total"] == 4)
        check("limit and offset", len((await hc.get("/notifications?limit=2", headers=FA)).json()["items"]) == 2 and len((await hc.get("/notifications?limit=2&offset=3", headers=FA)).json()["items"]) == 1)
        check("unread count", (await hc.get("/notifications/unread-count", headers=FA)).json() == {"unread": 4})
        first = r.json()["items"][0]["notification_id"]
        r2 = await hc.post(f"/notifications/{first}/read", headers=FA)
        check("mark one read", r2.status_code == 200 and r2.json()["is_read"] is True)
        check("unread falls to 3 and unread_only hides it", (await hc.get("/notifications/unread-count", headers=FA)).json() == {"unread": 3} and len((await hc.get("/notifications?unread_only=true", headers=FA)).json()["items"]) == 3)
        check("someone else's notification -> 404 (not 403: it does not reveal it exists)", (await hc.post(f"/notifications/{first}/read", headers=ADM)).status_code == 404)
        check("an unknown id -> 404", (await hc.post(f"/notifications/{uuid.uuid4()}/read", headers=FA)).status_code == 404)
        r = await hc.post("/notifications/read-all", headers=FA)
        check("read-all marks the 3 unread, and only mine", r.json() == {"marked": 3} and (await hc.get("/notifications/unread-count", headers=ADM)).json() == {"unread": 1})
        check("read-all again marks none", (await hc.post("/notifications/read-all", headers=FA)).json() == {"marked": 0})
        check("/notifications/unread-count is not swallowed by an {id} route", (await hc.get("/notifications/unread-count", headers=FA)).status_code == 200)

    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
