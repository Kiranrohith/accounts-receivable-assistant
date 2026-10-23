"""From a classified mail to the pipelines that do the work. All in the caller's transaction, so a mail that is
Processed always has its jobs (and a mail without jobs is never Processed).

  payment          one payment job per QUALIFYING attachment (each saved as a source document, traced to the mail);
                   with none, the mail's own text is the advice (an .eml source document, read like any document).
                   A file already received before (same bytes) is not read twice: it becomes a duplicate-payment
                   rejection pointing at the first one, never a raw constraint error.
  dispute          the mail's text as an .eml source document -> a dispute job (sender, received time and thread
                   come from the mail, so a reply in a known thread joins that dispute).
  both             the dispute job first; the payment (the text itself, or one job per attachment) follows, and
                   a payment job WAITS while a dispute job of the same mail is still queued or running, so the
                   hold the complaint puts on money is in place before the payment is matched.
  purchase_order   one job per qualifying attachment through the combined intake classifier (what it really is
                   is the classifier's call: an emailed invoice goes to document-type review, never auto-created).

Bare attachments (no subject, no text) from a known customer go through the same classifier, whatever they are.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import (
    GMAIL_LABEL_PROCESSED_DISPUTE,
    GMAIL_LABEL_PROCESSED_PAYMENT,
    GMAIL_LABEL_PROCESSED_PO,
)
from core.services.jobs import queue
from core.services.mail import ingest, threads
from core.services.mail.attachments import Decision as AttachmentDecision
from core.services.mail.classification import is_bare
from core.services.mail.attachments import MailRef, Qualification, qualify
from core.services.mail.parse import ParsedMail, parse_mail
from data.models.postgres import IngestedEmail
from data.models.postgres.enums import (
    DocKind,
    DisputeMessageKind,
    DocType,
    EmailClassification,
    EmailStatus,
    PaymentState,
    StepResult,
    TriggerType,
    UploadedVia,
)
from data.repositories.invoices import documents_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo

logger = logging.getLogger(__name__)
STEP = "handoff"
DUPLICATE_RETRY_SECONDS = 10
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_DOC_TYPES = {".pdf": DocType.PDF, ".docx": DocType.DOCX}

LABELS = {
    EmailClassification.PAYMENT: (GMAIL_LABEL_PROCESSED_PAYMENT,),
    EmailClassification.DISPUTE: (GMAIL_LABEL_PROCESSED_DISPUTE,),
    EmailClassification.BOTH: (GMAIL_LABEL_PROCESSED_PAYMENT, GMAIL_LABEL_PROCESSED_DISPUTE),
    EmailClassification.PURCHASE_ORDER: (GMAIL_LABEL_PROCESSED_PO,),
}


@dataclass
class HandOff:
    labels: tuple[str, ...] = ()
    jobs: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    duplicate_pending: bool = False  # a re-sent file whose first copy has no payment yet: a follow-up job finishes it


def load_mail(email: IngestedEmail) -> tuple[ParsedMail, Qualification]:
    mail = parse_mail(ingest.eml_path(email.gmail_message_id).read_bytes())
    return mail, qualify(mail.attachments, MailRef(email.gmail_message_id, email.id))


def _doc_type(name: str) -> DocType | None:
    ext = PurePath(name).suffix.lower()
    return DocType.IMAGE if ext in _IMAGE_EXTENSIONS else _DOC_TYPES.get(ext)


async def _body_document(db: AsyncSession, email: IngestedEmail, mail: ParsedMail, kind: DocKind, created_by: Any) -> Any:
    """The mail's own text as a source document: the .eml kept at poll time, read as text (no file hash: the registry
    is what makes it happen once)."""
    return await documents_repo.insert_document(
        db,
        doc_type=DocType.EML,
        uploaded_via=UploadedVia.EMAIL_BODY,
        doc_kind=kind,
        ingested_email_id=email.id,
        file_name=f"{(mail.subject or 'mail')[:80]}.eml",
        file_path=str(ingest.eml_path(email.gmail_message_id)),
        file_hash=None,
        created_by=created_by,
    )


async def record_duplicate(db: AsyncSession, email: IngestedEmail, mail: ParsedMail, name: str, first: Any) -> str:
    """This arrival of a file already received is a Rejected duplicate of the payment it first produced."""
    reason = f"Duplicate of an earlier payment: the same file ({name}) was already received"
    payment = await payments_repo.insert_payment(
        db,
        state=PaymentState.REJECTED,
        customer_id=first.customer_id,
        matched_layer=first.matched_layer,
        match_reason=reason,
        duplicate_of=first.id,
        from_email=mail.sender,
        ingested_email_id=email.id,
        payee_name=first.payee_name,
        payment_currency=first.payment_currency,
        reference_number=first.reference_number,
    )
    logs_repo.add_state_log(
        db, entity_type="payment", entity_id=payment.id, from_state=None, to_state=PaymentState.REJECTED.value,
        triggered_by_type=TriggerType.SYSTEM, reason=reason, occurred_at=datetime.now(UTC),
    )
    return f"{name}: duplicate of payment {first.id}; recorded as Rejected"


async def _duplicate_payment(
    db: AsyncSession, email: IngestedEmail, mail: ParsedMail, decision: AttachmentDecision, original_doc: Any, result: "HandOff"
) -> str:
    """The same file came before. If it already produced a payment, record this arrival as a Rejected duplicate of it;
    if that payment is not made yet (the first copy is still being read), a follow-up job records it when it exists."""
    first = (await payments_repo.payments_by_document(db, [original_doc])).get(original_doc)
    name = decision.attachment.file_name
    if first is None:
        result.duplicate_pending = True
        return f"{name}: the same file was already received (document {original_doc}); recorded as a duplicate once the first copy is read"
    return await record_duplicate(db, email, mail, name, first)


async def resolve_duplicates(db: AsyncSession, job: Any, email: IngestedEmail) -> None:
    """Job handler (entity: the e-mail): for each attachment of this mail whose bytes were received before, record the
    Rejected duplicate payment once the first copy's payment exists. Waits while that copy is still queued or running."""
    from core.services.invoices.processing import finish_job
    from data.models.postgres.enums import JobState
    from data.repositories.platform import jobs_repo

    mail, qualification = load_mail(email)
    waiting = False
    notes: list[str] = []
    for decision in qualification.qualifying:
        same = await documents_repo.document_id_by_hash(db, hashlib.sha256(decision.attachment.data).hexdigest())
        original = await documents_repo.get_document(db, same) if same else None
        if original is None or original.ingested_email_id == email.id:  # not a repeat: this mail's own file
            continue
        first = (await payments_repo.payments_by_document(db, [same])).get(same)
        if first is None:
            if await jobs_repo.pending_for_entity(db, same):
                waiting = True
            else:
                notes.append(f"{decision.attachment.file_name}: the first copy produced no payment; nothing to duplicate")
            continue
        if await payments_repo.duplicate_recorded(db, email.id, first.id):
            continue
        notes.append(await record_duplicate(db, email, mail, decision.attachment.file_name, first))
    if waiting:
        await queue.defer(db, job, DUPLICATE_RETRY_SECONDS, "the first copy of the file is still being read")
        return
    logs_repo.add_step_log(
        db, job_id=job.id, entity_type=queue.ENTITY_MAIL, entity_id=email.id, step_name="duplicates",
        result=StepResult.SUCCESS, occurred_at=datetime.now(UTC), output_summary={"recorded": notes},
    )
    await finish_job(db, job, JobState.SUCCEEDED)
    from data.repositories import unit_of_work

    await unit_of_work.commit(db)


