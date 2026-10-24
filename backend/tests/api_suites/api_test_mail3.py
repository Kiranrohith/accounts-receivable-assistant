"""M4: from a classified mail to its pipelines. Real database, fake Gmail, scripted classifier; the pipeline jobs are
NOT run here (api_test_mail4 runs them with the real reader). Proves the documents and jobs each class creates, the
duplicate-file rule, that a Processed mail always has its jobs, the dispute-first wait, the crash sweep and the
emailed-invoice guard."""

import asyncio
import sys
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, "src")
from sqlalchemy import text

from config.settings import get_settings
from core.services.jobs import queue
from core.services.mail import classification, cycle, ingest
from core.services.mail.gmail import RawMessage
from data.clients.db import SessionLocal, engine
from data.models.postgres import Customer, Invoice, Payment
from data.models.postgres.enums import PaymentState
from jobs.runner import HANDLERS
from schemas.mail.triage import MailClass, MailClassification

D = Decimal
s = get_settings()
assert "ar_mail3" in s.database_url, "refusing to run against a non-test database"
results = []
PDF_A = b"%PDF-1.4\n%advice A\n" + b"a" * 300
PDF_B = b"%PDF-1.4\n%advice B\n" + b"b" * 300
PDF_C = b"%PDF-1.4\n%advice C\n" + b"c" * 300
K = "ap@zenith.test"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:330])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def ex(sql, **p):
    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


def mail(subject, body, *, pdfs=(), sender=K):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "genworx.ardemo@gmail.com", subject
    m.set_content(body)
    for name, data in pdfs:
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return m.as_bytes()


class FakeGmail:
    def __init__(self):
        self.messages, self.order, self.labelled = {}, [], []

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
        self.labelled.append((mid, ids))

    async def aclose(self):
        pass


SCRIPT = {}


async def scripted(mail_obj, context, qualification, *, is_reply):
    return SCRIPT[mail_obj.subject], "scripted", 1


def v(cls, conf=93):
    return MailClassification(mail_class=cls, confidence=conf, reason=f"scripted {cls.value}")


async def triage_only():
    """Run the queued triage jobs; leave the pipeline jobs it creates alone."""
    while True:
        await ex("update jobs set run_after = now() + interval '1 day' where job_type <> 'mail_triage' and state = 'Queued'")
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-test")
        if job is None:
            return
        await queue.execute(job.id, "w-test", HANDLERS)


async def docs_of(subject):
    return await q(
        "select d.doc_kind::text, d.uploaded_via::text, d.doc_type::text, d.file_name, d.file_hash is null, d.file_path, d.ingested_email_id = e.id "
        "from source_documents d join ingested_emails e on e.id = d.ingested_email_id where e.subject = :s and d.uploaded_via <> 'email_attachment' or "
        "(e.subject = :s and d.id not in (select source_document_id from rejected_invoices where source_document_id is not null)) order by d.file_name", s=subject)


async def jobs_of(subject):
    return await q(
        "select j.job_type, j.state::text from jobs j join source_documents d on d.id = j.entity_id join ingested_emails e on e.id = d.ingested_email_id "
        "where e.subject = :s order by j.job_type, d.file_name", s=subject)


