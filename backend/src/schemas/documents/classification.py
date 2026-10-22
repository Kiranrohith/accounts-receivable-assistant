"""Structured output of the document-type classifier (runs before any field extraction)."""

from enum import Enum

from pydantic import BaseModel, Field


class DocumentClass(str, Enum):
    INVOICE = "invoice"
    PO = "po"
    PAYMENT = "payment"  # a remittance / payment advice: a customer telling us they paid
    DISPUTE = "dispute"  # a customer's complaint: they object to an invoice or to what we did
    OTHER = "other"
    UNCLEAR = "unclear"  # the model cannot cleanly separate invoice / po / payment / dispute / other


class DocClassification(BaseModel):
    doc_class: DocumentClass = Field(
        description="invoice, po, payment, dispute, other, or unclear when it cannot be told apart cleanly"
    )
    confidence: int = Field(
        ge=0, le=100, description="How sure you are of doc_class, 0-100"
    )
    also_payment: bool = Field(
        default=False,
        description="doc_class is dispute and the same document ALSO contains a payment advice",
    )
    also_dispute: bool = Field(
        default=False,
        description="doc_class is payment and the same document ALSO contains a complaint "
        "(for example a short payment with a stated reason)",
    )
    reason: str = Field(
        description="One short sentence: what the document is and what in it shows that"
    )