async def _attachment_jobs(
    db: AsyncSession, email: IngestedEmail, mail: ParsedMail, qualification: Qualification,
    *, kind: DocKind, job_type: str, created_by: Any, result: HandOff,
) -> None:
    for decision in qualification.qualifying:
        attachment = decision.attachment
        digest = hashlib.sha256(attachment.data).hexdigest()
        same = await documents_repo.document_id_by_hash(db, digest)
        if same is not None:
            note = await _duplicate_payment(db, email, mail, decision, same, result) if kind is DocKind.PAYMENT else (
                f"{attachment.file_name}: the same file was already received (document {same}); not read again"
            )
            result.skipped.append(note)
            continue
        path = Path(get_settings().raw_files_dir) / f"{digest}{PurePath(attachment.file_name).suffix.lower()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(attachment.data)
        document = await documents_repo.insert_document(
            db,
            doc_type=_doc_type(attachment.file_name),
            uploaded_via=UploadedVia.EMAIL_ATTACHMENT,
            doc_kind=kind,
            ingested_email_id=email.id,
            file_name=attachment.file_name,
            file_path=str(path),
            file_hash=digest,
            created_by=created_by,
        )
        job = await queue.enqueue(db, job_type=job_type, entity_id=document.id, created_by=created_by)
        result.jobs.append({"job": str(job.id), "type": job_type, "document": str(document.id), "file": attachment.file_name})


