"""Resolve who an invoice (later: a payment) belongs to: GSTIN, then name, then alias.

The customer-side GSTIN and name come from extraction. Anything doubtful is returned as
an issue and the caller sends the document to review; this never guesses:

- the GSTIN printed is our own, or is not a valid 15-character GSTIN
- the name is a confirmed alias of several customers
- the GSTIN and the name point at two different customers (identity conflict)
- the GSTIN is unknown but the name matches a customer who has a different GSTIN
- no customer found at all
- the matched customer is deactivated
"""

import re
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import GSTIN_PATTERN
from data.models.postgres import Customer
from data.repositories.customers import customers_repo

_GSTIN = re.compile(GSTIN_PATTERN)


@dataclass(frozen=True)
class CustomerMatch:
    customer: Customer | None
    method: str | None  # "gstin", "name" or "alias"
    issues: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        """A customer was found and nothing about it is doubtful."""
        return self.customer is not None and not self.issues


def _normalise_name(name: str) -> str:
    return " ".join(name.split()).lower()


async def resolve_customer(
    db: AsyncSession,
    *,
    gstin: str | None,
    name: str | None,
    our_gstin: str | None = None,
    our_company_name: str | None = None,
) -> CustomerMatch:
    issues: list[str] = []

    gstin = "".join(gstin.split()).upper() if gstin else None
    if gstin and our_gstin and gstin == our_gstin.upper():
        issues.append("The GSTIN read as the customer's is our own GSTIN; the customer could not be identified")
        gstin = None
    elif gstin and not _GSTIN.match(gstin):
        issues.append(f"'{gstin}' is not a valid GSTIN, so it cannot be used to find the customer")
        gstin = None

    name = " ".join(name.split()) if name else None
    if name and our_company_name and _normalise_name(name) == _normalise_name(our_company_name):
        issues.append("The name read as the customer's is our own company name")
        name = None

    by_gstin = await customers_repo.get_by_gstin(db, gstin) if gstin else None
    by_name = await customers_repo.get_by_name(db, name) if name else None
    name_method = "name"
    if name and by_name is None:  # last resort: a name a person confirmed as an alias
        aliased = await customers_repo.get_by_alias_name(db, name)
        if len(aliased) > 1:
            issues.append(
                f"The name '{name}' is a confirmed alias of several customers: "
                + ", ".join(sorted(c.customer_name for c in aliased))
            )
            return CustomerMatch(None, None, issues)
        if aliased:
            by_name, name_method = aliased[0], "alias"

    if by_gstin and by_name and by_gstin.id != by_name.id:
        issues.append(
            f"Identity conflict: GSTIN {gstin} belongs to '{by_gstin.customer_name}' "
            f"but the name '{name}' belongs to '{by_name.customer_name}'"
        )
        return CustomerMatch(None, None, issues)

    if by_gstin:
        customer, method = by_gstin, "gstin"
    elif by_name and gstin:
        issues.append(
            f"GSTIN {gstin} is not in the customer master, but the name '{name}' matches "
            f"'{by_name.customer_name}' (GSTIN {by_name.gstin}); please check which is right"
        )
        return CustomerMatch(by_name, None, issues)
    elif by_name:
        customer, method = by_name, name_method
    else:
        if not issues:
            what = f"GSTIN {gstin}" if gstin else f"name '{name}'" if name else "nothing"
            issues.append(f"Unknown customer: no customer in the master matches ({what})")
        return CustomerMatch(None, None, issues)

    if not customer.is_active:
        issues.append(f"Customer '{customer.customer_name}' is deactivated")
    return CustomerMatch(customer, method, issues)
