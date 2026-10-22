"""Structured output the LLM must return for one clarification e-mail."""

from pydantic import BaseModel, Field


class ClarificationDraft(BaseModel):
    subject: str = Field(description="A short subject line, without any reference number")
    body: str = Field(description="The whole e-mail, plain text, from greeting to sign-off")
    confidence: int = Field(
        ge=0,
        le=100,
        description="How sure you are that this e-mail is accurate, uses only the facts given, "
        "and could be sent as it is without anyone editing it",
    )
