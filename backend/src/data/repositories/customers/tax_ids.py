"""Tax-ID normalization: the one rule every comparison and every save of a customer's tax ID uses.

Uppercase, then remove spaces, dots, hyphens and slashes: "GB 284 7731 09", "gb2847731 09" and "GB-284.7731/09" are all
"GB284773109"; "94-3456789" is "943456789". It sits beside the customers.tax_id column it serves, the way the invoice-number
rule sits beside invoice_number_norm. Both sides of every comparison go through it (what is stored, what a document
prints, what a person types), and what is stored is the normalized form; the value as printed stays in the document's
own extraction. Nothing fuzzy follows from it: a normalized id either equals another or it does not.
"""

import re

_SEPARATORS = re.compile(r"[\s.\-/]+")


def normalize_tax_id(value: str | None) -> str:
    """The normalized tax ID; "" when there is nothing (None, blank, or only separators)."""
    return _SEPARATORS.sub("", value or "").upper()
