"""LLM field extraction (invoices; the same ladder serves purchase orders).

Parsed markdown in, a validated schema object out. Ladder:

1. Groq key 1: initial try + 2 retries (3s, 9s +/-30%).
2. Key 1 hit a RATE LIMIT (429) -> Groq key 2 straight away (no wait; a spent quota
   does not recover in seconds), same retry budget.
3. Key 1 failed for any other reason -> skip key 2, go to the Gemini fallback.
4. Key 2 failed for any reason -> Gemini fallback (same retry budget).
5. Everything failed -> ExtractionError (the job runner dead-letters; re-runnable).
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from config.settings import get_settings
from constants.constants import BACKOFF_JITTER, LLM_RETRY_DELAYS_SECONDS
from core.services.customers.tax_id import read_tax_id
from core.services.invoices.tax import with_tax_total
from prompts.invoices.extraction import USER_PROMPT_TEMPLATE, build_system_prompt
from schemas.invoices.extraction import ExtractedInvoice

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Every model and retry failed to produce a valid ExtractedInvoice."""


@dataclass(frozen=True)
class ExtractionOutcome:
    invoice: ExtractedInvoice
    model_used: str
    attempts: int
    tax_id_note: str | None = None  # why the reader's tax ID was left out (it failed the shape of its type), if it was


@dataclass(frozen=True)
class _Stage:
    label: str
    model: BaseChatModel
    method: str | None  # structured-output method; None = provider default
    on_groq_rate_limit_only: bool = False  # only entered after the previous stage hit 429


@lru_cache(maxsize=1)
def _stages() -> tuple[_Stage, ...]:
    
    s = get_settings()

    def groq(key: str | None) -> BaseChatModel:
        return init_chat_model(
            s.primary_llm_model, model_provider="groq", api_key=key, temperature=0
        )

    stages = []
    if s.groq_key_1:
        stages.append(_Stage(f"{s.primary_llm_model} [key1]", groq(s.groq_key_1), "json_schema"))
    if s.groq_key_2:
        stages.append(
            _Stage(
                f"{s.primary_llm_model} [key2]",
                groq(s.groq_key_2),
                "json_schema",
                on_groq_rate_limit_only=True,
            )
        )
    if s.gemini_key:
        stages.append(
            _Stage(
                s.fallback_llm_model,
                init_chat_model(
                    s.fallback_llm_model,
                    model_provider="google_genai",
                    api_key=s.gemini_key,
                    temperature=0,
                ),
                None,
            )
        )
    return tuple(stages)


def _jittered(seconds: float) -> float:
    return seconds * (1 + random.uniform(-float(BACKOFF_JITTER), float(BACKOFF_JITTER)))


def _is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429


async def run_structured_extraction[T: BaseModel](
    schema: type[T], messages: list[BaseMessage]
) -> tuple[T, str, int]:
    """Run the model ladder for any output schema; returns (result, model label, attempts)."""
    stages = _stages()
    if not stages:
        raise ExtractionError("No LLM API keys configured (GROQ_KEY_1/GROQ_KEY_2/GEMINI_KEY)")

    delays = (0, *LLM_RETRY_DELAYS_SECONDS)
    attempts = 0
    last_error: Exception | None = None
    prev_hit_rate_limit = False

    for stage in stages:
        if stage.on_groq_rate_limit_only and not prev_hit_rate_limit:
            continue
        structured = (
            stage.model.with_structured_output(schema, method=stage.method)
            if stage.method
            else stage.model.with_structured_output(schema)
        )
        prev_hit_rate_limit = False
        for delay in delays:
            if delay:
                await asyncio.sleep(_jittered(delay))
            attempts += 1
            try:
                result = await structured.ainvoke(messages)
            except Exception as exc:
                last_error = exc
                if _is_rate_limited(exc):
                    logger.warning("%s rate limited; moving on without retrying", stage.label)
                    prev_hit_rate_limit = True
                    break
                logger.warning("attempt %d on %s failed: %s", attempts, stage.label, exc)
                continue
            if isinstance(result, schema):
                return result, stage.label, attempts
            last_error = ValueError(f"{stage.label} returned no structured output")

    raise ExtractionError(
        f"Extraction failed after {attempts} attempts: {last_error}"
    ) from last_error


async def extract_invoice(markdown: str) -> ExtractionOutcome:
    settings = get_settings()
    messages = [
        SystemMessage(build_system_prompt(settings.our_company_name, settings.our_gstin)),
        HumanMessage(USER_PROMPT_TEMPLATE.format(document=markdown)),
    ]
    invoice, model_used, attempts = await run_structured_extraction(ExtractedInvoice, messages)
    # the tax ID must have the shape of its type; when the reader gave none, our own GSTIN or a bad one, take the document's other id
    invoice, tax_id_note = read_tax_id(invoice, markdown, settings.our_gstin)
    # two or more itemised taxes are often printed without a combined total: add them up
    invoice = with_tax_total(invoice)
    return ExtractionOutcome(invoice, model_used, attempts, tax_id_note)
