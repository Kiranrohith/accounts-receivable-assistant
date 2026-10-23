"""The credit note as a document: the PDF the customer receives, kept as a source document.

Rendered once from src/templates/notes/credit_note.html.j2 and stored under raw_files_dir/generated; the
note points at it through generated_document_id (uploaded_via=generated), so a resend attaches the very same
file. Flushes only: the caller commits.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import BUSINESS_TIMEZONE
from core.services.documents.pdf import html_to_pdf
from core.services.invoices.amounts import format_money
from core.services.mail import templates
from data.models.postgres import Customer, Note
from data.models.postgres.enums import DocKind, DocType, UploadedVia
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.payments import payments_repo


def file_name(note: Note) -> str:
    return f"{note.note_number}.pdf"


async def render(db: AsyncSession, note: Note, customer: Customer) -> bytes:
    invoice = await invoices_repo.get_invoice(db, note.linked_invoice_id) if note.linked_invoice_id else None
    payment = await payments_repo.get_payment(db, note.linked_payment_id) if note.linked_payment_id else None
    issued = (note.created_at or datetime.now(UTC)).astimezone(ZoneInfo(BUSINESS_TIMEZONE))
    html = templates.render_html(
        "credit_note",
        note_number=note.note_number,
        issue_date=f"{issued.day} {issued:%b %Y}",
        customer_name=customer.customer_name,
        customer_gstin=customer.gstin or "",
        note_amount=format_money(note.amount, note.currency),
        note_reason=note.reason or "Credit in your favour",
        linked_invoice_number=invoice.invoice_number if invoice else "",
        linked_payment_reference=payment.reference_number if payment and payment.reference_number else "",
    )
    return html_to_pdf(html)


async def ensure_pdf(db: AsyncSession, note: Note, customer: Customer) -> bytes:
    """The note's PDF: read back when it was made before, otherwise rendered, stored and linked now."""
    if note.generated_document_id is not None:
        document = await documents_repo.get_document(db, note.generated_document_id)
        if document is not None and document.file_path:
            stored = Path(document.file_path)
            if stored.exists():
                return stored.read_bytes()
            data = await render(db, note, customer)  # the file went missing: write it again where it belongs
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(data)
            return data
    data = await render(db, note, customer)
    path = Path(get_settings().raw_files_dir) / "generated" / file_name(note)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    document = await documents_repo.insert_document(
        db,
        doc_type=DocType.PDF,
        uploaded_via=UploadedVia.GENERATED,
        doc_kind=DocKind.PAYMENT,  # no pipeline reads generated documents; the kind only has to be one of the four
        file_name=file_name(note),
        file_path=str(path),
        file_hash=hashlib.sha256(data).hexdigest(),
    )
    note.generated_document_id = document.id
    return data