async def hand_off(
    db: AsyncSession, email: IngestedEmail, mail_class: EmailClassification, *, created_by: Any = None
) -> HandOff:
    """Create the documents and jobs for a classified mail, mark it Processed and log what was done. Flushes only."""
    mail, qualification = load_mail(email)
    result = HandOff(labels=LABELS.get(mail_class, ()))
    # a bare attachment (no subject, no text) is read by the document classifier: the mail says nothing about what it is
    bare = is_bare(mail) and mail_class is EmailClassification.PAYMENT
    payment_job = queue.JOB_INTAKE if bare else queue.JOB_PAYMENT

    async def body_job(kind: DocKind, job_type: str) -> None:
        document = await _body_document(db, email, mail, kind, created_by)
        job = await queue.enqueue(db, job_type=job_type, entity_id=document.id, created_by=created_by)
        result.jobs.append({"job": str(job.id), "type": job_type, "document": str(document.id), "file": document.file_name})

    if mail_class is EmailClassification.PAYMENT:
        if qualification.qualifying:
            await _attachment_jobs(db, email, mail, qualification, kind=DocKind.PAYMENT, job_type=payment_job, created_by=created_by, result=result)
        else:
            await body_job(DocKind.PAYMENT, queue.JOB_PAYMENT)
    elif mail_class is EmailClassification.DISPUTE:
        await body_job(DocKind.DISPUTE, queue.JOB_DISPUTE)
    elif mail_class is EmailClassification.BOTH:
        if qualification.qualifying:
            await body_job(DocKind.DISPUTE, queue.JOB_DISPUTE)  # first; the payment jobs wait for it
            await _attachment_jobs(db, email, mail, qualification, kind=DocKind.PAYMENT, job_type=queue.JOB_PAYMENT, created_by=created_by, result=result)
        else:
            await body_job(DocKind.DISPUTE, queue.JOB_DISPUTE_PAYMENT)  # one text: the complaint, then its payment
    elif mail_class is EmailClassification.PURCHASE_ORDER:
        await _attachment_jobs(db, email, mail, qualification, kind=DocKind.PO, job_type=queue.JOB_INTAKE, created_by=created_by, result=result)
    else:
        raise ValueError(f"nothing to hand off for a {mail_class.value} mail")

    # B7: a payment advice or a PO sent in the thread of one of our conversations leaves a note there. A complaint in a
    # dispute's thread is joined to its dispute by the dispute pipeline itself; in a reminder's thread it leaves a note on
    # the invoice, because no dispute exists yet to hold it
    link = await threads.find_thread(db, mail, email.gmail_thread_id)
    if link is not None and (
        mail_class in (EmailClassification.PAYMENT, EmailClassification.PURCHASE_ORDER) or link.kind == "reminder"
    ):
        await threads.note_reply(db, link, email, threads.reply_note(mail_class.value, link), kind=DisputeMessageKind.INTERNAL_NOTE)
    else:
        link = None
    if result.duplicate_pending:
        job = await queue.enqueue(db, job_type=queue.JOB_MAIL_DUPLICATE, entity_id=email.id, created_by=created_by, entity_type=queue.ENTITY_MAIL)
        result.jobs.append({"job": str(job.id), "type": queue.JOB_MAIL_DUPLICATE, "document": None, "file": "re-sent file(s)"})
    email.status = EmailStatus.PROCESSED
    email.label_applied = ", ".join(result.labels) or None
    logs_repo.add_state_log(
        db, entity_type="ingested_email", entity_id=email.id, from_state="Classified (waiting for its pipeline)",
        to_state=EmailStatus.PROCESSED.value,
        reason=f"Handed to {len(result.jobs)} job(s): " + ", ".join(sorted({j['type'] for j in result.jobs}) or ["none (only duplicates)"]),
        occurred_at=datetime.now(UTC),
    )
    logs_repo.add_step_log(
        db, entity_type=queue.ENTITY_MAIL, entity_id=email.id, step_name=STEP,
        result=StepResult.WARNING if result.skipped else StepResult.SUCCESS, occurred_at=datetime.now(UTC),
        output_summary={
            "class": mail_class.value, "jobs": result.jobs, "skipped": result.skipped, "labels": list(result.labels),
            "thread": link.as_json() if link else None,
        },
    )
    return result


async def unlabel_all(email: IngestedEmail, labels: tuple[str, ...]) -> None:
    """Best effort, like label_all: the labels of the pipeline a mail was taken out of."""
    for label in labels:
        try:
            await ingest.unlabel_message(email.gmail_message_id, label)
        except Exception:
            logger.exception("could not remove the label %s from Gmail message %s", label, email.gmail_message_id)


async def label_all(email: IngestedEmail, labels: tuple[str, ...]) -> None:
    """Best effort, after the commit: the registry is the truth, a label is a convenience (B3)."""
    for label in labels:
        try:
            await ingest.label_message(email.gmail_message_id, label)
        except Exception:
            logger.exception("could not label Gmail message %s with %s", email.gmail_message_id, label)
