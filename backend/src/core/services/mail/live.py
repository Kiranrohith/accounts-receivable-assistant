"""The live view of mail: every mail being read right now, which step its attachments are at, and, for a short while after,
how each ended. A mail leaves the list once its time is up; one that needs a person is a "review" and shows where to decide
it (Take Action); one that gave up is a "failed" and shows in Failed Items.

Nothing here is written: it is read from the jobs and the step log the pipeline already keeps. Only mail is covered: a file a
person uploads is followed on the Upload Documents page.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.documents.queries import summarize
from core.services.jobs import queue
from core.services.mail.attachments import MailRef
from data.models.postgres import IngestedEmail, Job
from data.models.postgres.enums import EmailClassification, EmailStatus, JobState
from data.repositories.mail import emails_repo
from data.repositories.platform import jobs_repo, logs_repo
from schemas.documents.uploads import DocumentUploadSummary
from schemas.mail.live import FinishedHow, LiveDocument, LiveMail, LiveMailFeed, LiveState

DOCUMENT_JOBS = (
    queue.JOB_INTAKE, queue.JOB_INVOICE, queue.JOB_PO, queue.JOB_PAYMENT, queue.JOB_DISPUTE, queue.JOB_DISPUTE_PAYMENT,
    queue.JOB_PAYMENT_REMATCH,
)
LIVE_LIMIT = 100
_REVIEW = ("Needs review", "Dispute needs classification")
_IN_FLIGHT = ("waiting", "retrying", "running")


def stage_for(job_type: str, last_step: str | None, kind: str) -> str:
    """What a running document job is doing, from the last step it finished. `kind` is what the document turned out to be."""
    if job_type == queue.JOB_PAYMENT_REMATCH:
        return "Matching the payment again"
    if last_step is None:
        return "Opening the file"
    if last_step == "parse":
        return "Working out what it is" if job_type == queue.JOB_INTAKE else "Reading its details"
    if last_step == "classify":
        return "Reading its details"
    if last_step in ("extract", "dispute_extract"):
        return "Checking the details"
    if last_step == "validate":
        return "Finding the customer"
    if last_step in ("customer_match", "identify", "dispute_identify"):
        return {"payment": "Matching to invoices", "po": "Creating the purchase order", "invoice": "Creating the invoice"}.get(kind, "Saving it")
    if last_step == "match":
        return "Saving it"
    if last_step in ("route", "dispute_route"):
        return "Finishing"
    return "Working on it"


def _state(job: Job, claimable: bool) -> LiveState:
    if job.state is JobState.RUNNING:
        return "running"
    if job.state is JobState.QUEUED:
        # a job that has run before and is not yet due again is waiting out a back-off, not waiting for a free worker
        return "waiting" if claimable or (job.attempt_count or 0) == 0 else "retrying"
    return "finished"


def _how_document_ended(job: Job, upload: DocumentUploadSummary) -> FinishedHow:
    if job.state is not JobState.SUCCEEDED:
        return "failed"
    if upload.status in _REVIEW:
        return "review"
    if upload.status == "Rejected":
        return "rejected"
    return "done"


def _how_mail_ended(triage: Job | None, mail: IngestedEmail, documents: list[LiveDocument]) -> tuple[FinishedHow, str | None]:
    """(how, the Take Action queue when it needs the mail decided by a person)."""
    if (triage is not None and triage.state in (JobState.FAILED, JobState.DEAD_LETTERED)) or any(d.finished_how == "failed" for d in documents):
        return "failed", None
    if mail.status is EmailStatus.HUMAN_TRIAGE:
        return "review", "mail_triage"
    if any(d.finished_how == "review" for d in documents):
        return "review", None
    if any(d.finished_how == "rejected" for d in documents):
        return "rejected", None
    if mail.classification is EmailClassification.GENERAL and not documents:
        return "ignored", None
    return "done", None


_ENDED = {
    "done": "Processed",
    "ignored": "General mail, set aside",
    "review": "Needs a person",
    "rejected": "An attachment was rejected",
    "failed": "Gave up",
}


def _document(job: Job, upload: DocumentUploadSummary, steps: list[tuple[str, datetime | None]], line: dict[uuid.UUID, int]) -> LiveDocument:
    state = _state(job, job.id in line)
    # only the steps of THIS attempt count: an earlier attempt's steps would show a stage it has not reached again
    names = [name for name, when in steps if when is None or job.started_at is None or when >= job.started_at]
    if state == "running":
        stage = stage_for(job.job_type, names[-1] if names else None, upload.document_kind)
    elif state == "waiting":
        stage = "Waiting for its turn"
    elif state == "retrying":
        stage = "Waiting to try again"
    else:
        stage = upload.status
    return LiveDocument(
        state=state, stage=stage, ahead=line.get(job.id) if state == "waiting" else None,
        finished_how=_how_document_ended(job, upload) if state == "finished" else None, upload=upload,
    )


async def live_feed(db: AsyncSession, *, recent_seconds: int) -> LiveMailFeed:
    now = datetime.now(UTC)
    since = now - timedelta(seconds=recent_seconds)
    triage_rows = await jobs_repo.live_triage_jobs(db, finished_since=since, limit=LIVE_LIMIT)
    doc_rows = await jobs_repo.live_mail_document_jobs(db, job_types=DOCUMENT_JOBS, finished_since=since, limit=LIVE_LIMIT)

    triage = {mail.id: job for job, mail in triage_rows}
    by_mail: dict[uuid.UUID, list[tuple[Job, object]]] = {}
    for job, document in doc_rows:
        by_mail.setdefault(document.ingested_email_id, []).append((job, document))
    emails = {mail.id: mail for _, mail in triage_rows} | await emails_repo.emails_by_ids(db, set(by_mail) - set(triage))

    uploads = {u.source_document_id: u for u in await summarize(db, [d for _, d in doc_rows])}
    line = {job_id: place for place, job_id in enumerate(await jobs_repo.waiting_line(db, now))}
    steps = await logs_repo.steps_of_jobs(db, [job.id for job, _ in doc_rows])

    items: list[tuple[datetime, LiveMail]] = []
    for email_id, mail in emails.items():
        job = triage.get(email_id)
        documents = [_document(j, uploads[d.id], steps.get(j.id, []), line) for j, d in by_mail.get(email_id, [])]
        jobs = ([job] if job else []) + [j for j, _ in by_mail.get(email_id, [])]
        triage_state = _state(job, job.id in line) if job else None

        finished_at: datetime | None = None
        how: FinishedHow | None = None
        queue_name: str | None = None
        ahead: int | None = None
        if triage_state in _IN_FLIGHT:
            state: LiveState = triage_state  # type: ignore[assignment]
            stage = {"running": "Deciding what the mail is", "waiting": "Waiting to be read", "retrying": "Waiting to try again"}[state]
            ahead = line.get(job.id) if state == "waiting" else None
        elif any(d.state == "running" for d in documents):
            state = "running"
            running = [d for d in documents if d.state == "running"]
            stage = running[0].stage if len(documents) == 1 else f"Reading {len(documents)} attachments ({sum(d.state == 'finished' for d in documents)} done)"
        elif any(d.state in ("waiting", "retrying") for d in documents):
            state = "waiting"
            stage = "Its attachment is waiting for its turn"
            ahead = min((d.ahead for d in documents if d.ahead is not None), default=None)
        else:
            state = "finished"
            how, queue_name = _how_mail_ended(job, mail, documents)
            stage = _ENDED[how]
            finished_at = max((j.finished_at for j in jobs if j.finished_at), default=None)

        review_id = mail.id if queue_name == "mail_triage" else None
        activity = max((t for j in jobs for t in (j.finished_at, j.started_at, j.queued_at) if t), default=now)
        items.append((activity, LiveMail(
            email_id=mail.id, mail_link=MailRef(mail.gmail_message_id).link, sender=mail.sender, subject=mail.subject,
            received_at=mail.received_at, mail_class=mail.classification.value if mail.classification else None,
            confidence=mail.classification_confidence, state=state, stage=stage, ahead=ahead, finished_at=finished_at,
            finished_how=how, review_queue=queue_name, review_id=review_id, documents=documents,
        )))
    # what is still going on first, then what ended, each newest first
    items.sort(key=lambda pair: (pair[1].state == "finished", -pair[0].timestamp()))
    return LiveMailFeed(as_of=now, items=[mail for _, mail in items])
