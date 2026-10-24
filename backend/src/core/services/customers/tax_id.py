"""The customer's tax ID (a GSTIN, a VAT number, a TRN, an EIN...) on a document, and what to do when it is missing or unusable.

An invoice or purchase order prints two registration numbers: ours and the customer's. The reader (a language model) returns
the customer's as three things: its type, its value as printed and the label it was printed under. Everything here is exact;
nothing is guessed:

- the shape check (`check_shape`): a value must have the shape of the type it claims. One that does not is dropped (set to
  null with a low confidence) and the reason is returned for the step log; the document is never rejected for it, because
  an id is a shortcut to the customer, not a requirement (the name and the aliases still find the customer).
- recovery from the text (`with_tax_id_from_text`): when the reader returned nothing usable, or returned our own GSTIN, the
  document's one other id that passes a shape check is the customer's. Several others are ambiguous, so none is chosen.
- fill from the matched customer (`with_customer_tax_id`): when the customer is identified by other means (its name, an
  alias, a person's choice) and the id is still empty, the customer's own id and type from the master data are put in the
  extraction.

Our own id is always a GSTIN (settings OUR_GSTIN). A type OTHER is kept as printed but is never usable to find a customer.
"""

import re
from typing import TypeVar

from pydantic import BaseModel

from constants.constants import GSTIN_PATTERN, TAX_ID_SHAPES
from data.repositories.customers.tax_ids import normalize_tax_id
from schemas.customers.tax_id import TaxIdType

_SHAPES = {name: re.compile(pattern) for name, pattern in TAX_ID_SHAPES.items()}
SHAPE_HELP = {
    "GSTIN": "15 characters, e.g. 29ABCDE1234F1Z5",
    "VAT": "two letters then 2 to 13 letters or digits, e.g. GB284773109",
    "EIN": "9 digits, e.g. 943456789",
    "TRN": "15 digits, e.g. 100123456700003",
}

# Found in the text: a GSTIN has a shape of its own; the other kinds are only taken from beside their label, because a bare
# 9 or 15 digit number or a letters-and-digits word could be anything.
_GSTIN_IN_TEXT = re.compile(r"(?<![A-Z0-9])" + GSTIN_PATTERN.removeprefix("^").removesuffix("$") + r"(?![A-Z0-9])")
_AFTER = r"\s*(?:REG(?:ISTRATION)?\.?\s*)?(?:NO\.?|NUMBER|NR\.?|ID)?\s*[:#]?\s*"  # the text is upper-cased first
_VAT_VALUE = r"([A-Z]{2}(?:[ .\-/]?[A-Z0-9]{2,4}(?:[ .\-/]\d{2,9}){1,4}|[ .\-/]?[A-Z0-9]{2,13}))(?![A-Z0-9])"
_LABELLED = {
    TaxIdType.VAT.value: re.compile(r"(?<![A-Z0-9])(?:VAT|UST-?IDNR|TVA|BTW)" + _AFTER + _VAT_VALUE),
    TaxIdType.TRN.value: re.compile(r"(?<![A-Z0-9])TRN" + _AFTER + r"(\d{15})(?![0-9])"),
    TaxIdType.EIN.value: re.compile(r"(?<![A-Z0-9])EIN" + _AFTER + r"(\d{2}-?\d{7})(?![0-9])"),
}

# Labels of numbers that are not a customer's tax registration, whatever the reader made of them (a PAN is an Indian
# entity ID, a CIN a company ID...): such a value is left out, so it cannot stand in for the id that is really needed.
_NOT_A_TAX_ID_LABEL = re.compile(r"(?<![A-Z0-9])(?:PAN|CIN|TAN|IEC|UDYAM|MSME|IFSC|PHONE|TEL|MOBILE|FAX|A/C|ACCOUNT)(?![A-Z0-9])")

RECOVERED_CONFIDENCE = 90  # the value is printed in the document, found by an exact pattern
REJECTED_CONFIDENCE = 10  # the reader's value failed its shape: kept in the log only

M = TypeVar("M", bound=BaseModel)


def _type_name(tax_id_type: object) -> str | None:
    return getattr(tax_id_type, "value", tax_id_type) or None


def has_shape(tax_id_type: object, normalized: str) -> bool:
    """Does the normalized value have the shape of its type? A type with no shape (OTHER, or none) always does."""
    pattern = _SHAPES.get(_type_name(tax_id_type) or "")
    return pattern is None or bool(pattern.match(normalized))


def usable_for_matching(tax_id_type: object, value: str | None) -> bool:
    """Only an id of a type with a shape, that has it, may be used to find a customer."""
    normalized = normalize_tax_id(value)
    name = _type_name(tax_id_type)
    return bool(normalized) and name in _SHAPES and has_shape(name, normalized)


