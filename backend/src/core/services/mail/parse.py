"""Reading a raw e-mail (RFC 822 bytes, as Gmail returns it) into what the pipeline needs.

Pure: no network, no database. The newest message's own text is the body (a quoted older message below it is the
extraction prompt's problem, not ours). A forwarded mail is read as it arrives: the forwarder is the sender.
"""

import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from core.services.mail.attachments import Attachment


@dataclass(frozen=True)
class ParsedMail:
    message_id_header: str | None
    sender: str | None  # the bare address, lower case
    sender_name: str | None
    to: tuple[str, ...]
    subject: str
    sent_at: datetime | None
    in_reply_to: str | None
    references: tuple[str, ...]
    body: str
    attachments: list[Attachment] = field(default_factory=list)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001, ANN201
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001, ANN201
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):  # noqa: ANN001, ANN201
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


def _body_of(message: EmailMessage) -> str:
    plain = message.get_body(preferencelist=("plain",))
    if plain is not None:
        text = plain.get_content().strip()
        if text:
            return text
    html = message.get_body(preferencelist=("html",))
    return _html_to_text(html.get_content()) if html is not None else ""


def _attachment_name(part: Message, index: int) -> str:
    name = part.get_filename()
    if name:
        return name
    extension = mimetypes.guess_extension(part.get_content_type()) or ""
    return f"attachment-{index}{extension}"


def _attachments_of(message: EmailMessage) -> list[Attachment]:
    found: list[Attachment] = []
    for index, part in enumerate(message.walk(), start=1):
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()
        has_name = bool(part.get_filename())
        has_cid = bool(part.get("Content-ID"))
        is_text_body = content_type in ("text/plain", "text/html") and not has_name and disposition != "attachment"
        if is_text_body:
            continue
        if not (has_name or disposition == "attachment" or (content_type.startswith("image/") and (has_cid or disposition == "inline"))):
            continue
        data = part.get_payload(decode=True) or b""
        found.append(
            Attachment(
                file_name=_attachment_name(part, index),
                data=data,
                content_type=content_type,
                inline=disposition == "inline" or has_cid,
            )
        )
    return found


def parse_mail(raw: bytes) -> ParsedMail:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    senders = getaddresses([str(message.get("From", ""))])
    name, address = senders[0] if senders else ("", "")
    try:
        sent_at = parsedate_to_datetime(str(message["Date"])) if message["Date"] else None
    except (TypeError, ValueError):
        sent_at = None
    references = tuple(str(message.get("References", "")).split())
    return ParsedMail(
        message_id_header=str(message["Message-ID"]).strip() if message["Message-ID"] else None,
        sender=address.strip().lower() or None,
        sender_name=name.strip() or None,
        to=tuple(a.strip().lower() for _, a in getaddresses([str(message.get("To", ""))]) if a.strip()),
        subject=str(message.get("Subject", "") or "").strip(),
        sent_at=sent_at,
        in_reply_to=str(message["In-Reply-To"]).strip() if message["In-Reply-To"] else None,
        references=references,
        body=_body_of(message),
        attachments=_attachments_of(message),
    )


def to_markdown(mail: ParsedMail) -> str:
    """A mail as the text a document reader would get: its own words, for a payment or complaint that has no file."""
    head = [f"From: {mail.sender_name + ' ' if mail.sender_name else ''}<{mail.sender or 'unknown'}>"]
    if mail.sent_at:
        head.append(f"Date: {mail.sent_at.isoformat()}")
    head.append(f"Subject: {mail.subject or '(none)'}")
    return "\n".join(head) + "\n\n" + (mail.body or "(no text)")
