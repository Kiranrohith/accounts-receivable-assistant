"""The real model reads the customer's tax ID with the new prompts: type, value as printed and label. Uses the configured
model keys and no database (the parsed text is given directly); each case is one model call.

Run:  uv run python tests/api_suites/api_test_taxid_llm.py
"""

import asyncio
import sys

sys.path.insert(0, "src")
from config.settings import get_settings
from core.services.invoices.extraction import extract_invoice
from core.services.purchase_orders.extraction import extract_purchase_order
from data.repositories.customers.tax_ids import normalize_tax_id

s = get_settings()
OURS = s.our_gstin
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


def invoice_text(buyer_block: str, number="INV-7001", extra_seller="") -> str:
    return f"""# TAX INVOICE

**Genworx Technologies Pvt Ltd**
12 Anna Salai, Chennai 600002
GSTIN: {OURS} {extra_seller}

Invoice No: {number}
Invoice Date: 05/10/2026
Due Date: 04/11/2026

**Bill To:**
{buyer_block}

| Description | Qty | Unit price | Amount |
|---|---|---|---|
| Consulting services | 1 | 10,000.00 | 10,000.00 |

Subtotal: 10,000.00
Total: 10,000.00
Currency: INR
"""


def po_text(buyer_block: str) -> str:
    return f"""# PURCHASE ORDER

{buyer_block}

PO No: PO-5501
PO Date: 02/10/2026

Supplier: Genworx Technologies Pvt Ltd, GSTIN {OURS}

| Description | Qty | Unit price (INR) |
|---|---|---|
| Laptop | 10 | 50,000.00 |

Order total INR 500,000.00
"""


async def invoice_case(label, block, kind, expected, **kw):
    out = await extract_invoice(invoice_text(block, **kw))
    inv = out.invoice
    got = normalize_tax_id(inv.tax_id_value)
    check(f"{label}: type {kind}", (inv.tax_id_type.value if inv.tax_id_type else None) == kind, f"got {inv.tax_id_type} {inv.tax_id_value!r} label={inv.tax_id_raw_label!r}")
    check(f"{label}: value {expected or 'none'}", got == normalize_tax_id(expected), f"got {inv.tax_id_value!r}")
    return inv


async def main():
    inv = await invoice_case("UK VAT", "Brightwave Digital Ltd\n14 Kingsway, London WC2B 6UN, United Kingdom\nVAT Reg No: GB 284 7731 09", "VAT", "GB 284 7731 09")
    check("UK VAT: the value is as printed and the label is kept", inv.tax_id_value is not None and "284" in inv.tax_id_value and (inv.tax_id_raw_label or "").upper().startswith("VAT"), f"{inv.tax_id_value!r} {inv.tax_id_raw_label!r}")
    await invoice_case("UAE TRN", "Al Noor Trading Technologies LLC\nSheikh Zayed Road, Dubai, UAE\nTRN: 100234567800003", "TRN", "100234567800003")
    await invoice_case("US EIN", "Halcyon Systems Inc\n500 Market St, San Francisco, CA\nEIN: 94-3456789", "EIN", "94-3456789")
    await invoice_case("SG UEN (not a listed kind)", "Kestrel Cloud Pte Ltd\n1 Raffles Place, Singapore\nUEN: 201934512K", "OTHER", "201934512K")
    await invoice_case("India GSTIN", "Zenith Softlabs Pvt Ltd\nBengaluru\nGSTIN: 29ABCDE1234F1Z5", "GSTIN", "29ABCDE1234F1Z5")
    await invoice_case("our id at the foot, customer's in the bill-to block", "Zenith Softlabs Pvt Ltd\nBengaluru\nGST Reg No: 29ABCDE1234F1Z5", "GSTIN", "29ABCDE1234F1Z5", extra_seller="\n(Registered office GSTIN again: " + OURS + ")")
    await invoice_case("no customer id printed (only ours)", "Plain Buyers Ltd\nPune\nPAN: AAACP1234C   Phone: +91 20 4000 1234", None, None)
    await invoice_case("a PAN and a phone number are not tax IDs", "Plain Buyers Ltd\nPune\nPAN: AAACP1234C\nPhone: 9876543210", None, None, number="INV-7002")

    out = await extract_purchase_order(po_text("Al Noor Trading Technologies LLC\nDubai, UAE\nTRN: 100234567800003\nBuyer / Orderer"))
    po = out.purchase_order
    check("UAE PO: TRN", (po.tax_id_type.value if po.tax_id_type else None) == "TRN" and normalize_tax_id(po.tax_id_value) == "100234567800003", f"{po.tax_id_type} {po.tax_id_value!r} {po.tax_id_raw_label!r}")
    out = await extract_purchase_order(po_text("Brightwave Digital Ltd\nLondon, United Kingdom\nVAT No: GB284773109"))
    po = out.purchase_order
    check("UK PO: VAT", (po.tax_id_type.value if po.tax_id_type else None) == "VAT" and normalize_tax_id(po.tax_id_value) == "GB284773109", f"{po.tax_id_type} {po.tax_id_value!r}")

    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
