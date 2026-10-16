"""Document-type classification: invoice | po | other | unclear, with a confidence.

Runs once per upload, after parsing and before any field extraction. The routing decision is a
pure function of the verdict, so the four exits can be unit-tested without a model:

- invoice or po, confidence >= TRIAGE_HUMAN_THRESHOLD  -> that kind's extraction
- other, confidence >= threshold                       -> rejected outright (no extraction spend)
- anything under the threshold, or "unclear"           -> the doc-type review queue
"""

from dataclasses import dataclass
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage

from constants.constants import TRIAGE_HUMAN_THRESHOLD
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from prompts.documents.classification import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from schemas.documents.classification import DocClassification, DocumentClass

__all__ = ["ClassificationOutcome", "ExtractionError", "Route", "classify_document", "decide_route"]


class Route(str, Enum):
    INVOICE = "invoice"
    PO = "po"
    REJECT_OTHER = "reject_other"
    REVIEW = "review"


@dataclass(frozen=True)
class ClassificationOutcome:
    result: DocClassification
    model_used: str
    attempts: int


def decide_route(result: DocClassification) -> Route:
    if result.doc_class is DocumentClass.UNCLEAR or result.confidence < TRIAGE_HUMAN_THRESHOLD:
        return Route.REVIEW
    return {
        DocumentClass.INVOICE: Route.INVOICE,
        DocumentClass.PO: Route.PO,
        DocumentClass.OTHER: Route.REJECT_OTHER,
    }[result.doc_class]


async def classify_document(markdown: str) -> ClassificationOutcome:
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(USER_PROMPT_TEMPLATE.format(document=markdown)),
    ]
    result, model_used, attempts = await run_structured_extraction(DocClassification, messages)
    return ClassificationOutcome(result, model_used, attempts)
