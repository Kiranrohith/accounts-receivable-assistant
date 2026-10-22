"""M5: replies in our own threads (B7) and the reroute buttons (E9). Real database, fake Gmail, scripted classifier and
scripted readers (no model calls). Proves: a reply finds its dispute by our Message-ID, the Gmail thread or the DSP
reference; a stranger replying in our thread is not ignored; a general reply becomes a note + a notice; a payment advice
in the thread leaves a note; E2 appends and E3 links; and the reroute both ways with every refusal."""

import asyncio
import re
import sys
from datetime import date
from decimal import Decimal
from email.message import EmailMessage

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from core.services.disputes.extraction import DisputeOutcome
from core.services.jobs import queue
from core.services.mail import ingest
from core.services.mail.gmail import RawMessage
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, DisputeMessage, Invoice, Payment
from data.models.postgres.enums import DisputeMessageKind, PaymentState
from jobs.runner import HANDLERS
from schemas.disputes.extraction import DisputeFieldConfidences, ExtractedDispute
from schemas.mail.triage import MailClass, MailClassification

D = Decimal
s = get_settings()
assert "ar_mail5" in s.database_url, "refusing to run against a non-test database"
results = []
K = "ap@zenith.test"
STRANGER = "colleague@zenith.test"
PDF = b"%PDF-1.4\n%advice pdf\n" + b"p" * 300


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def ex(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


def mail(subject, body, *, sender=K, message_id=None, in_reply_to=None, pdf=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "genworx.ardemo@gmail.com", subject
    m["Message-ID"] = message_id or f"<{abs(hash(subject + body))}@client.test>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)
    if pdf is not None:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename="advice.pdf")
    return m.as_bytes()


class FakeGmail:
    def __init__(self):
        self.messages, self.order, self.labelled, self.unlabelled = {}, [], [], []

    def add(self, mid, raw, thread=None):
        self.messages[mid] = RawMessage(mid, thread or f"t-{mid}", "1", 1_760_000_000_000, ("INBOX",), raw)
        self.order.insert(0, mid)

    async def list_message_ids(self, query, limit):
        return self.order[:limit]

    async def get_raw(self, mid):
        return self.messages[mid]

    async def ensure_labels(self, names):
        return {n: n for n in names}

    async def add_labels(self, mid, ids):
        self.labelled.append((mid, tuple(ids)))

    async def remove_labels(self, mid, ids):
        self.unlabelled.append((mid, tuple(ids)))

    async def aclose(self):
        pass


SCRIPT, CALLS = {}, []


async def scripted(mail_obj, context, qualification, *, is_reply):
    CALLS.append(mail_obj.subject)
    cls, conf = SCRIPT[mail_obj.subject]
    return MailClassification(mail_class=cls, confidence=conf, reason=f"scripted {cls.value}"), "scripted", 1


def conf90(category="Pricing"):
    return DisputeFieldConfidences(category=92, customer=90, invoices=90)


READ = {}  # subject -> the ExtractedDispute the "reader" returns


async def fake_extract_dispute(markdown):
    subject = re.search(r"^Subject: (.*)$", markdown, re.MULTILINE).group(1).strip()
    return DisputeOutcome(READ[subject], "stub", 1)


def claim(invoices, category="Pricing", summary="The price on the invoice is wrong"):
    return ExtractedDispute(customer_name="Zenith Softlabs Pvt Ltd", from_email=K, invoice_numbers=list(invoices),
                            summary=summary, category=category, confidence=conf90())


async def run_types(types):
    """Run the queued jobs of these types, one at a time; every other queued job is left waiting."""
    n = 0
    while True:
        await ex("update jobs set run_after = now() + interval '1 day' where state = 'Queued' and job_type <> all(:t)", t=list(types))
        await ex("update jobs set run_after = now() where state = 'Queued' and job_type = any(:t)", t=list(types))
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
        if job is None:
            return n
        await queue.execute(job.id, "w-test", HANDLERS)
        n += 1


async def triage():
    await run_types(["mail_triage"])


async def row(subject):
    return (await q("select id, status::text, classification::text, label_applied from ingested_emails where subject=:s", s=subject))[0]


async def jobs_of(subject):
    return await q(
        "select j.job_type, j.state::text, coalesce(j.last_error_message,'') from jobs j join source_documents d on d.id = j.entity_id "
        "join ingested_emails e on e.id = d.ingested_email_id where e.subject = :s order by j.created_at", s=subject)


async def msgs(dispute_id):
    return await q("select kind::text, body, gmail_message_id from dispute_messages where dispute_id=:d order by created_at, id", d=dispute_id)


async def notices(dispute_id):
    return (await q("select count(*) from notifications where type='DisputeFollowUp' and entity_id=:d", d=dispute_id))[0][0]


async def arrive(fake, mid, raw, thread, subject, cls=None, conf=93):
    fake.add(mid, raw, thread)
    if cls is not None:
        SCRIPT[subject] = (cls, conf)
    await ingest.poll(fake)
    await triage()


async def main():
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True
    import core.services.disputes.clarification as clar_mod
    import core.services.disputes.processing as dproc
    import core.services.mail.triage as triage_mod

    triage_mod.classification.classify_mail = scripted
    dproc.extract_dispute = fake_extract_dispute
    queue.retry_delays_seconds = (3600, 3600, 3600)

    async with SessionLocal() as db:
        c = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email=K, gstin="29ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.flush()
        for n in range(1, 9):
            db.add(Invoice(customer_id=c.id, invoice_number=f"INV-100{n}", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                           invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as hc:
        login = lambda e, p: hc.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa One", "fa.one@example.com", "FA"), ("Ftm One", "ftm.one@example.com", "FTM")):
            assert (await hc.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.one@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.one@example.com', 'pass12345')).json()['access_token']}"}

        # ================================================================ A. replies in our threads =========
        READ["Wrong price on INV-1001"] = claim(["INV-1001"])
        await arrive(fake, "g-d1", mail("Wrong price on INV-1001", "The price on INV-1001 is not what we agreed."), "TH-D1", "Wrong price on INV-1001", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        disp = await q("select id, status::text, assigned_to from disputes")
        check("the complaint became ONE dispute, assigned", len(disp) == 1 and disp[0][1] == "Assigned" and disp[0][2] is not None, str(disp))
        did = disp[0][0]
        ref = "DSP-" + str(did)[:8].upper()
        async with SessionLocal() as db:  # what outbound.send_draft records for the clarification it sent
            db.add(DisputeMessage(dispute_id=did, kind=DisputeMessageKind.SENT, body=f"Subject: Your query [{ref}]\n\nPlease explain.",
                                  gmail_message_id="<clar-1@genworx.test>"))
            await db.commit()
        base_msgs, base_notes = len(await msgs(did)), await notices(did)

        # R1: a colleague (unknown address) answers OUR mail: found by the Message-ID in In-Reply-To
        READ["Re: Wrong price"] = claim(["INV-1001"], summary="Colleague adds details")
        await arrive(fake, "g-r1", mail("Re: Wrong price", "We checked: the old rate was 90.", sender=STRANGER, in_reply_to="<clar-1@genworx.test>"),
                     "TH-R1", "Re: Wrong price", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        check("R1 (reply to our mail, other thread, unknown sender) joined the dispute (E2): no second dispute",
              (await q("select count(*) from disputes"))[0][0] == 1 and len(await msgs(did)) == base_msgs + 1, str(await msgs(did))[:200])
        check("R1: the owner got a DisputeFollowUp notice", await notices(did) == base_notes + 1)
        step = (await q("select output_summary->'thread' from step_log where step_name='triage' and entity_id=(select id from ingested_emails where subject='Re: Wrong price')"))[0][0]
        check("R1: the triage step recorded the link and how it was found", step and step["reference"] == ref and step["how"] == "a reply to our mail", str(step))

        # R2: only the DSP reference in the subject (a forwarded or re-typed reply)
        READ[f"Re: price [{ref}]"] = claim(["INV-1001"], summary="Reply with the reference only")
        await arrive(fake, "g-r2", mail(f"Re: price [{ref}]", "Any news?", sender=STRANGER), "TH-R2", f"Re: price [{ref}]", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        check("R2 (found by the dispute reference in the subject) joined the same dispute",
              (await q("select count(*) from disputes"))[0][0] == 1 and len(await msgs(did)) == base_msgs + 2)
        step = (await q("select output_summary->'thread'->>'how' from step_log where step_name='triage' and entity_id=(select id from ingested_emails where gmail_message_id='g-r2')"))[0][0]
        check("R2: found by the reference", step == "the dispute reference in the subject", str(step))

        # R3: "thanks, noted" from a stranger in the dispute's own Gmail thread: general, but never dropped
        CALLS.clear()
        await arrive(fake, "g-r3", mail("Re: thanks", "Thanks, noted. We will get back.", sender=STRANGER), "TH-D1", "Re: thanks", MailClass.GENERAL, 96)
        r3 = await row("Re: thanks")
        check("R3: a stranger in our thread is NOT dropped without a look: the model was asked", "Re: thanks" in CALLS, str(CALLS))
        check("R3: general reply -> Processed, labelled as a dispute conversation, not Ignored", r3[1:] == ("Processed", "general", "AR/Processed-Dispute"), str(r3))
        mm = await msgs(did)
        check("R3: it is on the dispute as the customer's message, with the mail id (thread evidence)",
              mm[-1][0] == "inbound_email" and "nothing in it is a payment or a new complaint" in mm[-1][1] and mm[-1][2] == "g-r3", str(mm[-1])[:240])
        check("R3: no job was created and the owner was told", (await jobs_of("Re: thanks")) == [] and await notices(did) == base_notes + 3)

        # R4: a stranger with nothing to do with us: still ignored, no model call
        CALLS.clear()
        await arrive(fake, "g-r4", mail("Weekly newsletter", "Buy our stuff", sender="promo@vendor.test"), None, "Weekly newsletter", MailClass.GENERAL)
        r4 = await row("Weekly newsletter")
        check("R4: an unrelated stranger is ignored without a model call", r4[1:] == ("Processed", "general", "AR/Ignored") and CALLS == [], f"{r4} {CALLS}")

        # R6: the customer answers our mail with the payment: a payment job AND a note on the dispute
        await arrive(fake, "g-r6", mail("Re: paid", "Paid INV-1002 today, UTR 998877, Rs 1000", in_reply_to="<clar-1@genworx.test>"), "TH-R6", "Re: paid", MailClass.PAYMENT, 94)
        mm = await msgs(did)
        check("R6: payment advice in the dispute's conversation: a payment job is queued", [j[:2] for j in await jobs_of("Re: paid")] == [("payment_extraction", "Queued")], str(await jobs_of("Re: paid")))
        check("R6: and an internal note on the dispute says so (with the mail link)",
              mm[-1][0] == "internal_note" and "payment advice" in mm[-1][1] and "mail.google.com" in mm[-1][1], str(mm[-1])[:240])
        hs = (await q("select output_summary->'thread'->>'reference' from step_log where step_name='handoff' and entity_id=(select id from ingested_emails where subject='Re: paid')"))[0][0]
        check("R6: the hand-off step log names the dispute", hs == ref, str(hs))

        # E3 and a late thank-you, after the dispute is resolved
        r = await hc.post(f"/disputes/{did}/resolve", headers=FA, json={"narrative": "Rate corrected, credit note agreed."})
        check("the dispute is resolved by its owner", r.status_code == 200, r.text[:160])
        READ["Re: still wrong"] = claim(["INV-1001"], summary="Still wrong after the resolution")
        await arrive(fake, "g-r5", mail("Re: still wrong", "The corrected invoice is still wrong.", in_reply_to="<clar-1@genworx.test>"), "TH-R5", "Re: still wrong", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        linked = await q("select id, linked_dispute_id, status::text from disputes where id <> :d", d=did)
        check("E3: a complaint in the thread of a RESOLVED dispute opens a new one linked to it", len(linked) == 1 and linked[0][1] == did, str(linked))
        before = len(await msgs(did))
        await arrive(fake, "g-r7", mail("Re: thank you", "Thank you for resolving this.", sender=STRANGER, in_reply_to="<clar-1@genworx.test>"), "TH-R7", "Re: thank you", MailClass.GENERAL, 95)
        check("a thank-you after the resolution is a note on the resolved dispute, not a new dispute",
              (await q("select count(*) from disputes"))[0][0] == 2 and len(await msgs(did)) == before + 1)

        # ================================================================ B. reroute (E9) ===================
        async def reroute(subject, to, headers=FA, note=None):
            email_id = (await row(subject))[0]
            body = {"mail_class": to, **({"note": note} if note else {})}
            return await hc.post(f"/mail/triage/{email_id}/reroute", headers=headers, json=body)

        # B1 payment -> dispute, nothing read yet
        await arrive(fake, "g-b1", mail("pay one", "Paid INV-1003 UTR 1111 Rs 1000"), None, "pay one", MailClass.PAYMENT)
        READ["pay one"] = claim(["INV-1003"], category="Quality", summary="Actually a complaint")
        jobs = await jobs_of("pay one")
        check("B1 setup: a payment job is queued", [j[:2] for j in jobs] == [("payment_extraction", "Queued")], str(jobs))
        r = await reroute("pay one", "dispute", note="it is a complaint")
        check("B1: payment -> dispute works", r.status_code == 200 and r.json()["mail_class"] == "dispute", r.text[:200])
        jobs = await jobs_of("pay one")
        check("B1: the payment job was withdrawn (Failed, with the reason) and a dispute job is queued",
              jobs[0][:2] == ("payment_extraction", "Failed") and "rerouted from payment to dispute" in jobs[0][2] and jobs[1][:2] == ("dispute_extraction", "Queued"), str(jobs))
        b1 = await row("pay one")
        check("B1: the mail is now a Processed dispute labelled AR/Processed-Dispute", b1[1:] == ("Processed", "dispute", "AR/Processed-Dispute"), str(b1))
        check("B1: Gmail lost the payment label and got the dispute one",
              ("g-b1", ("AR/Processed-Payment",)) in fake.unlabelled and ("g-b1", ("AR/Processed-Dispute",)) in fake.labelled, f"{fake.unlabelled[-2:]} {fake.labelled[-2:]}")
        tl = await q("select reason from state_log where entity_type='ingested_email' and entity_id=:e order by occurred_at", e=b1[0])
        check("B1: the mail's history says who rerouted and why", any("rerouted from payment" in t[0] and "it is a complaint" in t[0] for t in tl), str(tl)[-300:])
        await run_types(["dispute_extraction"])
        check("B1: the dispute pipeline then raised the dispute from the mail", (await q("select count(*) from disputes where ingested_email_id=:e", e=b1[0]))[0][0] == 1)

        # B2 a payment still under review is rejected
        await arrive(fake, "g-b2", mail("pay two", "Paid INV-1004 UTR 2222"), None, "pay two", MailClass.PAYMENT)
        b2 = (await row("pay two"))[0]
        async with SessionLocal() as db:
            db.add(Payment(state=PaymentState.NEEDS_REVIEW, ingested_email_id=b2, from_email=K, review_reasons=["amount missing"]))
            await db.commit()
        r = await reroute("pay two", "dispute")
        pm = await q("select state::text, match_reason from payments where ingested_email_id=:e", e=b2)
        check("B2: a payment waiting for review is Rejected, with the reason", r.status_code == 200 and pm[0][0] == "Rejected" and "rerouted" in pm[0][1], f"{r.text[:120]} {pm}")
        check("B2: the response lists what was taken back", any("rejected" in u for u in r.json()["undone"]), str(r.json()["undone"]))

        READ["pay two"] = claim(["INV-1004"], category="Quality", summary="Rerouted complaint")

        # B3 money already recorded: refused, nothing changes
        await arrive(fake, "g-b3", mail("pay three", "Paid INV-1005 UTR 3333"), None, "pay three", MailClass.PAYMENT)
        b3 = (await row("pay three"))[0]
        async with SessionLocal() as db:
            db.add(Payment(state=PaymentState.APPLIED, ingested_email_id=b3, from_email=K))
            await db.commit()
        r = await reroute("pay three", "dispute")
        check("B3: a payment that holds money is NOT undone silently (409, reverse it first)", r.status_code == 409 and "Reverse the payment first" in r.json()["detail"], r.text[:200])
        check("B3: nothing changed (still a payment, its job still queued)", (await row("pay three"))[2] == "payment" and [j[1] for j in await jobs_of("pay three")] == ["Queued"])

        # B4 a job running right now
        await arrive(fake, "g-b4", mail("pay four", "Paid INV-1006 UTR 4444"), None, "pay four", MailClass.PAYMENT)
        await ex("update jobs set state='Running', locked_by='w-x', started_at=now(), heartbeat_at=now() where job_type='payment_extraction' and entity_id in "
                 "(select d.id from source_documents d join ingested_emails e on e.id=d.ingested_email_id where e.subject='pay four')")
        r = await reroute("pay four", "dispute")
        check("B4: a payment job being read right now -> 409, try again", r.status_code == 409 and "being read right now" in r.json()["detail"], r.text[:200])
        check("B4: nothing changed", (await row("pay four"))[2] == "payment")
        await ex("update jobs set state='Queued', locked_by=null where job_type='payment_extraction' and state='Running'")

        # B5 dispute -> payment: the unworked dispute is withdrawn
        READ["dispute two"] = claim(["INV-1007"], summary="Goods were damaged", category="Quality")
        await arrive(fake, "g-b5", mail("dispute two", "Goods for INV-1007 were damaged."), None, "dispute two", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        b5 = (await row("dispute two"))[0]
        d5 = (await q("select id, status::text, assigned_to from disputes where ingested_email_id=:e", e=b5))[0]
        check("B5 setup: an assigned dispute and its clarification job queued", d5[1] == "Assigned" and (await q("select count(*) from jobs where job_type='dispute_clarification' and entity_id=:d and state='Queued'", d=d5[0]))[0][0] == 1)
        r = await reroute("dispute two", "payment", headers=ADM, note="this is a payment advice")
        check("B5: dispute -> payment works", r.status_code == 200 and r.json()["mail_class"] == "payment" and any("withdrawn" in u for u in r.json()["undone"]), r.text[:220])
        d5n = (await q("select status::text, resolution_narrative from disputes where id=:d", d=d5[0]))[0]
        check("B5: the dispute is Resolved as Withdrawn, with the reason", d5n[0] == "Resolved" and d5n[1].startswith("Withdrawn: the mail was rerouted from dispute to payment"), str(d5n))
        check("B5: the invoice is no longer flagged disputed", (await q("select is_disputed from invoices where invoice_number='INV-1007'"))[0][0] is False)
        check("B5: no resolution e-mail draft was made for the customer", (await q("select count(*) from dispute_messages where dispute_id=:d and kind='draft'", d=d5[0]))[0][0] == 0)
        check("B5: its clarification job was withdrawn", (await q("select state::text from jobs where job_type='dispute_clarification' and entity_id=:d", d=d5[0]))[0][0] == "Failed")
        check("B5: the owner was told", (await q("select count(*) from notifications where entity_id=:d and message like '%withdrawn%'", d=d5[0]))[0][0] == 1)
        check("B5: a payment job (the mail's text) is queued", [j[:2] for j in await jobs_of("dispute two") if j[0] == "payment_extraction"] == [("payment_extraction", "Queued")], str(await jobs_of("dispute two")))

        # B6 a dispute already worked on stays
        READ["dispute three"] = claim(["INV-1008"], summary="Damaged", category="Quality")
        await arrive(fake, "g-b6", mail("dispute three", "INV-1008 goods were damaged."), None, "dispute three", MailClass.DISPUTE)
        await run_types(["dispute_extraction"])
        b6 = (await row("dispute three"))[0]
        d6 = (await q("select id from disputes where ingested_email_id=:e", e=b6))[0][0]
        async with SessionLocal() as db:
            db.add(DisputeMessage(dispute_id=d6, kind=DisputeMessageKind.SENT, body="Subject: x\n\nsent", gmail_message_id="<clar-b6@genworx.test>"))
            await db.commit()
        r = await reroute("dispute three", "payment")
        check("B6: a dispute with an e-mail already sent to the customer stays (409)", r.status_code == 409 and "already gone to the customer" in r.json()["detail"], r.text[:200])
        await ex("update disputes set status='Escalated' where id=:d", d=d6)
        r = await reroute("dispute three", "payment")
        check("B6: an escalated dispute stays (409)", r.status_code == 409 and "Escalated" in r.json()["detail"], r.text[:200])
        check("B6: nothing changed (still a dispute, still open)", (await row("dispute three"))[2] == "dispute" and (await q("select status::text from disputes where id=:d", d=d6))[0][0] == "Escalated")

        # B7 a follow-up the mail added to another dispute becomes a note when the mail is a payment
        r1 = (await row("Re: Wrong price"))[0]
        r = await reroute("Re: Wrong price", "payment")
        check("B7: rerouting a dispute mail that only FOLLOWED UP another dispute works", r.status_code == 200, r.text[:200])
        mm = await q("select kind::text, body from dispute_messages where ingested_email_id=:e", e=r1)
        check("B7: its message on the dispute became an internal note", mm and mm[0][0] == "internal_note" and "Not a customer message" in mm[0][1], str(mm)[:240])
        check("B7: the original dispute was NOT withdrawn", (await q("select status::text from disputes where id=:d", d=did))[0][0] == "Resolved" and "Withdrawn" not in ((await q("select resolution_narrative from disputes where id=:d", d=did))[0][0] or ""))

        # B8 what cannot be rerouted
        await arrive(fake, "g-b8", mail("unsure mail", "something"), None, "unsure mail", MailClass.PAYMENT, 50)
        r = await reroute("unsure mail", "dispute")
        check("B8: a mail waiting in the human queue -> 409 (route it there)", r.status_code == 409 and "ordinary routing action" in r.json()["detail"], r.text[:200])
        r = await reroute("Weekly newsletter", "payment")
        check("B8: an ignored mail -> 409", r.status_code == 409)
        await arrive(fake, "g-b9", mail("paid and complaint", "Paid 400 of 1000, goods damaged"), None, "paid and complaint", MailClass.BOTH)
        r = await reroute("paid and complaint", "payment")
        check("B8: a 'both' mail is not swapped -> 409", r.status_code == 409)
        r = await reroute("pay three", "payment")
        check("B8: the same class -> 409 already handled", r.status_code == 409 and "already handled as a payment" in r.json()["detail"], r.text[:200])
        check("B8: unknown mail -> 404", (await hc.post("/mail/triage/00000000-0000-0000-0000-000000000000/reroute", headers=FA, json={"mail_class": "dispute"})).status_code == 404)
        check("B8: an FTM may not reroute -> 403", (await reroute("pay four", "dispute", headers=FTM)).status_code == 403)
        check("B8: a made-up class is refused -> 422", (await hc.post(f"/mail/triage/{(await row('pay four'))[0]}/reroute", headers=FA, json={"mail_class": "general"})).status_code == 422)

        # B9 attachment read once cannot be read twice
        await arrive(fake, "g-b10", mail("pdf advice", "Paid INV-1002, advice attached", pdf=PDF), None, "pdf advice", MailClass.PAYMENT)
        r = await reroute("pdf advice", "dispute")
        check("B9: payment with an attachment -> dispute works", r.status_code == 200, r.text[:200])
        r = await reroute("pdf advice", "payment")
        check("B9: and back again is refused: the file was already read from this mail once (409)", r.status_code == 409 and "already read from this mail once" in r.json()["detail"], r.text[:240])

        # B10 views
        routed = (await hc.get("/mail/triage", headers=FA, params={"list": "routed", "limit": 200})).json()
        subjects = {i["subject"] for i in routed}
        check("B10: list=routed shows the mails handed to a pipeline (not the ignored or queued ones)",
              {"pay one", "dispute two", "pay three"} <= subjects and "Weekly newsletter" not in subjects and "unsure mail" not in subjects, str(sorted(subjects))[:240])
        det = (await hc.get(f"/mail/triage/{(await row('pay two'))[0]}", headers=FA)).json()
        check("B10: the detail shows what the mail produced and where it can be sent",
              det["reroute_to"] == "payment" and [p["state"] for p in det["produced"]] == ["Rejected", "Assigned"], f"{det['reroute_to']} {det['produced']}")
        det = (await hc.get(f"/mail/triage/{(await row('pay three'))[0]}", headers=FA)).json()
        check("B10: a payment mail can be rerouted to a dispute", det["reroute_to"] == "dispute")
        det = (await hc.get(f"/mail/triage/{(await row('Re: still wrong'))[0]}", headers=FA)).json()
        check("B10: the detail names the conversation of ours it continues", (det["replies_to"] or "").startswith(ref) and "a reply to our mail" in det["replies_to"], str(det["replies_to"]))

        # B11 two people at once
        await arrive(fake, "g-b11", mail("race mail", "Paid INV-1005 UTR 5555"), None, "race mail", MailClass.PAYMENT)
        a, b = await asyncio.gather(reroute("race mail", "dispute"), reroute("race mail", "dispute", headers=ADM))
        check("B11: two people rerouting the same mail at once: one wins, the other gets 409", sorted([a.status_code, b.status_code]) == [200, 409], f"{a.status_code} {b.status_code}")
        check("B11: exactly one dispute job exists", len([j for j in await jobs_of("race mail") if j[0] == "dispute_extraction"]) == 1, str(await jobs_of("race mail")))

        # the money identities still hold, and nothing is stuck
        stuck = await q("select job_type, state::text, last_error_message from jobs where state in ('Failed','DeadLettered') and coalesce(last_error_message,'') not like '%rerouted%' and coalesce(last_error_message,'') not like '%reroute%'")
        check("no job failed except the ones withdrawn by a reroute", not stuck, str(stuck)[:300])

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
