import sys
from pathlib import Path
from docx import Document

out = Path(sys.argv[1]); OURS = "33AAHCG2210A1Z7"

def invoice(name, *, title="TAX INVOICE", number="GW-E2E-001", issued="05/10/2026", due="04/11/2026",
            to_name="Zenith Softlabs Pvt Ltd", to_gstin="33AABCZ1234F1Z5", currency="INR", total="100000.00",
            item=("Consulting services", "1", "100000.00")):
    d = Document()
    d.add_heading(title, 1)
    d.add_paragraph("Genworx Technologies Pvt Ltd\n12 Anna Salai, Chennai 600002\nGSTIN: " + OURS)
    d.add_paragraph(f"Invoice No: {number}")
    d.add_paragraph(f"Invoice Date: {issued}")
    if due: d.add_paragraph(f"Due Date: {due}")
    bill = f"Bill To:\n{to_name}\nBengaluru"
    if to_gstin: bill += f"\nGSTIN: {to_gstin}"
    d.add_paragraph(bill)
    t = d.add_table(rows=2, cols=3); t.style = "Table Grid"
    for i, h in enumerate(("Description", "Qty", f"Amount ({currency})")): t.rows[0].cells[i].text = h
    for i, v in enumerate(item): t.rows[1].cells[i].text = v
    d.add_paragraph(f"Total {currency} {total}")
    d.save(out / name)

invoice("c01_gstin_match.docx")
invoice("c02_name_match.docx", number="GW-E2E-002", to_name="Meridian Tech Solutions Pvt Ltd", to_gstin=None, total="25000.00", item=("Support", "5", "25000.00"))
invoice("c03_debit_note.docx", title="DEBIT NOTE", number="GW-E2E-003", total="5000.00", item=("Late fee", "1", "5000.00"))
invoice("c04_unknown_customer.docx", number="GW-E2E-004", to_name="Unknown Widgets Ltd", to_gstin="07AAACU9999A1Z1")
invoice("c05_identity_conflict.docx", number="GW-E2E-005", to_name="Meridian Tech Solutions Pvt Ltd", to_gstin="33AABCZ1234F1Z5")
invoice("c06_usd.docx", number="GW-E2E-006", currency="QAR", total="1000.00", item=("Licence", "1", "1000.00"))
invoice("c07_duplicate_number.docx", number="GW-E2E-001", total="777.00", item=("Different thing", "1", "777.00"))
invoice("c09_no_due_date.docx", number="GW-E2E-009", due=None)
invoice("c11_review_race.docx", number="GW-E2E-011", to_name="Unknown Widgets Ltd", to_gstin=None)
invoice("c10_name_ok_gstin_unknown.docx", number="GW-E2E-010", to_gstin="07AAACZ0000A1Z1")

d = Document()
d.add_heading("PURCHASE ORDER", 1)
d.add_paragraph("PO No PO-7781\nBuyer: Zenith Softlabs Pvt Ltd\nPlease supply 10 laptops by 30 Oct 2026.\nTotal INR 500000")
d.save(out / "c08_purchase_order.docx")
print("made", len(list(out.glob("*.docx"))))
