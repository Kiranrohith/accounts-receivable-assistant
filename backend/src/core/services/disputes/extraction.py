"""LLM field extraction for customer complaints: same model ladder, dispute schema/prompt."""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import get_settings
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from prompts.disputes.extraction import USER_PROMPT_TEMPLATE, build_system_prompt
from schemas.disputes.extraction import ExtractedDispute

__all__ = ["DisputeOutcome", "ExtractionError", "extract_dispute"]


@dataclass(frozen=True)
class DisputeOutcome:
    dispute: ExtractedDispute
    model_used: str
    attempts: int


async def extract_dispute(markdown: str) -> DisputeOutcome:
    settings = get_settings()
    messages = [
        SystemMessage(build_system_prompt(settings.our_company_name, settings.our_gstin)),
        HumanMessage(USER_PROMPT_TEMPLATE.format(document=markdown)),
    ]
    dispute, model_used, attempts = await run_structured_extraction(ExtractedDispute, messages)
    return DisputeOutcome(dispute, model_used, attempts)
