"""Purchase-order-number normalization: the one rule every comparison of PO numbers uses.

Uppercase, then remove everything that is not a letter or a digit: "PO-123", "PO123", "po 123" and "P.O./123" are all
"PO123". The prefix stays ("123" never matches "PO123"). The database holds the same thing in
purchase_orders.po_number_norm and invoices.po_number_norm, generated columns (PO_NO_NORM_SQL in the model), so a stored
number can never be left without it; the rule here is ASCII-only for the same reason (accented letters are removed,
never transliterated). A number with no letter or digit at all normalizes to "" and names nothing.
"""

import re

_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")


def normalize_po_no(number: str) -> str:
    return _NOT_ALNUM.sub("", number).upper()
