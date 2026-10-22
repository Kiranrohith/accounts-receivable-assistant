"""Who paid? The identity waterfall for a payment (Flow 2, step 4).

  L1  an invoice number the advice quotes -> that invoice's customer
  L2  the sender's e-mail -> the customer with that e-mail (or a confirmed e-mail alias)
  L3  the payer's name   -> the customer with that name (or a confirmed name alias)
  L4  nothing identified -> a person decides. Evidence that disagrees or is ambiguous also goes to a
      person, but keeps the layer it was found at (L1/L2/L3) with no customer, so the review shows where
      it came from.

Anything doubtful is returned as an issue and the payment goes to review; this never guesses:
quoted invoices owned by different customers (C1), the invoice's owner and the sender or payer
name naming different customers (C1), a shared e-mail address or alias, a deactivated customer.

`decide_identity` is pure (facts in, verdict out); `gather_facts` does the lookups.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, Invoice
from data.models.postgres.enums import MatchedLayer
from data.repositories.customers import customers_repo
from data.repositories.invoices import invoices_repo
from data.repositories.invoices.numbers import normalize_invoice_no
from schemas.payments.extraction import ExtractedPayment


@dataclass(frozen=True)
class IdentityFacts:
    quoted_invoices: list[Invoice] = field(default_factory=list)  # quoted numbers we know
    unknown_numbers: list[str] = field(default_factory=list)  # quoted numbers we do not have
    quoted_owners: list[Customer] = field(default_factory=list)  # their distinct customers
    email_matches: list[Customer] = field(default_factory=list)
    name_matches: list[Customer] = field(default_factory=list)
    payer_name_is_ours: bool = False


@dataclass(frozen=True)
class IdentityResult:
    customer: Customer | None
    layer: MatchedLayer
    issues: list[str] = field(default_factory=list)
    unknown_numbers: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        """A customer was found and nothing about it is doubtful."""
        return self.customer is not None and not self.issues


def _names(customers: list[Customer]) -> str:
    return ", ".join(sorted(c.customer_name for c in customers))


def decide_identity(facts: IdentityFacts) -> IdentityResult:
    issues: list[str] = []
    unknown = facts.unknown_numbers
    owners = facts.quoted_owners
    emails = facts.email_matches
    names = facts.name_matches

    def fail(message: str, layer: MatchedLayer = MatchedLayer.L4) -> IdentityResult:
        """No customer is named. `layer` is the layer whose evidence was reached and disagreed or was
        ambiguous (L1/L2/L3), so a reviewer sees where it came from; L4 only when nothing identified."""
        return IdentityResult(None, layer, [*issues, message], unknown)

    def other_evidence_disagrees(customer: Customer) -> str | None:
        """The sender's e-mail or the payer's name names a different customer than `customer`."""
        for label, found in (("sender e-mail", emails), ("payer name", names)):
            if found and customer.id not in {c.id for c in found}:
                return (
                    f"Identity conflict: the quoted invoice(s) belong to '{customer.customer_name}' "
                    f"but the {label} belongs to '{_names(found)}'"
                )
        return None

    # --- L1: quoted invoice numbers ---
    if len(owners) > 1:
        return fail(
            f"The invoices quoted belong to different customers ({_names(owners)}); "
            "please say who paid",
            MatchedLayer.L1,
        )
    if len(owners) == 1:
        customer, layer = owners[0], MatchedLayer.L1
        conflict = other_evidence_disagrees(customer)
        if conflict:
            return fail(conflict, MatchedLayer.L1)
    # --- L2: sender e-mail ---
    elif emails:
        if len(emails) > 1:
            return fail(
                f"The sender's e-mail address belongs to several customers ({_names(emails)}); "
                "please say who paid",
                MatchedLayer.L2,
            )
        customer, layer = emails[0], MatchedLayer.L2
        conflict = None
        if names and customer.id not in {c.id for c in names}:
            conflict = (
                f"Identity conflict: the sender e-mail belongs to '{customer.customer_name}' "
                f"but the payer name belongs to '{_names(names)}'"
            )
        if conflict:
            return fail(conflict, MatchedLayer.L2)
    # --- L3: payer name ---
    elif names:
        if len(names) > 1:
            return fail(
                f"The payer name matches several customers ({_names(names)}); please say who paid",
                MatchedLayer.L3,
            )
        customer, layer = names[0], MatchedLayer.L3
    else:
        what = "its quoted invoices, sender e-mail and payer name"
        if facts.payer_name_is_ours:
            what += " (the payer read was our own company)"
        return fail(f"The customer could not be identified from {what}")

    if not customer.is_active:
        issues.append(f"Customer '{customer.customer_name}' is deactivated")
    return IdentityResult(customer, layer, issues, unknown)


def _clean(values: list[str | None]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value and value.strip():
            seen.setdefault(value.strip(), None)
    return list(seen)


async def gather_facts(
    db: AsyncSession,
    payment: ExtractedPayment,
    *,
    our_company_name: str | None = None,
) -> IdentityFacts:
    numbers = _clean([i.invoice_number for i in payment.invoices])
    invoices = await invoices_repo.get_by_numbers(db, numbers)
    known = {normalize_invoice_no(i.invoice_number) for i in invoices}
    owner_ids = {i.customer_id for i in invoices}
    owners = [c for c in [await customers_repo.get_by_id(db, cid) for cid in owner_ids] if c]

    email_matches = (
        await customers_repo.get_by_email_or_alias(db, payment.from_email)
        if payment.from_email and payment.from_email.strip()
        else []
    )

    payer = " ".join(payment.payer_name.split()) if payment.payer_name else None
    ours = bool(
        payer and our_company_name and payer.lower() == " ".join(our_company_name.split()).lower()
    )
    name_matches: list[Customer] = []
    if payer and not ours:
        exact = await customers_repo.get_by_name(db, payer)
        aliased = await customers_repo.get_by_alias_name(db, payer)
        by_id = {c.id: c for c in [*([exact] if exact else []), *aliased]}
        name_matches = list(by_id.values())

    return IdentityFacts(
        quoted_invoices=invoices,
        unknown_numbers=[n for n in numbers if normalize_invoice_no(n) not in known],
        quoted_owners=owners,
        email_matches=email_matches,
        name_matches=name_matches,
        payer_name_is_ours=ours,
    )


async def identify_payer(
    db: AsyncSession, payment: ExtractedPayment, *, our_company_name: str | None = None
) -> IdentityResult:
    return decide_identity(await gather_facts(db, payment, our_company_name=our_company_name))
