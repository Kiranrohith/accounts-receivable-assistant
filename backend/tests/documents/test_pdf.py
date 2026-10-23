"""Our own documents as PDFs: the credit note template renders, becomes a real PDF with its words in it, and never
fails on a character the built-in fonts do not have."""

import pypdfium2

from core.services.documents.pdf import html_to_pdf
from core.services.mail import templates

NOTE = dict(
    note_number="CN-2026-000007", issue_date="22 Oct 2026", customer_name="Zenith Softlabs Pvt Ltd",
    customer_gstin="29ABCDE1234F1Z5", note_amount="INR 1,234.00", note_reason="Overpayment of INV-1001",
    linked_invoice_number="INV-1001", linked_payment_reference="UTR123",
)


def words_of(data: bytes) -> str:
    return "\n".join(page.get_textpage().get_text_range() for page in pypdfium2.PdfDocument(data))


def test_the_credit_note_becomes_a_pdf_with_every_fact_on_it():
    data = html_to_pdf(templates.render_html("credit_note", **NOTE))
    assert data.startswith(b"%PDF")
    text = words_of(data)
    for fact in ("CREDIT NOTE", "CN-2026-000007", "22 Oct 2026", "Zenith Softlabs Pvt Ltd", "29ABCDE1234F1Z5", "INR 1,234.00", "INV-1001", "UTR123"):
        assert fact in text, fact


def test_optional_lines_are_left_out_when_there_is_nothing_to_say():
    data = html_to_pdf(templates.render_html("credit_note", **{**NOTE, "customer_gstin": "", "linked_invoice_number": "", "linked_payment_reference": ""}))
    text = words_of(data)
    assert "GSTIN" not in text and "Invoice:" not in text and "Payment:" not in text


def test_characters_the_fonts_lack_do_not_fail_the_document():
    data = html_to_pdf(templates.render_html("credit_note", **{**NOTE, "customer_name": "Zenith – “Bharat” சென்னை Ltd"}))
    text = words_of(data)
    assert data.startswith(b"%PDF") and 'Zenith - "Bharat"' in text and "Ltd" in text


def test_the_debit_note_template_renders_to_html():
    html = templates.render_html(
        "debit_note", invoice_number="DN-1", invoice_date="1 Oct 2026", due_date="31 Oct 2026", customer_name="Zenith",
        customer_gstin="", lines=[{"description": "Late fee", "amount": "INR 500.00"}], total="INR 500.00", linked_reference="INV-1",
    )
    assert "DEBIT NOTE" in html and "Late fee" in html
