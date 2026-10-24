"""Whose complaint is it, and which invoices is it about?

Same evidence and the same never-guess rule as the payer waterfall (core.services.payments.identity,
reused for the verdict): the invoices it quotes, the sender's e-mail, the customer's GSTIN and name.
Conflicting or ambiguous evidence gives NO customer, and the dispute waits in the classify queue
with the reasons beside it. A deactivated customer can still complain, so that alone is not an issue.

The invoices returned are only the quoted ones that exist and belong to the identified customer.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.payments.identity import IdentityFacts, decide_identity
from data.models.postgres import Customer, Invoice
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.invoices.numbers import normalize_invoice_no
from schemas.disputes.extraction import ExtractedDispute


@dataclass(frozen=True)
class DisputeIdentity:
    customer: Customer | None
    invoices: list[Invoice] = field(default_factory=list)  # the quoted ones that are this customer's
    issues: list[str] = field(default_factory=list)  # why no customer, or what else is doubtful
    unknown_numbers: list[str] = field(default_factory=list)  # quoted, but not an invoice we have
    foreign_numbers: list[str] = field(default_factory=list)  # quoted, but another customer's


def _clean(values: list[str | None]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value and value.strip():
            seen.setdefault(value.strip(), None)
    return list(seen)


async def gather_facts(
    db: AsyncSession, extracted: ExtractedDispute, *, our_company_name: str | None = None
) -> IdentityFacts:
    numbers = _clean(list(extracted.invoice_numbers))
    invoices = await invoices_repo.get_by_numbers(db, numbers)
    known = {normalize_invoice_no(i.invoice_number) for i in invoices}
    owners = [
        c for c in [await customers_repo.get_by_id(db, cid) for cid in {i.customer_id for i in invoices}] if c
    ]
    email_matches = (
        await customers_repo.get_by_email_or_alias(db, extracted.from_email)
        if extracted.from_email and extracted.from_email.strip()
        else []
    )
    name = " ".join(extracted.customer_name.split()) if extracted.customer_name else None
    ours = bool(
        name and our_company_name and name.lower() == " ".join(our_company_name.split()).lower()
    )
    matches: dict = {}
    if name and not ours:
        exact = await customers_repo.get_by_name(db, name)
        for customer in [*([exact] if exact else []), *await customers_repo.get_by_alias_name(db, name)]:
            matches[customer.id] = customer
    if extracted.customer_gstin and extracted.customer_gstin.strip():
        by_gstin = await customers_repo.get_by_tax_id(db, extracted.customer_gstin)  # a complaint's id is read as a GSTIN only
        if by_gstin is not None:
            matches[by_gstin.id] = by_gstin
    return IdentityFacts(
        quoted_invoices=invoices,
        unknown_numbers=[n for n in numbers if normalize_invoice_no(n) not in known],
        quoted_owners=owners,
        email_matches=email_matches,
        name_matches=list(matches.values()),
        payer_name_is_ours=ours,
    )


def decide(facts: IdentityFacts) -> DisputeIdentity:
    verdict = decide_identity(facts)
    # a deactivated customer still gets heard
    blocking = [i for i in verdict.issues if "deactivated" not in i]
    if verdict.customer is None or blocking:
        what = [i.replace("payer", "customer").replace("who paid", "who this is") for i in blocking]
        return DisputeIdentity(None, [], what, list(facts.unknown_numbers))
    customer = verdict.customer
    mine = [i for i in facts.quoted_invoices if i.customer_id == customer.id]
    foreign = [i.invoice_number for i in facts.quoted_invoices if i.customer_id != customer.id]
    return DisputeIdentity(customer, mine, [], list(facts.unknown_numbers), foreign)


async def identify_complainant(
    db: AsyncSession, extracted: ExtractedDispute, *, our_company_name: str | None = None
) -> DisputeIdentity:
    return decide(await gather_facts(db, extracted, our_company_name=our_company_name))
