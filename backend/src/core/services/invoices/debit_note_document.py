"""A debit note as a document: the PDF that rides on its reminder e-mails (a debit note is an invoice of type DEBIT_NOTE).

Rendered from src/templates/notes/debit_note.html.j2, stored once under raw_files_dir/generated (uploaded_via=generated)
and linked through invoices.generated_document_id, so every reminder of the same debit note attaches the same file.
Flushes only: the caller commits.
"""

import hashlib
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.services.documents.pdf import html_to_pdf
from core.services.invoices.amounts import format_money
from core.services.mail import templates
from data.models.postgres import Customer, Invoice
from data.models.postgres.enums import DocKind, DocType, UploadedVia
from data.repositories.invoices import documents_repo, invoices_repo


def file_name(invoice: Invoice) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in invoice.invoice_number)
    return f"{safe}.pdf"


async def render(db: AsyncSession, invoice: Invoice, customer: Customer) -> bytes:
    lines = [
        {"description": item.description, "amount": format_money(item.line_total, invoice.invoice_currency) if item.line_total is not None else ""}
        for item in await invoices_repo.current_line_items(db, invoice)
    ]
    html = templates.render_html(
        "debit_note",
        invoice_number=invoice.invoice_number,
        invoice_date=f"{invoice.invoice_date:%d %b %Y}",
        due_date=f"{invoice.due_date:%d %b %Y}",
        customer_name=customer.customer_name,
        customer_tax_id=customer.tax_id or "",
        customer_tax_id_label=customer.tax_id_type.value if customer.tax_id_type else "Tax ID",
        lines=lines,
        total=format_money(invoice.collectible_total if invoice.invoice_currency == "INR" else (invoice.amount_foreign or invoice.collectible_total), invoice.invoice_currency),
        linked_reference=invoice.po_number or "",
    )
    return html_to_pdf(html)


async def ensure_pdf(db: AsyncSession, invoice: Invoice, customer: Customer) -> bytes:
    """The debit note's PDF: read back when it was made before, otherwise rendered, stored and linked now."""
    if invoice.generated_document_id is not None:
        document = await documents_repo.get_document(db, invoice.generated_document_id)
        if document is not None and document.file_path:
            stored = Path(document.file_path)
            if stored.exists():
                return stored.read_bytes()
            data = await render(db, invoice, customer)  # the file went missing: write it again where it belongs
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(data)
            return data
    data = await render(db, invoice, customer)
    path = Path(get_settings().raw_files_dir) / "generated" / f"debit-{file_name(invoice)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    document = await documents_repo.insert_document(
        db,
        doc_type=DocType.PDF,
        uploaded_via=UploadedVia.GENERATED,
        doc_kind=DocKind.INVOICE,
        file_name=file_name(invoice),
        file_path=str(path),
        file_hash=hashlib.sha256(data).hexdigest(),
    )
    invoice.generated_document_id = document.id
    return data
