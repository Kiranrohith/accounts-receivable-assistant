"""Which e-mail attachments become payment jobs (decided at triage, before any job is spawned).

For each attachment, in this order:
  1. an inline / CID image (a logo, a signature picture)            -> SKIP   (decoration)
  2. an image under MIN_ATTACHMENT_KB                               -> SKIP   (an icon or tracking pixel)
     The size floor is for IMAGES ONLY: a PDF of any size above zero qualifies (small ERP and text PDFs
     are often well under 5 KB).
  3. a file of 0 bytes, or whose content is not what its name says
     (a PDF must start "%PDF-"; PNG and JPEG signatures too)        -> REJECT (the plain reason AND the link
     to the source mail, shown to staff; not retryable, never a payment job)
  4. a file type we do not read                                      -> REJECT (visible too: it might be a
     real advice in a format we cannot open, which a person must see)
  5. everything else                                                  -> QUALIFY: one payment job each

This filter is for MAIL attachments only. A person's manual upload is stricter on purpose: an empty file is
refused there with a visible 422 (see invoices/upload.check_upload).

Nothing is dropped silently: every skip and reject carries its reason and its source mail (skips go to the step log, rejects
are recorded against the ingested e-mail). When nothing qualifies, the payment is read from the mail's
own body instead (an .eml source document); a body without an amount goes to review (amount_missing).

A corrupt or password-protected PDF is NOT decided here: its signature is fine, so it qualifies, and
fails at parse time as a re-runnable payment job (case 9).
"""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath

from constants.constants import MIN_ATTACHMENT_KB
from core.services.invoices.parsing import SUPPORTED_EXTENSIONS
from core.services.invoices.upload import signature_matches

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"})
MIN_IMAGE_BYTES = MIN_ATTACHMENT_KB * 1024


@dataclass(frozen=True)
class MailRef:
    """The mail an attachment came with. A rejection must lead back to it, or staff cannot act on it."""

    gmail_message_id: str
    ingested_email_id: uuid.UUID | None = None

    @property
    def link(self) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{self.gmail_message_id}"


class Verdict(str, Enum):
    SKIP = "skip"
    REJECT = "reject"
    QUALIFY = "qualify"


@dataclass(frozen=True)
class Attachment:
    file_name: str
    data: bytes
    content_type: str | None = None
    inline: bool = False  # sent inline in the body (Content-Disposition inline, or a Content-ID)


@dataclass(frozen=True)
class Decision:
    attachment: Attachment
    verdict: Verdict
    reason: str
    mail: MailRef | None = None

    def as_log(self) -> dict:
        """What is written to the step log (skips) or recorded against the e-mail (rejections): the plain
        reason AND the way back to the source mail."""
        return {
            "file_name": self.attachment.file_name,
            "size_bytes": len(self.attachment.data),
            "verdict": self.verdict.value,
            "reason": self.reason,
            "source_mail": (
                {
                    "gmail_message_id": self.mail.gmail_message_id,
                    "ingested_email_id": str(self.mail.ingested_email_id) if self.mail.ingested_email_id else None,
                    "link": self.mail.link,
                }
                if self.mail
                else None
            ),
        }


@dataclass
class Qualification:
    qualifying: list[Decision] = field(default_factory=list)
    skipped: list[Decision] = field(default_factory=list)
    rejected: list[Decision] = field(default_factory=list)

    @property
    def body_only(self) -> bool:
        """Nothing usable was attached: the payment is read from the mail's own text."""
        return not self.qualifying


def _extension(attachment: Attachment) -> str:
    return PurePath(attachment.file_name).suffix.lower()


def is_image(attachment: Attachment) -> bool:
    kind = (attachment.content_type or "").lower()
    return _extension(attachment) in IMAGE_EXTENSIONS or kind.startswith("image/")


def classify_attachment(attachment: Attachment, mail: MailRef | None = None) -> Decision:
    def decide(verdict: Verdict, reason: str) -> Decision:
        return Decision(attachment, verdict, reason, mail)

    image = is_image(attachment)
    if image and attachment.inline:
        return decide(Verdict.SKIP, "inline image (e-mail decoration)")
    if image and len(attachment.data) < MIN_IMAGE_BYTES:
        return decide(Verdict.SKIP, f"image under {MIN_ATTACHMENT_KB} KB (a logo or icon)")
    if len(attachment.data) == 0:
        return decide(Verdict.REJECT, "attachment is empty")
    ext = _extension(attachment)
    if ext not in SUPPORTED_EXTENSIONS:
        return decide(Verdict.REJECT, f"file type '{ext or attachment.file_name}' is not supported")
    if not signature_matches(ext, attachment.data):
        return decide(Verdict.REJECT, "file content does not match its type")
    return decide(Verdict.QUALIFY, "readable payment-advice candidate")


def qualify(attachments: list[Attachment], mail: MailRef) -> Qualification:
    """`mail` is required: every skip and rejection carries the link back to its source mail."""
    result = Qualification()
    for attachment in attachments:
        decision = classify_attachment(attachment, mail)
        {
            Verdict.QUALIFY: result.qualifying,
            Verdict.SKIP: result.skipped,
            Verdict.REJECT: result.rejected,
        }[decision.verdict].append(decision)
    return result