def infer_type(value: str | None) -> str | None:
    """The type a value's shape says it is (the shapes never overlap), for an id typed with no type."""
    normalized = normalize_tax_id(value)
    return next((name for name, pattern in _SHAPES.items() if pattern.match(normalized)), None) if normalized else None


def check_shape(extracted: M) -> tuple[M, str | None]:
    """The shape check on what the reader returned. Returns the extraction (the tax ID emptied, with a low confidence,
    when its value does not have the shape of its type) and the reason for the step log, or None when nothing changed."""
    value = getattr(extracted, "tax_id_value", None)
    if value is None or not value.strip():
        return extracted, None
    name = _type_name(extracted.tax_id_type)  # type: ignore[attr-defined]
    normalized = normalize_tax_id(value)
    label = (getattr(extracted, "tax_id_raw_label", None) or "").upper()
    if normalized and _NOT_A_TAX_ID_LABEL.search(label):
        note = f"The number '{value.strip()}' is labelled '{label}', which is not a tax registration number; it was left out"
        confidence = extracted.confidence.model_copy(update={"tax_id": REJECTED_CONFIDENCE})  # type: ignore[attr-defined]
        return extracted.model_copy(
            update={"tax_id_value": None, "tax_id_type": None, "tax_id_raw_label": None, "confidence": confidence}
        ), note
    if not normalized or has_shape(name, normalized):
        return extracted, None
    note = (
        f"The tax ID '{value.strip()}' is not usable: it does not have the shape of a {name} "
        f"({SHAPE_HELP[name]}); it was left out"  # type: ignore[index]
    )
    confidence = extracted.confidence.model_copy(update={"tax_id": REJECTED_CONFIDENCE})  # type: ignore[attr-defined]
    return extracted.model_copy(update={"tax_id_value": None, "confidence": confidence}), note


def _candidates_in_text(text: str, our_gstin: str | None) -> list[tuple[str, str, str]]:
    """Every distinct (type, value as printed, label) in the text that passes its shape check, ours set aside."""
    upper = (text or "").upper()
    ours = normalize_tax_id(our_gstin)
    found: dict[str, tuple[str, str, str]] = {}
    for match in _GSTIN_IN_TEXT.finditer(upper):
        if match.group() != ours:
            found.setdefault(match.group(), ("GSTIN", match.group(), "GSTIN"))
    for name, pattern in _LABELLED.items():
        for match in pattern.finditer(upper):
            printed = match.group(1).strip()
            normalized = normalize_tax_id(printed)
            if normalized != ours and has_shape(name, normalized):
                found.setdefault(normalized, (name, printed, name))
    return list(found.values())


def recover(extracted: M, text: str, our_gstin: str | None) -> tuple[str, str, str] | None:
    """(type, value, label) to use as the customer's id when the reader's answer cannot be used (empty, our own GSTIN, or
    of the wrong shape): the one other id the document prints. None when the reader's id stands or there is no single one."""
    value = getattr(extracted, "tax_id_value", None)
    normalized = normalize_tax_id(value)
    ours = normalize_tax_id(our_gstin)
    if normalized and normalized != ours and has_shape(_type_name(extracted.tax_id_type), normalized):  # type: ignore[attr-defined]
        return None
    others = _candidates_in_text(text, our_gstin)
    return others[0] if len(others) == 1 else None


def with_tax_id_from_text(extracted: M, text: str, our_gstin: str | None) -> M:
    """The extraction with the customer's tax ID recovered from the text when the reader missed it (see `recover`).
    Returns the same object when nothing changes."""
    found = recover(extracted, text, our_gstin)
    if found is None:
        return extracted
    name, value, label = found
    confidence = extracted.confidence.model_copy(update={"tax_id": RECOVERED_CONFIDENCE})  # type: ignore[attr-defined]
    return extracted.model_copy(
        update={"tax_id_type": TaxIdType(name), "tax_id_value": value, "tax_id_raw_label": label, "confidence": confidence}
    )


def read_tax_id(extracted: M, text: str, our_gstin: str | None) -> tuple[M, str | None]:
    """What the reader returned, made usable: the shape check, then the recovery from the text. Returns the extraction and
    the shape-check reason (for the step log), if any."""
    checked, note = check_shape(extracted)
    return with_tax_id_from_text(checked, text, our_gstin), note


def with_customer_tax_id(extracted: M, tax_id: str | None, tax_id_type: object) -> M:
    """The extraction with the matched customer's own tax ID and type where the extraction has none."""
    if normalize_tax_id(getattr(extracted, "tax_id_value", None)) or not tax_id:
        return extracted
    name = _type_name(tax_id_type)
    return extracted.model_copy(
        update={"tax_id_value": tax_id, "tax_id_type": TaxIdType(name) if name else None, "tax_id_raw_label": None}
    )
