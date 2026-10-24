"""The customer's GSTIN, when the reader did not find it.

An invoice or purchase order prints two GSTINs: ours and the customer's. The reader (a language model) sometimes returns
ours, or nothing, for the customer's. Two safety nets, both exact (nothing is guessed):

- recovery from the text: the document's own text is searched for GSTINs; once ours is set aside, a single remaining
  one is the customer's. Several remaining ones are ambiguous, so none is chosen.
- fill from the matched customer: when the customer is identified by other means (its name, an alias, a person's
  choice) and the GSTIN is still empty, the customer's own GSTIN from the master data is put in the extraction.
"""

import re
from typing import TypeVar

from pydantic import BaseModel

from constants.constants import GSTIN_PATTERN

_VALID = re.compile(GSTIN_PATTERN)
# the same shape anywhere in a text, not glued to other letters or digits
_IN_TEXT = re.compile(r"(?<![A-Z0-9])" + GSTIN_PATTERN.removeprefix("^").removesuffix("$") + r"(?![A-Z0-9])")

RECOVERED_CONFIDENCE = 90  # the value is printed in the document, found by an exact pattern

M = TypeVar("M", bound=BaseModel)


def clean(value: str | None) -> str | None:
    """A GSTIN as the system keeps it: no spaces, upper case; None when nothing is there."""
    return "".join(value.split()).upper() if value and value.strip() else None


def gstins_in_text(text: str) -> list[str]:
    """Every distinct valid-looking GSTIN printed in the text, in order of appearance."""
    return list(dict.fromkeys(_IN_TEXT.findall((text or "").upper())))


def recover(extracted: str | None, text: str, our_gstin: str | None) -> str | None:
    """The GSTIN to use as the customer's when the reader's answer cannot be used (empty, our own, or not a GSTIN
    at all): the one other GSTIN the document prints. None when there is no such single one."""
    given = clean(extracted)
    ours = clean(our_gstin)
    if given and _VALID.match(given) and given != ours:
        return given
    others = [g for g in gstins_in_text(text) if g != ours]
    return others[0] if len(others) == 1 else None


def with_gstin_from_text(extracted: M, text: str, our_gstin: str | None) -> M:
    """The extraction with the customer's GSTIN recovered from the text when the reader missed it (see `recover`).
    Returns the same object when nothing changes."""
    found = recover(extracted.customer_gstin, text, our_gstin)  # type: ignore[attr-defined]
    if found is None or found == clean(extracted.customer_gstin):  # type: ignore[attr-defined]
        return extracted
    confidence = extracted.confidence.model_copy(update={"customer_gstin": RECOVERED_CONFIDENCE})  # type: ignore[attr-defined]
    return extracted.model_copy(update={"customer_gstin": found, "confidence": confidence})


def with_customer_gstin(extracted: M, customer_gstin: str | None) -> M:
    """The extraction with the matched customer's own GSTIN where the extraction has none."""
    if clean(extracted.customer_gstin) or not customer_gstin:  # type: ignore[attr-defined]
        return extracted
    return extracted.model_copy(update={"customer_gstin": customer_gstin})
