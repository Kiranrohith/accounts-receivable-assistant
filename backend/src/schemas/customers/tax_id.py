"""The customer's tax ID as it travels through the schemas: what kind of number, its value as printed, and the label it
was printed under. Used by the invoice and purchase-order extraction and review edits, and by the customer screens."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaxIdType(str, Enum):
    """The same members as the database enum data.models.postgres.enums.TaxIdType (a test keeps them equal)."""

    GSTIN = "GSTIN"
    VAT = "VAT"
    TRN = "TRN"
    EIN = "EIN"
    OTHER = "OTHER"  # any label that is not one of the others; stored and shown, never used to find a customer


class TaxIdFields(BaseModel):
    """The three things the reader returns about the CUSTOMER's tax identifier (not ours)."""

    tax_id_type: TaxIdType | None = Field(
        default=None, description="GSTIN, VAT, TRN, EIN, or OTHER when the printed label is none of these; null if no id"
    )
    tax_id_value: str | None = Field(default=None, description="The customer's tax identifier exactly as printed")
    tax_id_raw_label: str | None = Field(default=None, description="The literal label it was printed under, e.g. 'VAT Reg No'")


def from_legacy(data: Any) -> Any:
    """Extractions saved before tax IDs were generalized carry `customer_gstin` (and a confidence of that name): read
    them as a GSTIN, so a document still waiting for review keeps the id it was read with."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    old = data.pop("customer_gstin", None)
    if old and not data.get("tax_id_value"):
        data["tax_id_value"] = old
        data["tax_id_type"] = data.get("tax_id_type") or TaxIdType.GSTIN.value
    confidence = data.get("confidence")
    if isinstance(confidence, dict) and "customer_gstin" in confidence:
        confidence = dict(confidence)
        old_confidence = confidence.pop("customer_gstin")
        confidence.setdefault("tax_id", old_confidence)
        data["confidence"] = confidence
    data.pop("customer_gstin_from_customer", None)
    return data
