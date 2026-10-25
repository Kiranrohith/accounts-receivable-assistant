"""The payment's own reference (UTR, NEFT / RTGS / IMPS reference, cheque or transaction number). Pure: no I/O.

It is the only thing taken as proof that money was sent: validation sends an advice without a usable one to a
person, and the duplicate check keys on it.
"""

from constants.constants import JUNK_REFERENCES


def reference_key(reference: str | None) -> str | None:
    """The normalised reference used to spot a repeat, or None when it cannot identify a payment:
    empty, a bare bank-rail name (NEFT, IMPS...), or nothing but punctuation."""
    if not reference:
        return None
    key = reference.strip().upper()
    if not key or key in JUNK_REFERENCES or not any(ch.isalnum() for ch in key):
        return None
    return key
