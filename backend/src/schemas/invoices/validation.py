from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    FAIL = "fail"  # cannot become an invoice: goes to the rejected list
    REVIEW = "review"  # a human should look at the document before it is accepted


class Outcome(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class ValidationIssue(BaseModel):
    code: str
    field: str | None
    severity: Severity
    message: str  # plain English, shown to the user


class ValidationResult(BaseModel):
    outcome: Outcome
    issues: list[ValidationIssue]
