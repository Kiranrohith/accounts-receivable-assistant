"""The prompt rules for reading the CUSTOMER's tax identifier, shared by the invoice and the purchase-order prompts.

The rules are the same for both; only the two parties' roles differ (an invoice is issued by the seller to the buyer, an
order by the buyer to the seller)."""


def tax_id_rules(document: str, seller: str, buyer: str, where_buyer: str) -> str:
    """The tax-ID bullets of the PARTIES section. `document` is "An invoice" or "A purchase order"; `seller` / `buyer` name the
    two parties as that document calls them; `where_buyer` says where the buyer's identifier usually sits."""
    return f"""\
- {document} prints (at least) TWO tax identifiers (registration numbers): the {seller}'s and the {buyer}'s. They carry labels \
such as "GSTIN", "GSTIN/UIN", "GST No", "VAT Reg No", "VAT No", "TRN", "EIN" or "Tax ID", and they can sit anywhere: \
the letterhead, the party blocks, the footer, or an [embedded image] section. Search the WHOLE document, not only one \
block.
- Return the CUSTOMER's tax identifier: the registration number belonging to the {buyer}, NOT the {seller}'s and NOT our own \
company's. Return three things. tax_id_value: the identifier exactly as printed (spaces, hyphens and prefixes included). \
tax_id_raw_label: the literal label text printed with it. tax_id_type: GSTIN, VAT, TRN or EIN when the label is that \
kind of number, and OTHER when the label is anything else (ABN, UEN, BTW, Tax ID...). When there is no label, judge by \
the number's shape (15 characters in the GSTIN pattern, 15 digits for a TRN, 9 digits for an EIN, two letters then \
digits for a VAT number), and use OTHER when you cannot tell.
- Examples. "GSTIN: 29ABCDE1234F1Z5" gives tax_id_type GSTIN, tax_id_value "29ABCDE1234F1Z5", tax_id_raw_label "GSTIN". \
"VAT Reg No: GB 284 7731 09" gives tax_id_type VAT, tax_id_value "GB 284 7731 09", tax_id_raw_label "VAT Reg No". \
"TRN: 100123456700003" gives tax_id_type TRN, tax_id_value "100123456700003", tax_id_raw_label "TRN".
- Work by elimination. List every tax identifier you can see. The {seller}'s is the one in the letterhead / {seller} block (and \
the one that equals OUR GSTIN when that is given below). The customer's is the OTHER one, from {where_buyer}. When the \
document shows exactly two different identifiers and one is the {seller}'s, the other is the {buyer}'s: return it, even if \
its label is missing or unusual. Never return the {seller}'s identifier as the customer's.
- Return null for all three (tax_id_type, tax_id_value, tax_id_raw_label) only when the document shows no tax identifier \
other than the {seller}'s. Copy the identifier exactly as printed: never complete, correct or reformat it. These are NOT \
tax identifiers, so never return them in these fields, whatever the label: a PAN, CIN, TAN, IEC or Udyam number, a \
phone or fax number, a bank account or IFSC code, an invoice or PO number.
"""
