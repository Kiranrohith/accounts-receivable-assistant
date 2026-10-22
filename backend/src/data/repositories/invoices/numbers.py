"""Invoice-number normalization: the one rule every comparison of invoice numbers uses.

Uppercase, then remove everything that is not a letter or a digit:
"INV-1176", "INV1176", "inv 1176" and "Inv/1176" are all "INV1176". The prefix stays: a bare "1176"
is "1176", which never matches "INV1176" (an advice that quotes only a bare number does not name that
invoice). Debit notes follow the same rule ("DN-2001" is "DN2001").

(It lives beside the repository because the generated column it mirrors does.)
The database holds the same thing in invoices.invoice_number_norm, a generated column (INVOICE_NO_NORM_SQL in
the model), so a stored number can never be left without it. The two must agree; the pure rule here is
ASCII-only for that reason (accented letters are removed, never transliterated).
"""

import re

_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")


def normalize_invoice_no(number: str) -> str:
    return _NOT_ALNUM.sub("", number).upper()
