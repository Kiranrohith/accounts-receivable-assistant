"""The human side of mail triage: the queue of mails a person must route, the ignored list, and the decision.

A person may route a mail that is waiting in the queue, or pull one back out of the ignored list (E9: a wrong
automatic call is fixed in one click). `general` sends it to the ignored list; any other class records the
decision and hands the mail to its pipeline (jobs are queued in the same transaction).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError, PermissionDeniedError
from core.services.mail import handoff, ingest, reroute
from core.services.mail.attachments import MailRef
from core.services.mail.parse import parse_mail
from core.services.mail.triage import apply_decision
from core.services.mail.classification import Decision, Route
from data.models.postgres import IngestedEmail, User
from data.models.postgres.enums import EmailClassification, EmailStatus
from data.repositories import unit_of_work
from data.repositories.disputes import disputes_repo
from data.repositories.mail import emails_repo
from data.repositories.payments import payments_repo
from data.repositories.platform import logs_repo
from schemas.common import Page
from schemas.mail.triage import AttachmentView, ClassifyMail, ClassifyResult, TriageDetail, TriageItem

logger = logging.getLogger(__name__)
PREVIEW = 300


def _is_ignored(email: IngestedEmail) -> bool:
    return email.status is EmailStatus.PROCESSED and email.classification is EmailClassification.GENERAL


def _is_unclassified(email: IngestedEmail) -> bool:
    """Registered but never classified (the models were down): a person may route it. The automatic triage job later
    finds it decided and finishes without touching it."""
    return email.status is None and email.classification is None


async def _latest_step(db: AsyncSession, email: IngestedEmail, step: str) -> dict | None:
    rows = await logs_repo.list_step_logs(db, entity_type="ingested_email", entity_id=email.id)
    matching = [r for r in rows if r.step_name == step and r.output_summary]
    return matching[-1].output_summary if matching else None


def _read(email: IngestedEmail):  # noqa: ANN202
    try:
        return parse_mail(ingest.eml_path(email.gmail_message_id).read_bytes())
    except OSError:
        return None


async def _item(db: AsyncSession, email: IngestedEmail, *, full: bool = False) -> TriageItem | TriageDetail:
    triage = await _latest_step(db, email, "triage")
    attachments = await _latest_step(db, email, "attachments") or {}
    mail = _read(email)
    body = (mail.body if mail else "").strip()
    listed = [*attachments.get("qualifying", []), *attachments.get("skipped", []), *attachments.get("rejected", [])]
    link = (triage or {}).get("thread") or (await _latest_step(db, email, "handoff") or {}).get("thread")
    fields = dict(
        email_id=email.id,
        gmail_message_id=email.gmail_message_id,
        mail_link=MailRef(email.gmail_message_id).link,
        sender=email.sender,
        subject=email.subject,
        received_at=email.received_at,
        status=email.status.value if email.status else None,
        suggested_class=email.classification.value if email.classification else None,
        confidence=email.classification_confidence,
        reason=(triage or {}).get("reason"),
        attachments=len(listed),
        body_preview=body[:PREVIEW],
        replies_to=f"{link['reference']} ({link['status']}), found by {link['how']}" if link else None,
    )
    if not full:
        return TriageItem(**fields)
    timeline = [
        {"at": r.occurred_at, "from": r.from_state, "to": r.to_state, "reason": r.reason}
        for r in await logs_repo.list_state_log(db, entity_type="ingested_email", entity_id=email.id)
    ]
    return TriageDetail(
        **fields,
        body=body,
        attachment_list=[
            AttachmentView(file_name=a["file_name"], size_bytes=a["size_bytes"], verdict=a["verdict"], reason=a["reason"])
            for a in listed
        ],
        timeline=timeline,
        produced=await _produced(db, email),
        reroute_to=reroute.can_reroute_to(email),
    )


async def _produced(db: AsyncSession, email: IngestedEmail) -> list[dict]:
    payments = [
        {"kind": "payment", "id": p.id, "state": p.state.value}
        for p in await payments_repo.payments_of_mail(db, email.id)
    ]
    disputes = [
        {"kind": "dispute", "id": d.id, "state": d.status.value, "reference": f"DSP-{str(d.id)[:8].upper()}"}
        for d in await disputes_repo.disputes_of_mail(db, email.id)
    ]
    return [*payments, *disputes]


async def list_items(db: AsyncSession, *, which: str, limit: int, offset: int) -> list[TriageItem]:
    """`queue` (waiting for a person, including mails the reader never classified), `ignored` (general mail) or `routed` (handed to a pipeline)."""
    if which == "routed":
        rows = await emails_repo.list_routed(db, limit=limit, offset=offset)
    else:
        rows = await emails_repo.list_by_state(db, waiting_for_person=which == "queue", limit=limit, offset=offset)
    return [await _item(db, e) for e in rows]


async def mail_log(
    db: AsyncSession, *, outcome: str, search: str | None, sort: str, descending: bool, limit: int, offset: int
) -> Page[TriageItem]:
    """Every mail we have read, one page at a time, with what became of it: waiting, routed, ignored, failed or still being read."""
    rows, total = await emails_repo.search_log(db, outcome=outcome, search=search, sort=sort, descending=descending, limit=limit, offset=offset)
    return Page(items=[await _item(db, e) for e in rows], total=total)


async def get_detail(db: AsyncSession, email_id: uuid.UUID) -> TriageDetail:
    email = await emails_repo.get_email(db, email_id)
    if email is None:
        raise NotFoundError("Mail not found")
    return await _item(db, email, full=True)


async def classify(
    db: AsyncSession, user: User, email_id: uuid.UUID, body: ClassifyMail, *, ignored_only: bool = False
) -> ClassifyResult:
    email = await emails_repo.lock_email(db, email_id)
    if email is None:
        raise NotFoundError("Mail not found")
    if ignored_only and not _is_ignored(email):  # the log-only right: a mail set aside may be sent on, nothing else may be routed
        raise PermissionDeniedError("Your role may send on only a mail that was set aside as general mail")
    if email.status is not EmailStatus.HUMAN_TRIAGE and not _is_ignored(email) and not _is_unclassified(email):
        raise ConflictError(
            "This mail is not waiting for a person: only mails in the triage queue, or in the ignored list, can be routed here"
        )
    chosen = EmailClassification(body.mail_class.value)
    note = f" ({body.note})" if body.note else ""
    decision = Decision(
        Route.IGNORE if chosen is EmailClassification.GENERAL else Route.AUTO,
        chosen,
        100,
        f"routed by {user.username}{note}",
    )
    old_labels = tuple(x for x in (email.label_applied or "").split(", ") if x)  # AR/Triage or AR/Ignored, if it had one
    label = apply_decision(db, email, decision, by=f"Person ({user.username})")
    email.updated_by = user.id
    labels = (label,) if label else ()
    handed = None
    if chosen is not EmailClassification.GENERAL:
        handed = await handoff.hand_off(db, email, chosen, created_by=user.id)
        labels = handed.labels
    await unit_of_work.commit(db)
    # the mail leaves the queue or the ignored list: its old Gmail label comes off before the new one goes on
    await handoff.unlabel_all(email, tuple(x for x in old_labels if x not in labels))
    await handoff.label_all(email, labels)
    if handed is None:
        message = "Moved to the ignored list."
    else:
        message = f"Handed to its pipeline as {chosen.value}: {len(handed.jobs)} job(s) queued" + (
            f"; skipped: {'; '.join(handed.skipped)}" if handed.skipped else "."
        )
    return ClassifyResult(
        email_id=email.id, mail_class=chosen.value, status=email.status.value if email.status else None, message=message
    )
