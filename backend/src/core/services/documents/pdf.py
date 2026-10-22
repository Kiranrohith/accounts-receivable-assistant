"""Rendering one of our own documents (a credit note, a debit note) to a PDF.

The templates in src/templates/notes are plain HTML (headings, paragraphs, bold, line breaks, a table).
fpdf2 draws them with its built-in fonts, which cover Latin-1 only: the few typographic characters people
paste into names are turned into their plain form first, anything else becomes "?" rather than failing the
whole document.
"""

import re

from fpdf import FPDF

_PLAIN = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "₹": "INR "}
)
_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)


def html_to_pdf(html: str) -> bytes:
    body = _BODY.search(html)
    text = (body.group(1) if body else html).translate(_PLAIN)
    text = text.encode("latin-1", "replace").decode("latin-1")
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.write_html(text)
    return bytes(pdf.output())
