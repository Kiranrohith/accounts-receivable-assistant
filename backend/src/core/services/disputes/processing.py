"""The complaint steps of the document pipeline (after parsing and classification).

read the complaint (LLM) -> identify the customer and the invoices -> raise the dispute(s).

  - one dispute per invoice the complaint names (each has its own deadlines and its own hold);
    a complaint that names none, or none we know, is a customer-level dispute; one whose sender
    cannot be identified cleanly has NO customer and waits in the classify queue
  - a category the reader is under DISPUTE_CLASSIFY_THRESHOLD sure of (or could not tell) also
    waits in the classify queue, keeping the suggestion
  - the whole complaint text is kept as the first message of each dispute

It does not end the job: the caller does (a document that also holds a payment advice goes on
to the payment steps, dispute first). Returns False when the job was handed back for a retry or
lost to another worker; the caller then stops.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import DISPUTE_CLASSIFY_THRESHOLD
from core.services.disputes import create
from core.services.disputes.extraction import ExtractionError, extract_dispute
from core.services.disputes.identity import DisputeIdentity, identify_complainant
from core.services.invoices.processing import log_step, retry_job
from core.services.mail import ingest, threads
from core.services.mail.parse import parse_mail
from core.services.jobs import queue
from data.models.postgres import Dispute, IngestedEmail, Job, SourceDocument
from data.models.postgres.enums import DisputeCategory, DisputeStatus, StepResult
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.invoices import documents_repo
from data.repositories.mail import emails_repo
from data.repositories.platform import jobs_repo
from schemas.disputes.extraction import ExtractedDispute

EXTRACT_STEP = "dispute_extract"
IDENTIFY_STEP = "dispute_identify"
ROUTE_STEP = "dispute_route"
MESSAGE_LIMIT = 8000  # characters of the complaint kept as its first message


def reader_notes(extracted: ExtractedDispute, identity: DisputeIdentity) -> list[str]:
    """What a person classifying the dispute should know the reader could not settle."""
    notes = list(identity.issues)
    if identity.customer is None and not identity.issues:
        notes.append("The reader could not tell which customer this is from")
    if identity.unknown_numbers:
        notes.append("Invoice numbers quoted that we do not have: " + ", ".join(identity.unknown_numbers))
    if identity.foreign_numbers:
        notes.append("Invoice numbers quoted that belong to another customer: " + ", ".join(identity.foreign_numbers))
    sure = extracted.confidence.category
    if extracted.category is None:
        notes.append("The reader could not tell the reason for the complaint")
    elif sure is None or sure < DISPUTE_CLASSIFY_THRESHOLD:
        notes.append(
            f"The reader suggests {extracted.category} but is only {sure or 0}% sure "
            f"(needs at least {DISPUTE_CLASSIFY_THRESHOLD}%)"
        )
    return notes


async def _thread_dispute(db: AsyncSession, mail_row: IngestedEmail | None) -> Dispute | None:
    """The dispute whose conversation this mail continues (B7), when it came by mail."""
    if mail_row is None:
        return None
    try:
        mail = parse_mail(ingest.eml_path(mail_row.gmail_message_id).read_bytes())
    except OSError:
        return None  # the kept file is gone: the mail thread id alone still joins
    link = await threads.find_thread(db, mail, mail_row.gmail_thread_id)
    return await disputes_repo.get_dispute(db, link.dispute_id) if link else None


def describe(dispute: Dispute) -> dict:
    return {
        "dispute_id": str(dispute.id),
        "status": dispute.status.value,
        "category": dispute.category.value if dispute.category else None,
        "invoice_id": str(dispute.invoice_id) if dispute.invoice_id else None,
        "assigned_to": str(dispute.assigned_to) if dispute.assigned_to else None,
    }


async def run_dispute_steps(
    db: AsyncSession, job: Job, document: SourceDocument, markdown: str, *, primary: bool
) -> bool:
    """`primary`: the complaint is the document's main content (no payment advice beside it), so
    its fields become the document's extraction; otherwise they are kept beside the payment's."""
    if document.id in await disputes_repo.disputes_by_documents(db, [document.id]):
        return True  # an earlier attempt already raised them: never twice

    # --- extract ---
    started = datetime.now(UTC)
    try:
        outcome = await extract_dispute(markdown)
    except ExtractionError as exc:
        log_step(
            db, job, document.id, started, step_name=EXTRACT_STEP, result=StepResult.ERROR,
            error_message=str(exc),
        )
        await retry_job(db, job, str(exc))
        return False
    extracted = outcome.dispute
    dump = extracted.model_dump(mode="json")
    if primary:
        documents_repo.save_extraction(document, dump, extracted.confidence.model_dump(mode="json"))
    else:
        documents_repo.save_dispute_extraction(document, dump)
    jobs_repo.update_job(job, model_used=outcome.model_used)
    log_step(
        db, job, document.id, started, step_name=EXTRACT_STEP, result=StepResult.SUCCESS,
        output_summary={
            "category": extracted.category,
            "category_confidence": extracted.confidence.category,
            "payment_related": extracted.payment_related,
            "invoice_numbers": extracted.invoice_numbers,
            "attempts": outcome.attempts,
        },
        model_used=outcome.model_used,
    )
    await unit_of_work.commit(db)  # the reading is safe before any business row

    # --- identify ---
    if not await queue.hold(db, job):  # a worker that lost its job writes no business rows
        return False
    started = datetime.now(UTC)
    mail_row = await emails_repo.get_email(db, document.ingested_email_id) if document.ingested_email_id else None
    if mail_row is not None and mail_row.sender:  # it came by mail: the sender header is the complainant's address
        extracted.from_email = mail_row.sender
    identity = await identify_complainant(
        db, extracted, our_company_name=get_settings().our_company_name
    )
    log_step(
        db, job, document.id, started, step_name=IDENTIFY_STEP,
        result=StepResult.SUCCESS if identity.customer else StepResult.WARNING,
        output_summary={
            "customer_id": str(identity.customer.id) if identity.customer else None,
            "invoices": [i.invoice_number for i in identity.invoices],
            "unknown_invoice_numbers": identity.unknown_numbers,
            "other_customers_invoice_numbers": identity.foreign_numbers,
            "issues": identity.issues,
        },
    )

    # --- route: raise the dispute(s) ---
    started = datetime.now(UTC)
    category = DisputeCategory(extracted.category) if extracted.category else None
    notes = reader_notes(extracted, identity)
    summary = (extracted.summary or "").strip() or f"Complaint received in {document.file_name or 'an uploaded document'}"
    from_email = (extracted.from_email or "").strip().lower() or None
    received_at = (mail_row.received_at if mail_row and mail_row.received_at else None) or document.created_at
    targets = [i.id for i in identity.invoices] or [None]
    thread_dispute = await _thread_dispute(db, mail_row)
    disputes: list[Dispute] = []
    for invoice_id in targets:
        created = await create.create_dispute(
            db,
            customer_id=identity.customer.id if identity.customer else None,
            invoice_id=invoice_id,
            summary=summary,
            from_email=from_email,
            received_at=received_at,
            ingested_email_id=document.ingested_email_id,
            gmail_thread_id=mail_row.gmail_thread_id if mail_row else None,
            gmail_message_id=mail_row.gmail_message_id if mail_row else None,
            thread_dispute=thread_dispute,
            category=category,
            payment_related=extracted.payment_related,
            confidence=extracted.confidence.category if category else None,
            created_by=document.created_by,
            source_document_id=document.id,
            inbound_body=markdown[:MESSAGE_LIMIT],
            intake_notes=notes,
            hold_in_queue=identity.customer is None,  # whose complaint is it? a person must say
        )
        disputes.append(created.dispute)
    queued = [d for d in disputes if d.status is DisputeStatus.RAISED]
    log_step(
        db, job, document.id, started, step_name=ROUTE_STEP,
        result=StepResult.WARNING if queued else StepResult.SUCCESS,
        output_summary={
            "disposition": "queued" if queued else "created",
            "disputes": [describe(d) for d in disputes],
            "customer_id": str(identity.customer.id) if identity.customer else None,
            "reasons": notes,
        },
    )
    await unit_of_work.commit(db)
    return True
