"""A line item's quantity as a model reads it off a document: a number, or a number with its unit ("3 months", "53 screens")."""

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator

# a number, then a unit made of letters only: "3 months", "1,200 nos", "8.5 days". A unit with digits of its own ("3 x 4") is
# not one: it stays as it is and fails the check, rather than being read as 3.
_NUMBER_WITH_UNIT = re.compile(r"^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*[A-Za-z][A-Za-z .\-/]*$")


def read_quantity(value: Any) -> Any:  # noqa: ANN401
    """The number in a quantity written with a unit; None for text with no digit in it ("monthly"); anything else untouched."""
    if not isinstance(value, str):
        return value
    found = _NUMBER_WITH_UNIT.match(value)
    if found:
        return found.group(1).replace(",", "")
    if not any(ch.isdigit() for ch in value):
        return None
    return value


Quantity = Annotated[Decimal | None, BeforeValidator(read_quantity)]
