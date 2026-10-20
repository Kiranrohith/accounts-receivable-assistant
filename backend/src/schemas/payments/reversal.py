import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ReversePayment(BaseModel):
    reason: Reason
    after: Literal["rematch", "review"] = Field(
        default="rematch",
        description="What happens to the payment once it is reversed: `rematch` runs the matcher "
        "again straight away (use it when the data was fixed: an alias, an invoice, a hold); "
        "`review` puts it in the review queue for a person (use it when the automatic match was "
        "wrong and would simply be repeated)",
    )


class ReversedAllocation(BaseModel):
    invoice_number: str
    amount: Decimal
    funded_by: Literal["payment", "credit_note"]
    note_number: str | None = None  # the note, when a credit spend was undone
    invoice_status_after: str


class AllocationResult(BaseModel):
    invoice_number: str
    allocated: Decimal
    tolerance: str
    invoice_status: str


class ReversalResult(BaseModel):
    payment_id: uuid.UUID
    state: str  # where the payment is now
    message: str
    reversed: list[ReversedAllocation]
    voided_notes: list[str]  # credit notes born from this payment, unspent, now Voided
    credit_restored: list[str]  # notes whose spent amount was given back (a spend its advice caused)
    rematch_reasons: list[str] = []  # when `rematch` ended in review: why
    new_allocations: list[AllocationResult] = []  # when `rematch` applied it again
