"""The reports as PDF: a title, a few summary lines, and a table that repeats its heading on every page."""

from collections.abc import Sequence
from decimal import Decimal

from fpdf import FPDF
from fpdf.enums import Align

from core.services.documents.pdf import latin1


def inr(amount: Decimal | int | str | None) -> str:
    """INR 1,23,456.00 with Indian digit grouping; fpdf's built-in fonts have no rupee sign, so the code is written out."""
    if amount is None:
        return "-"
    value = Decimal(amount).quantize(Decimal("0.01"))
    sign = "-" if value < 0 else ""
    whole, _, paise = f"{abs(value):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"INR {sign}{','.join([*groups, tail])}.{paise}"


def table_pdf(
    *,
    title: str,
    lines: Sequence[str],
    headers: Sequence[str],
    widths: Sequence[float],
    right: Sequence[int],
    rows: Sequence[Sequence[str]],
    summary: Sequence[tuple[str, str]] = (),
    empty: str = "There is nothing to report.",
) -> bytes:
    """A landscape A4 report. `right` lists the (0-based) columns whose text is right-aligned (the numbers)."""
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(latin1(title))
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, latin1(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(90, 90, 90)
    for line in lines:
        pdf.cell(0, 5, latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    if summary:
        pdf.set_font("Helvetica", "B", 10)
        for label, value in summary:
            pdf.cell(60, 6, latin1(label))
            pdf.cell(0, 6, latin1(value), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    pdf.set_font("Helvetica", size=9)
    if not rows:
        pdf.cell(0, 8, latin1(empty), new_x="LMARGIN", new_y="NEXT")
    else:
        aligns = tuple(Align.R if i in right else Align.L for i in range(len(headers)))
        with pdf.table(col_widths=tuple(widths), text_align=aligns, line_height=5.5, first_row_as_headings=True) as table:
            head = table.row()
            for text in headers:
                head.cell(latin1(text))
            for values in rows:
                row = table.row()
                for text in values:
                    row.cell(latin1(text))
    return bytes(pdf.output())
