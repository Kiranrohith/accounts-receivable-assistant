"""LLM field extraction for purchase orders: same model ladder as invoices, PO schema/prompt."""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import get_settings
from core.services.customers.tax_id import read_tax_id
from core.services.invoices.extraction import ExtractionError, run_structured_extraction
from prompts.purchase_orders.extraction import USER_PROMPT_TEMPLATE, build_system_prompt
from schemas.purchase_orders.extraction import ExtractedPurchaseOrder

__all__ = ["ExtractionError", "PurchaseOrderOutcome", "extract_purchase_order"]


@dataclass(frozen=True)
class PurchaseOrderOutcome:
    purchase_order: ExtractedPurchaseOrder
    model_used: str
    attempts: int
    tax_id_note: str | None = None  # why the reader's tax ID was left out (it failed the shape of its type), if it was


async def extract_purchase_order(markdown: str) -> PurchaseOrderOutcome:
    settings = get_settings()
    messages = [
        SystemMessage(build_system_prompt(settings.our_company_name, settings.our_gstin)),
        HumanMessage(USER_PROMPT_TEMPLATE.format(document=markdown)),
    ]
    po, model_used, attempts = await run_structured_extraction(ExtractedPurchaseOrder, messages)
    # the tax ID must have the shape of its type; when the reader gave none, our own GSTIN or a bad one, take the document's other id
    po, tax_id_note = read_tax_id(po, markdown, settings.our_gstin)
    return PurchaseOrderOutcome(po, model_used, attempts, tax_id_note)
