"""The comparison key for line descriptions: casefolded, punctuation stripped, spaces collapsed.

Used on both sides of the fulfilment check, so "Laptop (Dell 5440)" and "laptop dell 5440"
are the same item. Matching is normalised-exact on purpose: no fuzzy or semantic matching.
"""

import re

_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)


def normalize_description(text: str | None) -> str:
    return _SEPARATORS.sub(" ", (text or "").casefold()).strip()
