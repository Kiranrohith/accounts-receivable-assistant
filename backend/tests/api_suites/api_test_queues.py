"""The decision queues (GET /queues, GET /queues/items). Real database, no model. Proves: each role sees only the queues its
permissions allow, the reviews of someone's own uploads are theirs alone, every queue's selector runs and counts what the
queue's own endpoint lists, the merged list pages, sorts and searches in the database, and a queue you may not work is
refused."""

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Dispute, IngestedEmail, Invoice, Payment, PendingInvoiceChange, Reminder, SourceDocument, Task
from data.models.postgres.enums import (
    DisputeStatus, DocKind, EmailClassification, EmailStatus, InvoiceStatus, InvoiceType, PaymentState, PendingChangeReason,
    ReminderState, TaskStatus, TaskType, UploadedVia,
)

D = Decimal
s = get_settings()
assert "ar_queues" in s.database_url, "refusing to run against a non-test database"
RUN = uuid.uuid4().hex[:6]
NOW = datetime.now(timezone.utc)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def user_id(email):
    async with SessionLocal() as db:
        return (await db.execute(text("select id from users where email=:e"), {"e": email})).scalar()


def minutes_ago(n):
    return NOW - timedelta(minutes=n)


async def seed(fa, ftm):
    """One or more items in every queue, with distinct created_at so the order is not a coin toss."""
    async with SessionLocal() as db:
        cust = Customer(customer_name=f"Queue Test {RUN} Ltd", contact="A", email=f"ap@q-{RUN}.test", gstin="29QUEUE0001F1Z5",
                        deducts_tds=False, billing_currency="INR", country="India")
        db.add(cust)
        await db.flush()
        inv = Invoice(customer_id=cust.id, invoice_number=f"Q-INV-{RUN}", type=InvoiceType.INVOICE, invoice_date=date(2026, 8, 1),
                      due_date=date(2026, 8, 31), invoice_currency="INR", amount_inr=D("9000"), collectible_total=D("9000"),
                      amount_paid=D("0"), status=InvoiceStatus.OPEN)
        db.add(inv)
        await db.flush()

        doc = SourceDocument(doc_kind=DocKind.INVOICE, uploaded_via=UploadedVia.UPLOAD, file_name="scan-1.pdf", file_hash=uuid.uuid4().hex)
        db.add(doc)
        await db.flush()

        def pending(reason, snapshot, by, minutes, invoice_id=None, doc_id=None):
            db.add(PendingInvoiceChange(reason=reason, proposed_snapshot=snapshot, created_by=by, invoice_id=invoice_id,
                                        source_document_id=doc_id, created_at=minutes_ago(minutes)))

        X = PendingChangeReason.EXTRACTION_REVIEW
        pending(X, {"kind": "doc_type", "reasons": ["Could be an invoice or a PO"], "classification": {"doc_class": "invoice"}}, fa, 50, doc_id=doc.id)
        pending(X, {"reasons": ["Total does not match the lines"], "extraction": {"invoice_number": f"INV-Q1-{RUN}", "customer_name": "Alpha Ltd", "total_amount": "1,234", "currency": "INR"}}, fa, 40)
        pending(X, {"reasons": [], "extraction": {"invoice_number": f"INV-Q2-{RUN}", "customer_name": "Beta Ltd", "total_amount": "5000.50", "currency": "INR"}}, fa, 30)
        pending(X, {"kind": "purchase_order", "reasons": ["No PO number"], "extraction": {"po_number": f"PO-Q1-{RUN}", "customer_name": "Alpha Ltd", "total_amount": "100", "currency": "INR"}}, fa, 20)
        pending(X, {"reasons": ["Someone else's upload"], "extraction": {"invoice_number": f"INV-FTM-{RUN}", "customer_name": "Gamma Ltd", "total_amount": "10", "currency": "INR"}}, ftm, 10)
        pending(PendingChangeReason.VERSION_UPLOAD, {"reasons": ["Amount changed"], "changes": [], "can_approve": True, "base_version": 1}, fa, 25, invoice_id=inv.id)
        inv2 = Invoice(customer_id=cust.id, invoice_number=f"Q-INV2-{RUN}", type=InvoiceType.INVOICE, invoice_date=date(2026, 8, 2),
                       due_date=date(2026, 9, 1), invoice_currency="INR", amount_inr=D("500"), collectible_total=D("500"),
                       amount_paid=D("0"), status=InvoiceStatus.OPEN)
        db.add(inv2)
        await db.flush()
        pending(PendingChangeReason.PO_MISMATCH, {"reasons": ["Quantity above the PO"], "po_number": "PO-X", "lines": []}, None, 15, invoice_id=inv2.id)

        for state, amount, minutes, reasons in (
            (PaymentState.NEEDS_REVIEW, D("45000"), 60, ["Two invoices fit"]),
            (PaymentState.NEEDS_REVIEW, D("700"), 35, None),
            (PaymentState.UNAPPLIED, D("300"), 12, None),
        ):
            db.add(Payment(state=state, customer_id=cust.id, amount_received_inr=amount, unapplied_amount=amount if state is PaymentState.UNAPPLIED else D("0"),
                           payment_currency="INR", reference_number=f"UTR-{minutes}-{RUN}", review_reasons=reasons, created_at=minutes_ago(minutes)))

        db.add(IngestedEmail(gmail_message_id=f"gm-q1-{RUN}", sender=f"who@{RUN}.test", subject=f"Re: your invoice {RUN}", received_at=minutes_ago(45),
                             classification=EmailClassification.PAYMENT, classification_confidence=62, status=EmailStatus.HUMAN_TRIAGE))
        db.add(IngestedEmail(gmail_message_id=f"gm-q2-{RUN}", sender="x@y.test", subject="done already", received_at=minutes_ago(5),
                             classification=EmailClassification.GENERAL, status=EmailStatus.PROCESSED))

        db.add(Reminder(invoice_id=inv.id, reminder_tier=15, state=ReminderState.DRAFTED, draft_body="Subject: x\n\nbody", created_at=minutes_ago(18)))
        db.add(Reminder(invoice_id=inv.id, reminder_tier=1, state=ReminderState.SENT, created_at=minutes_ago(90)))

        today = date.today()
        db.add(Dispute(customer_id=cust.id, invoice_id=None, received_at=minutes_ago(55), status=DisputeStatus.RAISED, summary="a"))
        db.add(Dispute(customer_id=None, invoice_id=None, received_at=minutes_ago(33), status=DisputeStatus.RAISED, summary="b"))
        db.add(Dispute(customer_id=cust.id, invoice_id=inv.id, received_at=minutes_ago(22), status=DisputeStatus.ASSIGNED, assigned_to=fa,
                       first_response_due=today + timedelta(days=2), resolution_due=today + timedelta(days=5), summary="mine"))
        db.add(Dispute(customer_id=cust.id, invoice_id=inv2.id, received_at=minutes_ago(21), status=DisputeStatus.ESCALATED, assigned_to=ftm,
                       first_response_due=today - timedelta(days=3), resolution_due=today + timedelta(days=1), summary="escalated"))
        db.add(Dispute(customer_id=cust.id, invoice_id=None, received_at=minutes_ago(80), status=DisputeStatus.RESOLVED, assigned_to=fa, summary="done"))

        db.add(Task(type=TaskType.COLLECTION_60D, invoice_id=inv.id, customer_id=cust.id, assigned_to=ftm, status=TaskStatus.OPEN, created_at=minutes_ago(9)))
        db.add(Task(type=TaskType.COLLECTION_60D, invoice_id=inv2.id, customer_id=cust.id, assigned_to=fa, status=TaskStatus.OPEN, created_at=minutes_ago(8)))
        db.add(Task(type=TaskType.COLLECTION_60D, invoice_id=inv2.id, customer_id=cust.id, assigned_to=ftm, status=TaskStatus.CLOSED, created_at=minutes_ago(7)))
        await db.commit()
        return inv.id


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa Q", "fa.q@example.com", "FA"), ("Ftm Q", "ftm.q@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.q@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.q@example.com', 'pass12345')).json()['access_token']}"}
        fa_id, ftm_id = await user_id("fa.q@example.com"), await user_id("ftm.q@example.com")
        await seed(fa_id, ftm_id)

        # ======================================== A. the catalog: who sees which queues, and how many items
        keys = lambda r: [q["key"] for q in r.json()]
        counts = lambda r: {q["key"]: q["count"] for q in r.json()}
        check("no token -> 401", (await c.get("/queues")).status_code == 401)

        r = await c.get("/queues", headers=FA)
        check("FA sees the 11 queues its role works, in screen order, and not collection tasks", r.status_code == 200 and keys(r) == [
            "doc_type_review", "invoice_review", "po_review", "version_review", "po_mismatch", "payment_review", "unapplied_cash",
            "mail_triage", "reminder_draft", "dispute_classify", "my_dispute"], str(keys(r)))
        fa_counts = counts(r)
        check("FA counts: only its own uploads' reviews, plus the shared queues", fa_counts == {
            "doc_type_review": 1, "invoice_review": 2, "po_review": 1, "version_review": 1, "po_mismatch": 1, "payment_review": 2,
            "unapplied_cash": 1, "mail_triage": 1, "reminder_draft": 1, "dispute_classify": 2, "my_dispute": 1}, str(fa_counts))

        r = await c.get("/queues", headers=FTM)
        check("FTM sees 10 queues: no mail triage, reminder drafts or dispute classification, but the TDS changes it asked for", keys(r) == [
            "doc_type_review", "invoice_review", "po_review", "version_review", "tds_review", "po_mismatch", "payment_review", "unapplied_cash",
            "my_dispute", "collection_task"], str(keys(r)))
        ftm_counts = counts(r)
        check("FTM counts: its OWN invoice review (not the FA's), its escalated dispute, its open task (not the closed one, not the FA's)",
              ftm_counts == {"doc_type_review": 0, "invoice_review": 1, "po_review": 0, "version_review": 0, "po_mismatch": 1,
                             "payment_review": 2, "unapplied_cash": 1, "my_dispute": 1, "collection_task": 1, "tds_review": 0}, str(ftm_counts))

        r = await c.get("/queues", headers=ADM)
        check("ADM sees all 13 queues", len(r.json()) == 13 and "collection_task" in keys(r) and "mail_triage" in keys(r))
        adm_counts = counts(r)
        check("ADM's own-upload and assigned queues are empty; the shared ones are not", adm_counts["invoice_review"] == 0 and adm_counts["my_dispute"] == 0
              and adm_counts["collection_task"] == 0 and adm_counts["payment_review"] == 2 and adm_counts["mail_triage"] == 1 and adm_counts["dispute_classify"] == 2, str(adm_counts))
        check("every queue has a label and a description", all(q["label"] and q["description"] for q in r.json()))

        # ======================================== B. each count equals what the queue's own endpoint lists
        async def listed(path, headers, **params):
            body = (await c.get(path, headers=headers, params=params)).json()
            return len(body["items"] if isinstance(body, dict) else body)

        mirrored = {
            "doc_type_review": await listed("/documents/type-reviews", FA),
            "invoice_review": await listed("/invoices/reviews", FA),
            "po_review": await listed("/purchase-orders/reviews", FA),
            "version_review": await listed("/invoices/version-reviews", FA),
            "po_mismatch": await listed("/purchase-orders/mismatch-reviews", FA),
            "payment_review": await listed("/payments/reviews", FA, limit=200),
            "unapplied_cash": await listed("/payments", FA, state="Unapplied", limit=200),
            "mail_triage": await listed("/mail/triage", FA, list="queue", limit=200),
            "reminder_draft": await listed("/reminders", FA, state="Drafted", limit=200),
            "dispute_classify": await listed("/disputes/classify-queue", FA, limit=200),
        }
        for key, own in mirrored.items():
            check(f"{key}: the queue count equals what its own endpoint lists ({own})", fa_counts[key] == own, f"queue {fa_counts[key]} vs own {own}")
        check("my_dispute: equals the disputes assigned to FA that are open", fa_counts["my_dispute"] == await listed("/disputes", FA, assigned_to=str(fa_id), open_only="true", limit=200))
        check("collection_task: equals FTM's open tasks", ftm_counts["collection_task"] == await listed("/tasks", FTM, status="Open", assigned_to=str(ftm_id), limit=200))

        # ======================================== C. the merged list
        r = await c.get("/queues/items", headers=FA, params={"limit": 100})
        body = r.json()
        total_fa = sum(fa_counts.values())
        check("all queues merged: total is the sum of the counts", r.status_code == 200 and body["total"] == total_fa and len(body["items"]) == total_fa, f"{body['total']} vs {total_fa}")
        check("newest first by default", [i["created_at"] for i in body["items"]] == sorted((i["created_at"] for i in body["items"]), reverse=True))
        check("every item names its queue and has an id and a created_at", all(i["queue"] and i["item_id"] and i["created_at"] for i in body["items"]))

        r = await c.get("/queues/items", headers=FA, params={"sort": "oldest", "limit": 100})
        check("oldest first", [i["created_at"] for i in r.json()["items"]] == sorted(i["created_at"] for i in r.json()["items"]))

        pages, seen = [], []
        for offset in (0, 5, 10):
            p = (await c.get("/queues/items", headers=FA, params={"limit": 5, "offset": offset})).json()
            pages.append(len(p["items"]))
            seen += [(i["queue"], i["item_id"]) for i in p["items"]]
        check("paging: 5 + 5 + the rest, total unchanged, no item twice, none missed", pages == [5, 5, total_fa - 10] and len(set(seen)) == total_fa == len(seen), str(pages))

        r = await c.get("/queues/items", headers=FA, params={"queue": "payment_review", "sort": "amount"})
        amounts = [Decimal(i["amount"]) for i in r.json()["items"]]
        check("one queue, largest amount first", r.json()["total"] == 2 and amounts == [D("45000"), D("700")], str(amounts))
        check("a payment's title fields: its reference, its customer, INR, its first reason", r.json()["items"][0]["subject"].startswith("UTR-60")
              and r.json()["items"][0]["customer_name"] == f"Queue Test {RUN} Ltd" and r.json()["items"][0]["currency"] == "INR" and r.json()["items"][0]["reason"] == "Two invoices fit")
        check("a payment with no stored reason still has one in plain English", r.json()["items"][1]["reason"])

        r = await c.get("/queues/items", headers=FA, params={"queue": "invoice_review", "sort": "oldest"})
        first, second = r.json()["items"]
        check("an extraction review shows the invoice number, customer and reason from the extraction", first["subject"] == f"INV-Q1-{RUN}" and first["customer_name"] == "Alpha Ltd" and first["reason"] == "Total does not match the lines")
        check("a total that is not a plain number (\"1,234\") is left empty, not an error", first["amount"] is None and Decimal(second["amount"]) == D("5000.50"))
        check("a review with no reasons still says why it is here", bool(second["reason"]))

        r = await c.get("/queues/items", headers=FA, params={"queue": "doc_type_review"})
        check("a document-type review is titled by its file name", r.json()["items"][0]["subject"] == "scan-1.pdf")

        r = await c.get("/queues/items", headers=FA, params={"sort": "deadline", "limit": 100})
        due = [i["due_at"] for i in r.json()["items"]]
        with_due = [d for d in due if d is not None]
        check("deadline sort: items with a deadline first, soonest first, the rest after", due[:len(with_due)] == with_due and with_due == sorted(with_due) and all(d is None for d in due[len(with_due):]) and len(with_due) >= 1, str(due[:4]))

        r = await c.get("/queues/items", headers=FA, params={"q": f"INV-Q2-{RUN}"})
        check("search by number finds exactly it", r.json()["total"] == 1 and r.json()["items"][0]["subject"] == f"INV-Q2-{RUN}")
        r = await c.get("/queues/items", headers=FA, params={"q": "beta"})
        check("search is case-insensitive and matches the customer", r.json()["total"] == 1)
        r = await c.get("/queues/items", headers=FA, params={"q": f"queue test {RUN}", "limit": 100})
        check("search by customer name across queues, total counts the whole match", r.json()["total"] >= 4 and r.json()["total"] == len(r.json()["items"]))
        r = await c.get("/queues/items", headers=FA, params={"q": "zzz-no-such"})
        check("no match -> empty page, total 0", r.json() == {"items": [], "total": 0})
        r = await c.get("/queues/items", headers=FA, params={"q": "%"})
        check("a % in the search is not a wildcard that matches everything", r.status_code == 200 and r.json()["total"] < total_fa)

        # ======================================== D. who may ask for what
        check("a queue your role does not work -> 403", (await c.get("/queues/items", headers=FA, params={"queue": "collection_task"})).status_code == 403)
        check("another role's own-upload review is not in your list", all(i["subject"] != f"INV-FTM-{RUN}" for i in (await c.get("/queues/items", headers=FA, params={"limit": 100})).json()["items"]))
        check("but the FTM sees it in its own", any(i["subject"] == f"INV-FTM-{RUN}" for i in (await c.get("/queues/items", headers=FTM, params={"limit": 100})).json()["items"]))
        check("an unknown queue -> 404", (await c.get("/queues/items", headers=FA, params={"queue": "nope"})).status_code == 404)
        check("no token -> 401", (await c.get("/queues/items")).status_code == 401)
        check("limit above 100 -> 422", (await c.get("/queues/items", headers=FA, params={"limit": 101})).status_code == 422)
        check("an unknown sort -> 422", (await c.get("/queues/items", headers=FA, params={"sort": "random"})).status_code == 422)
        r = await c.get("/queues/items", headers=FTM, params={"queue": "collection_task"})
        check("a collection task shows its invoice, customer and balance", r.json()["total"] == 1 and r.json()["items"][0]["subject"] == f"Q-INV-{RUN}" and Decimal(r.json()["items"][0]["amount"]) == D("9000"))
        r = await c.get("/queues/items", headers=FTM, params={"queue": "my_dispute"})
        check("a dispute shows its deadline", r.json()["total"] == 1 and r.json()["items"][0]["due_at"] is not None)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