async def main():
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True
    import core.services.mail.triage as triage_mod
    triage_mod.classification.classify_mail = scripted
    raw_dir = Path(s.raw_files_dir)

    async with SessionLocal() as db:
        c = Customer(customer_name="Zenith Softlabs Pvt Ltd", contact="Anita", email=K, tax_id_type="GSTIN", tax_id="29ABCDE1234F1Z5",
                     deducts_tds=False, billing_currency="INR", country="India")
        db.add(c)
        await db.flush()
        db.add(Invoice(customer_id=c.id, invoice_number="INV-1001", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                       invoice_currency="INR", amount_inr=D("1000"), collectible_total=D("1000")))
        await db.commit()

    mails = {
        "two advices": (mail("two advices", "Paid INV-1001, two advices attached", pdfs=[("a.pdf", PDF_A), ("b.pdf", PDF_B)]), MailClass.PAYMENT),
        "body advice": (mail("body advice", "Paid INV-1001, UTR 123456, Rs 1000"), MailClass.PAYMENT),
        "resend": (mail("resend", "Same advice again, INV-1001", pdfs=[("a-again.pdf", PDF_A)]), MailClass.PAYMENT),
        "complaint": (mail("complaint", "Goods arrived damaged, INV-1001"), MailClass.DISPUTE),
        "both with file": (mail("both with file", "Paid part, goods damaged", pdfs=[("c.pdf", PDF_C)]), MailClass.BOTH),
        "both text only": (mail("both text only", "Paid 400 of 1000 as goods were damaged"), MailClass.BOTH),
        "an order": (mail("an order", "Our purchase order is attached", pdfs=[("po.pdf", PDF_B + b"po")]), MailClass.PURCHASE_ORDER),
        "": (mail("", "", pdfs=[("scan.pdf", PDF_C + b"bare")]), None),
    }
    for i, (subject, (raw, cls)) in enumerate(mails.items()):
        fake.add(f"m{i}", raw)
        if cls:
            SCRIPT[subject] = v(cls)

    # the first advice already produced a payment (so a re-sent copy has something to be a duplicate OF)
    await ingest.poll(fake)
    first = (await q("select d.id from source_documents d where false"))
    classification.classify_mail = scripted
    # process the mails in arrival order but let "two advices" create its payment BEFORE "resend" is triaged
    await ex("update jobs set run_after = now() + interval '1 day' where entity_id in (select id from ingested_emails where subject = 'resend')")
    await triage_only()
    doc_a = (await q("select id from source_documents where file_name = 'a.pdf'"))[0][0]
    async with SessionLocal() as db:
        cust = (await db.execute(text("select id from customers limit 1"))).scalar()
        db.add(Payment(state=PaymentState.APPLIED, customer_id=cust, payment_currency="INR", amount_received_inr=D("1000"),
                       reference_number="R-A", source_document_id=doc_a))
        await db.commit()
    await ex("update jobs set run_after = now() where job_type = 'mail_triage' and state = 'Queued'")
    await triage_only()

    # ---- payment with two attachments --------------------------------------------------------------------
    rows = await q("select d.file_name, d.doc_kind::text, d.uploaded_via::text, d.doc_type::text, d.file_hash is not null, d.file_path, d.ingested_email_id = e.id "
                   "from source_documents d join ingested_emails e on e.id = d.ingested_email_id where e.subject = 'two advices' order by d.file_name")
    check("two qualifying attachments: two source documents (payment, email_attachment, pdf, hashed, traced to the mail)",
          [(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows] == [("a.pdf", "payment", "email_attachment", "pdf", True, True), ("b.pdf", "payment", "email_attachment", "pdf", True, True)], str(rows))
    check("the files are on disk, byte for byte", all(Path(r[5]).read_bytes() in (PDF_A, PDF_B) for r in rows))
    check("one payment job each, Queued", await jobs_of("two advices") == [("payment_extraction", "Queued")] * 2, str(await jobs_of("two advices")))
    e = (await q("select status::text, classification::text, label_applied from ingested_emails where subject = 'two advices'"))[0]
    check("the mail is Processed with its label", e == ("Processed", "payment", "AR/Processed-Payment"), str(e))

    # ---- body-only payment ---------------------------------------------------------------------------------
    rows = await q("select d.doc_kind::text, d.uploaded_via::text, d.doc_type::text, d.file_hash is null, d.file_path from source_documents d join ingested_emails e on e.id = d.ingested_email_id where e.subject = 'body advice'")
    check("no attachment: the mail's own text is an .eml source document (email_body, no file hash)", len(rows) == 1 and rows[0][:4] == ("payment", "email_body", "eml", True) and rows[0][4].endswith(".eml"), str(rows))
    check("and one payment job", await jobs_of("body advice") == [("payment_extraction", "Queued")], str(await jobs_of("body advice")))

    # ---- the same file again -------------------------------------------------------------------------------
    check("a re-sent copy of an already received file creates no new document and no job", await jobs_of("resend") == [] and (await q("select count(*) from source_documents d join ingested_emails e on e.id=d.ingested_email_id where e.subject='resend'"))[0][0] == 0, str(await jobs_of("resend")))
    dup = await q("select p.state::text, p.duplicate_of, p.ingested_email_id = e.id, p.source_document_id, p.match_reason from payments p join ingested_emails e on e.id = p.ingested_email_id where e.subject = 'resend'")
    first_payment = (await q("select id from payments where reference_number = 'R-A'"))[0][0]
    check("it is recorded as a Rejected duplicate payment of the first one, traced to the mail, no source document (no constraint error)",
          len(dup) == 1 and dup[0][0] == "Rejected" and dup[0][1] == first_payment and dup[0][2] and dup[0][3] is None and "same file" in dup[0][4], str(dup))
    check("the re-sent mail is still Processed (nothing was lost, nothing repeated)", (await q("select status::text from ingested_emails where subject='resend'"))[0][0] == "Processed")

    # ---- dispute ---------------------------------------------------------------------------------------------
    rows = await q("select d.doc_kind::text, d.uploaded_via::text, d.doc_type::text from source_documents d join ingested_emails e on e.id = d.ingested_email_id where e.subject = 'complaint'")
    check("a complaint: the mail text as an .eml dispute document and one dispute job", rows == [("dispute", "email_body", "eml")] and await jobs_of("complaint") == [("dispute_extraction", "Queued")], f"{rows} {await jobs_of('complaint')}")
    check("labelled AR/Processed-Dispute", (await q("select label_applied from ingested_emails where subject='complaint'"))[0][0] == "AR/Processed-Dispute")

    # ---- both ------------------------------------------------------------------------------------------------
    check("both with an attachment: a dispute job (the text) AND a payment job (the file)", await jobs_of("both with file") == [("dispute_extraction", "Queued"), ("payment_extraction", "Queued")], str(await jobs_of("both with file")))
    check("both with text only: ONE job that raises the complaint and then reads the payment", await jobs_of("both text only") == [("dispute_payment_extraction", "Queued")], str(await jobs_of("both text only")))
    check("both: labelled Processed-Payment and Processed-Dispute", (await q("select label_applied from ingested_emails where subject='both with file'"))[0][0] == "AR/Processed-Payment, AR/Processed-Dispute")

    # ---- purchase order, bare attachment ------------------------------------------------------------------------
    rows = await q("select d.doc_kind::text, d.file_name from source_documents d join ingested_emails e on e.id = d.ingested_email_id where e.subject = 'an order'")
    check("a PO mail: its attachment is a PO document read by the combined intake classifier (document_intake)", rows == [("po", "po.pdf")] and await jobs_of("an order") == [("document_intake", "Queued")], f"{rows} {await jobs_of('an order')}")
    bare = await q("select j.job_type, d.doc_kind::text, e.label_applied from jobs j join source_documents d on d.id=j.entity_id join ingested_emails e on e.id=d.ingested_email_id where e.subject is null")
    check("a bare attachment from a known customer: read by the document classifier (document_intake), payment candidate", bare == [("document_intake", "payment", "AR/Processed-Payment")], str(bare))

    # ---- a Processed mail always has its jobs ------------------------------------------------------------------------
    orphan = await q("select e.gmail_message_id from ingested_emails e where e.status = 'Processed' and e.classification <> 'general' and not exists (select 1 from source_documents d where d.ingested_email_id = e.id and d.uploaded_via <> 'upload') and e.subject <> 'resend'")
    check("no Processed mail is without its documents", orphan == [], str(orphan))

    # ---- dispute first -----------------------------------------------------------------------------------------------
    pay_job = (await q("select j.id from jobs j join source_documents d on d.id=j.entity_id join ingested_emails e on e.id=d.ingested_email_id where e.subject='both with file' and j.job_type='payment_extraction'"))[0][0]
    await ex("update jobs set run_after = now(), state = 'Queued' where id = :i", i=pay_job)
    async with SessionLocal() as db:
        claimed = await queue.claim(db, "w-wait")
    while claimed is not None and claimed.id != pay_job:
        await ex("update jobs set run_after = now() + interval '1 day' where id = :i", i=claimed.id)
        async with SessionLocal() as db:
            claimed = await queue.claim(db, "w-wait")
    await queue.execute(pay_job, "w-wait", HANDLERS)
    waiting = (await q("select state::text, attempt_count, run_after > now() from jobs where id = :i", i=pay_job))[0]
    log = await q("select reason from state_log where entity_id = :i and entity_type = 'source_document' order by occurred_at desc limit 1", i=(await q("select entity_id from jobs where id = :i", i=pay_job))[0][0])
    check("a payment job whose mail also holds a complaint WAITS while the dispute job is queued (deferred, attempt not counted)", waiting[0] == "Queued" and waiting[1] == 0 and waiting[2] is True and "Deferred" in log[0][0] and "dispute" in log[0][0], f"{waiting} {log}")

    # ---- crash sweep -----------------------------------------------------------------------------------------------
    fake.add("late", mail("late", "Paid INV-1001 UTR 777"), thread="t-late")
    SCRIPT["late"] = v(MailClass.PAYMENT)
    await ingest.poll(fake)
    await ex("update ingested_emails set classification='payment', classification_confidence=90 where subject='late'")
    await ex("delete from jobs where entity_id = (select id from ingested_emails where subject='late')")
    handed = await cycle.sweep()
    late = (await q("select status::text, label_applied from ingested_emails where subject='late'"))[0]
    check("the sweep hands off a mail a crash left classified but not handed off", handed == 1 and late == ("Processed", "AR/Processed-Payment") and await jobs_of("late") == [("payment_extraction", "Queued")], f"{handed} {late}")
    check("and does nothing the second time", await cycle.sweep() == 0)

    # ---- an invoice that arrived by mail is never created on its own -------------------------------------------------
    import core.services.documents.intake as intake_mod
    from schemas.documents.classification import DocClassification, DocumentClass
    from core.services.documents.classification import ClassificationOutcome

    async def says_invoice(markdown):
        return ClassificationOutcome(DocClassification(doc_class=DocumentClass.INVOICE, confidence=97, reason="a tax invoice"), "scripted", 1)

    async def fake_parse(db, job, document):
        from core.services.invoices.parsing import ParsedDocument
        return ParsedDocument("x.pdf", 1, "TAX INVOICE No INV-9999")

    intake_mod.classify_document = says_invoice
    intake_mod.parse_step = fake_parse
    po_doc = (await q("select d.id from source_documents d join ingested_emails e on e.id=d.ingested_email_id where e.subject='an order'"))[0][0]
    job_id = (await q("select id from jobs where entity_id = :d", d=po_doc))[0][0]
    await ex("update jobs set run_after = now() where id = :j", j=job_id)
    async with SessionLocal() as db:
        claimed = await queue.claim(db, "w-inv")
    while claimed is not None and claimed.id != job_id:
        await ex("update jobs set run_after = now() + interval '1 day' where id = :i", i=claimed.id)
        async with SessionLocal() as db:
            claimed = await queue.claim(db, "w-inv")
    await queue.execute(job_id, "w-inv", HANDLERS)
    out = await q("select state::text from jobs where id = :j", j=job_id)
    review = await q("select reason::text from pending_invoice_changes where source_document_id = :d", d=po_doc)
    invoices = (await q("select count(*) from invoices where invoice_number = 'INV-9999'"))[0][0]
    check("a PO mail whose attachment turns out to be an invoice goes to document-type review, no invoice is created", out == [("Succeeded",)] and review and invoices == 0, f"{out} {review} {invoices}")

    # ---- a re-sent file whose first copy has NO payment yet (still queued): a follow-up job records the duplicate ----
    fake.add("resend2", mail("resend2", "b again, INV-1001", pdfs=[("b-again.pdf", PDF_B)]))
    SCRIPT["resend2"] = v(MailClass.PAYMENT)
    await ingest.poll(fake)
    await ex("update jobs set run_after = now() where job_type = 'mail_triage' and state = 'Queued'")
    await triage_only()
    dupjob = await q("select j.id, j.state::text from jobs j join ingested_emails e on e.id = j.entity_id where e.subject = 'resend2' and j.job_type = 'mail_duplicate'")
    check("first copy not read yet: the re-sent mail gets a follow-up mail_duplicate job, no payment yet, no document", len(dupjob) == 1 and (await q("select count(*) from payments p join ingested_emails e on e.id=p.ingested_email_id where e.subject='resend2'"))[0][0] == 0 and (await q("select count(*) from source_documents d join ingested_emails e on e.id=d.ingested_email_id where e.subject='resend2'"))[0][0] == 0, str(dupjob))

    async def run_job(job_id):
        await ex("update jobs set run_after = now() + interval '1 day' where state = 'Queued' and id <> :i", i=job_id)
        await ex("update jobs set run_after = now() where id = :i", i=job_id)
        async with SessionLocal() as db:
            claimed = await queue.claim(db, "w-dup")
        assert claimed is not None and claimed.id == job_id
        await queue.execute(job_id, "w-dup", HANDLERS)

    await run_job(dupjob[0][0])
    st = (await q("select state::text, attempt_count from jobs where id = :i", i=dupjob[0][0]))[0]
    check("while the first copy's job is still queued the follow-up WAITS (deferred, attempt not counted)", st[0] == "Queued" and st[1] == 0, str(st))
    doc_b = (await q("select id from source_documents where file_name = 'b.pdf'"))[0][0]
    await ex("update jobs set state = 'Succeeded' where entity_id = :d", d=doc_b)
    async with SessionLocal() as db:
        cust = (await db.execute(text("select id from customers limit 1"))).scalar()
        db.add(Payment(state=PaymentState.APPLIED, customer_id=cust, payment_currency="INR", amount_received_inr=D("500"), reference_number="R-B", source_document_id=doc_b))
        await db.commit()
    await run_job(dupjob[0][0])
    dup2 = await q("select p.state::text, p.reference_number from payments p join ingested_emails e on e.id = p.ingested_email_id where e.subject = 'resend2'")
    st = (await q("select state::text from jobs where id = :i", i=dupjob[0][0]))[0][0]
    check("once the first copy has its payment, the duplicate is recorded (Rejected, same reference) and the job Succeeds", dup2 == [("Rejected", "R-B")] and st == "Succeeded", f"{dup2} {st}")
    await ex("update jobs set run_after = now() where id = :i", i=dupjob[0][0])
    await ex("update jobs set state = 'Queued' where id = :i", i=dupjob[0][0])
    await run_job(dupjob[0][0])
    check("running it again records nothing twice", (await q("select count(*) from payments p join ingested_emails e on e.id = p.ingested_email_id where e.subject = 'resend2'"))[0][0] == 1)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
