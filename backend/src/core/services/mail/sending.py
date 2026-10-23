"""What every outgoing e-mail of ours does around the send itself, in one place.

The transport (allowlist, STARTTLS, retries) is `smtp.send`. This module adds what every caller needs on top:

  deliver()         send one message and leave a step-log row either way (the plain reason on a failure)
  tell_failure()    the SendFailure notice to the people who must act on it

Callers decide what a failed send means for their own record (a dispute draft stays a draft, a reminder is not
sent, a credit-note job retries or fails); they never write their own log line or notice.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services import notifications
from core.services.mail import smtp
from data.models.postgres import Role, User
from data.models.postgres.enums import NotificationType, StepResult
from data.repositories.platform import logs_repo

ADMIN_ROLE = "ADM"
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def clean_address(value: str | None) -> str | None:
    """The address as one trimmed string, or None when it cannot be one (blank, no @, a list of addresses)."""
    value = (value or "").strip()
    return value if _ADDRESS.match(value) else None


@dataclass(frozen=True)
class Delivery:
    to: str
    sent: smtp.Sent | None = None
    error: smtp.SmtpError | None = None

    @property
    def ok(self) -> bool:
        return self.sent is not None

    @property
    def permanent(self) -> bool:
        """A failure no retry will change (not on the allowlist, an address the server refuses)."""
        return self.error is not None and self.error.permanent

    @property
    def reason(self) -> str:
        return self.error.message if self.error else ""


def log_send(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    step_name: str,
    to: str,
    sent: smtp.Sent | None = None,
    error: smtp.SmtpError | None = None,
    job_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """One step-log row for one send: success (with the message id) or error (with the plain reason)."""
    summary = {"to": to, **(extra or {})}
    if sent is not None:
        summary.update(message_id=sent.message_id, attempts=sent.attempts)
    logs_repo.add_step_log(
        db,
        job_id=job_id,
        entity_type=entity_type,
        entity_id=entity_id,
        step_name=step_name,
        result=StepResult.SUCCESS if sent is not None else StepResult.ERROR,
        input_summary={"to": to},
        output_summary=summary if sent is not None else None,
        error_message=error.message if error is not None else None,
        occurred_at=datetime.now(UTC),
    )


async def deliver(
    db: AsyncSession,
    message: EmailMessage,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    step_name: str,
    job_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> Delivery:
    """Send and log. Never raises for a refused or failed send: the caller reads the Delivery."""
    to = ", ".join(smtp.recipients_of(message)) or str(message["To"] or "")
    try:
        sent = await smtp.send(message)
    except smtp.SmtpError as exc:
        log_send(db, entity_type=entity_type, entity_id=entity_id, step_name=step_name, to=to, error=exc, job_id=job_id, extra=extra)
        return Delivery(to, error=exc)
    log_send(db, entity_type=entity_type, entity_id=entity_id, step_name=step_name, to=to, sent=sent, job_id=job_id, extra=extra)
    return Delivery(to, sent=sent)


async def admin_ids(db: AsyncSession) -> list[uuid.UUID]:
    """The active administrators: who is told about a send nobody in particular owns."""
    rows = await db.scalars(
        select(User.id).join(Role, Role.id == User.role_id).where(Role.code == ADMIN_ROLE, User.is_active, Role.is_active)
    )
    return list(rows)


async def tell_failure(
    db: AsyncSession,
    recipients: list[uuid.UUID | None],
    text: str,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    actor: uuid.UUID | None = None,
) -> None:
    """A SendFailure notice to each distinct person (None entries are skipped). With nobody named, the admins."""
    people = list(dict.fromkeys(r for r in recipients if r is not None)) or await admin_ids(db)
    for person in people:
        await notifications.create(
            db, person, NotificationType.SEND_FAILURE, text, entity_type=entity_type, entity_id=entity_id, actor=actor
        )
