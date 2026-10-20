"""LLM field extraction for payment advices: same model ladder as invoices, payment schema/prompt."""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import get_settings
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from prompts.payments.extraction import USER_PROMPT_TEMPLATE, build_system_prompt
from schemas.payments.extraction import ExtractedPayment

__all__ = ["ExtractionError", "PaymentOutcome", "extract_payment"]


@dataclass(frozen=True)
class PaymentOutcome:
    payment: ExtractedPayment
    model_used: str
    attempts: int


async def extract_payment(markdown: str) -> PaymentOutcome:
    settings = get_settings()
    messages = [
        SystemMessage(build_system_prompt(settings.our_company_name, settings.our_gstin)),
        HumanMessage(USER_PROMPT_TEMPLATE.format(document=markdown)),
    ]
    payment, model_used, attempts = await run_structured_extraction(ExtractedPayment, messages)
    return PaymentOutcome(payment, model_used, attempts)
