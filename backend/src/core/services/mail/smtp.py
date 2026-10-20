"""Sending one e-mail over SMTP (Mailpit in development).

Plain stdlib smtplib run in a thread, so no new dependency and the event loop is never blocked.
Only the TRANSPORT is retried (the server unreachable, a connection dropped, a 4xx "try later"):
3 retries after 3s, 9s and 15s. A refusal that will not change (a rejected recipient or sender, a
5xx reply) fails at once. The verdict about whether to send at all is never revisited here.
"""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config.settings import get_settings
from constants.constants import SMTP_RETRY_DELAYS_SECONDS, SMTP_TIMEOUT_SECONDS

# Module-level so tests can shorten them.
retry_delays_seconds: tuple[float, ...] = SMTP_RETRY_DELAYS_SECONDS


class SmtpError(Exception):
    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.message = message
        self.permanent = permanent


@dataclass(frozen=True)
class Sent:
    message_id: str
    attempts: int


def build_message(
    *, to: str, subject: str, body: str, sender: str, in_reply_to: str | None = None
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or "localhost")
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message


def _send_once(host: str, port: int, message: EmailMessage) -> None:
    """One attempt. Raises SmtpError, marked permanent or not."""
    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.send_message(message)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPNotSupportedError) as exc:
        raise SmtpError(f"The mail server refused it: {exc}", permanent=True) from exc
    except smtplib.SMTPResponseException as exc:
        raise SmtpError(
            f"The mail server answered {exc.smtp_code}: {exc.smtp_error!r}", permanent=exc.smtp_code >= 500
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:  # unreachable, dropped, timed out
        raise SmtpError(f"The mail server could not be reached: {exc}", permanent=False) from exc


async def send(message: EmailMessage) -> Sent:
    """Send with transport retries. Raises SmtpError once they are used up (or at once if permanent)."""
    settings = get_settings()
    attempts = 0
    for delay in (*retry_delays_seconds, None):
        attempts += 1
        try:
            await asyncio.to_thread(_send_once, settings.smtp_host, settings.smtp_port, message)
            return Sent(str(message["Message-ID"]), attempts)
        except SmtpError as exc:
            if exc.permanent or delay is None:
                raise SmtpError(f"{exc.message} (after {attempts} attempt(s))", permanent=exc.permanent) from exc
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
