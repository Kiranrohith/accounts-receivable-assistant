"""Polling the AR mailbox: each new message is registered, its attachments qualified, and a triage job queued,
all in ONE transaction. So a registered mail without its job cannot exist, and a mail that is already registered
is never taken twice (the Gmail message id is unique in `ingested_emails`: overlapping polls, a crash before the
label, a re-poll of a mail still waiting for a person are all harmless).

What is kept for each mail:
  * the whole message as `<raw_files_dir>/mail/<gmail id>.eml` (so triage and the payment jobs never need Gmail
    again, and a re-run reads exactly what arrived);
  * the registry row (sender, subject, received time, thread id: the thread links a reply to what it answers);
  * the attachment decisions in the step log: qualifying and skipped ones listed, each with its reason;
  * every REJECTED attachment as a rejected upload (kind `mail_attachment`) with the plain reason AND the link to
    the source mail, so a person can open the mail and handle a possible real advice by hand.

A message that cannot be read is registered as Failed with its error (visible, and never retried forever).
Labels are applied by the triage job after its decision, not here.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import GMAIL_LABELS, GMAIL_MAX_PER_POLL, GMAIL_POLL_QUERY
from core.services.jobs import queue
from core.services.mail import gmail
from core.services.mail.attachments import Decision, MailRef, qualify
from core.services.mail.parse import ParsedMail, parse_mail
from data.clients.db import session_scope
from data.models.postgres import RejectedInvoice
from data.models.postgres.enums import DocKind, DocType, EmailStatus, StepResult, UploadedVia
from data.repositories.invoices import documents_repo
from data.repositories.mail import emails_repo
from data.repositories.platform import logs_repo

logger = logging.getLogger(__name__)

LIST_WINDOW = 300  # newest messages looked at per poll; at most GMAIL_MAX_PER_POLL unknown ones are taken
REJECTED_KIND = "mail_attachment"
_DOC_TYPES = {".pdf": DocType.PDF, ".docx": DocType.DOCX, ".csv": DocType.CSV, ".eml": DocType.EML}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}

# Tests swap this for a fake mailbox.
client_factory = gmail.get_client


def eml_path(gmail_message_id: str) -> Path:
    return Path(get_settings().raw_files_dir) / "mail" / f"{gmail_message_id}.eml"


@dataclass(frozen=True)
class Ingested:
    email_id: Any
    qualifying: int
    skipped: int
    rejected: int


def _doc_type(file_name: str) -> DocType | None:
    ext = PurePath(file_name).suffix.lower()
    return DocType.IMAGE if ext in _IMAGE_EXTENSIONS else _DOC_TYPES.get(ext)


def _log(db: AsyncSession, email_id: Any, step: str, result: StepResult, **fields: Any) -> None:
    logs_repo.add_step_log(
        db,
        entity_type=queue.ENTITY_MAIL,
        entity_id=email_id,
        step_name=step,
        result=result,
        occurred_at=datetime.now(UTC),
        **fields,
    )


async def _record_rejection(db: AsyncSession, decision: Decision, email_id: Any, parsed: ParsedMail) -> None:
    """A rejected attachment becomes a rejected upload that says why and leads back to the mail."""
    attachment, mail = decision.attachment, decision.mail
    data = attachment.data
    file_hash = hashlib.sha256(data).hexdigest()
    same = await documents_repo.document_id_by_hash(db, file_hash)
    document_id = same
    duplicate_of = None
    if same is None:
        suffix = PurePath(attachment.file_name).suffix.lower() or ".bin"
        path = Path(get_settings().raw_files_dir) / f"{file_hash}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        document = await documents_repo.insert_document(
            db,
            doc_type=_doc_type(attachment.file_name),
            uploaded_via=UploadedVia.EMAIL_ATTACHMENT,
            doc_kind=DocKind.PAYMENT,
            ingested_email_id=email_id,
            file_name=attachment.file_name,
            file_path=str(path),
            file_hash=file_hash,
        )
        document_id = document.id
    else:  # the same bytes were seen before (an empty file, a resent PDF): point at that document
        document_id, duplicate_of = None, same
    message = (
        f"Attachment '{attachment.file_name}' of the mail \"{parsed.subject or '(no subject)'}\" from "
        f"{parsed.sender or 'an unknown sender'} was rejected: {decision.reason}. Open the source mail: {mail.link}"
    )
    db.add(
        RejectedInvoice(
            source_document_id=document_id,
            duplicate_of_source_document_id=duplicate_of,
            attempted_file_name=attachment.file_name,
            rejection_reasons=[{"code": "mail_attachment_rejected", "message": message}],
            extracted_snapshot={
                "kind": REJECTED_KIND,
                "mail": {
                    "gmail_message_id": mail.gmail_message_id,
                    "ingested_email_id": str(email_id),
                    "link": mail.link,
                    "subject": parsed.subject,
                    "sender": parsed.sender,
                },
                "attachment": decision.as_log(),
            },
        )
    )
    await db.flush()


async def ingest_message(db: AsyncSession, raw: gmail.RawMessage) -> Ingested | None:
    """Register one message and queue its triage. None when it was already registered. Flushes only."""
    parsed = parse_mail(raw.raw)
    received = (
        datetime.fromtimestamp(raw.received_ms / 1000, UTC) if raw.received_ms else parsed.sent_at or datetime.now(UTC)
    )
    email_id = await emails_repo.insert_if_new(
        db,
        gmail_message_id=raw.gmail_message_id,
        gmail_thread_id=raw.thread_id,
        history_id=raw.history_id,
        sender=parsed.sender,
        subject=parsed.subject[:998] if parsed.subject else None,
        received_at=received,
        status=None,  # waiting for triage
    )
    if email_id is None:
        return None
    path = eml_path(raw.gmail_message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.raw)

    mail = MailRef(raw.gmail_message_id, email_id)
    qualification = qualify(parsed.attachments, mail)
    for decision in qualification.rejected:
        await _record_rejection(db, decision, email_id, parsed)
    _log(
        db, email_id, "attachments", StepResult.WARNING if qualification.rejected else StepResult.SUCCESS,
        input_summary={"attachments": len(parsed.attachments)},
        output_summary={
            "qualifying": [d.as_log() for d in qualification.qualifying],
            "skipped": [d.as_log() for d in qualification.skipped],
            "rejected": [d.as_log() for d in qualification.rejected],
            "body_only": qualification.body_only,
        },
    )
    logs_repo.add_state_log(
        db,
        entity_type="ingested_email",
        entity_id=email_id,
        from_state=None,
        to_state="Received",
        reason=f"Polled from Gmail: {parsed.subject or '(no subject)'}",
        occurred_at=datetime.now(UTC),
    )
    await queue.enqueue(
        db, job_type=queue.JOB_MAIL_TRIAGE, entity_id=email_id, created_by=None, entity_type=queue.ENTITY_MAIL
    )
    return Ingested(email_id, len(qualification.qualifying), len(qualification.skipped), len(qualification.rejected))


async def _record_failure(raw: gmail.RawMessage, error: str) -> None:
    """The message could not be read: keep the fact (and the raw bytes) so it is seen, and never poll it again."""
    async with session_scope() as db:
        email_id = await emails_repo.insert_if_new(
            db,
            gmail_message_id=raw.gmail_message_id,
            gmail_thread_id=raw.thread_id,
            history_id=raw.history_id,
            received_at=datetime.fromtimestamp(raw.received_ms / 1000, UTC) if raw.received_ms else None,
            status=EmailStatus.FAILED,
        )
        if email_id is not None:
            path = eml_path(raw.gmail_message_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw.raw)
            _log(db, email_id, "ingest", StepResult.ERROR, error_message=error)
            await db.commit()


async def poll(client, *, limit: int = GMAIL_MAX_PER_POLL, query: str = GMAIL_POLL_QUERY) -> dict:  # noqa: ANN001
    """One pass over the mailbox. Each message is its own transaction, oldest first."""
    ids = await client.list_message_ids(query, LIST_WINDOW)
    async with session_scope() as db:
        known = await emails_repo.known_message_ids(db, ids)
    fresh = [i for i in reversed(ids) if i not in known][:limit]  # list is newest first; take the oldest unseen
    done = failed = 0
    for message_id in fresh:
        raw = await client.get_raw(message_id)
        try:
            async with session_scope() as db:
                result = await ingest_message(db, raw)
                await db.commit()
            done += result is not None
        except Exception as exc:  # unreadable mail: record it and carry on with the rest
            logger.exception("could not ingest Gmail message %s", message_id)
            failed += 1
            await _record_failure(raw, f"{type(exc).__name__}: {exc}")
    return {"listed": len(ids), "new": len(fresh), "ingested": done, "failed": failed}


async def run_scheduled(now: datetime) -> dict:
    """The `gmail_poll` schedule. A mailbox that is not authorized (or polling switched off) is skipped, not an error."""
    if not gmail.enabled():
        return {"skipped": "Gmail polling is off or the mailbox is not authorized"}
    client = client_factory()
    try:
        return await poll(client)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------- labels
_label_ids: dict[str, str] = {}


async def ensure_labels(client) -> dict[str, str]:  # noqa: ANN001
    """Create the AR/... labels that are missing (B9: at boot, so a broken setup shows at once, not at the first mail)."""
    _label_ids.update(await client.ensure_labels(GMAIL_LABELS))
    return dict(_label_ids)


async def ensure_labels_at_boot() -> None:
    """Called when the worker starts. Any failure stops the start, except an authorization that has run out: that one
    is logged loudly and polling reports it on every tick, so the API itself still comes up."""
    if not gmail.enabled():
        logger.info("Gmail polling is off or the mailbox is not authorized: no labels created")
        return
    client = client_factory()
    try:
        await ensure_labels(client)
    except gmail.GmailAuthError as exc:
        logger.error("GMAIL IS NOT AUTHORIZED: %s", exc.message)
    finally:
        await client.aclose()


async def unlabel_message(gmail_message_id: str, label: str) -> None:
    """Take one of our labels off a message (a mail sent to the other pipeline). Best effort, like label_message."""
    client = client_factory()
    try:
        if label not in _label_ids:
            await ensure_labels(client)
        await client.remove_labels(gmail_message_id, [_label_ids[label]])
    finally:
        await client.aclose()


async def label_message(gmail_message_id: str, label: str) -> None:
    """Put one of our labels on a message. Best effort: a failure is the caller's to log, never a reason to redo work."""
    client = client_factory()
    try:
        if label not in _label_ids:
            await ensure_labels(client)
        await client.add_labels(gmail_message_id, [_label_ids[label]])
    finally:
        await client.aclose()

