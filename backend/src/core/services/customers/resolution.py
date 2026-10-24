"""Resolve who an invoice or purchase order belongs to: tax ID, then name, then alias.

The customer-side tax ID (a GSTIN, a VAT number, a TRN, an EIN...) and name come from extraction. The tax ID layer is exact:
the normalized value equals the customer's normalized tax_id, or it does not match; there is no fuzzy or prefix matching
on an id. An id of type OTHER is never used. Anything doubtful is returned as
an issue and the caller sends the document to review; this never guesses:

- the tax ID printed is our own GSTIN, or does not have the shape of its type (a GSTIN is 15 characters)
- the name is a confirmed alias of several customers
- the tax ID and the name point at two different customers (identity conflict)
- the tax ID is unknown but the name matches a customer who has a different tax ID
- no customer found at all
- the matched customer is deactivated
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.customers.tax_id import has_shape, infer_type
from data.models.postgres import Customer
from data.repositories.customers import customers_repo
from data.repositories.customers.tax_ids import normalize_tax_id


@dataclass(frozen=True)
class CustomerMatch:
    customer: Customer | None
    method: str | None  # "tax_id", "name" or "alias"
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
    tax_id: str | None,
    name: str | None,
    tax_id_type: str | None = None,
    our_gstin: str | None = None,
    our_company_name: str | None = None,
) -> CustomerMatch:
    issues: list[str] = []

    value = tax_id
    tax_id = normalize_tax_id(tax_id) or None
    kind = getattr(tax_id_type, "value", tax_id_type) or (infer_type(tax_id) if tax_id else None)
    if tax_id and our_gstin and tax_id == normalize_tax_id(our_gstin):
        issues.append("The GSTIN read as the customer's is our own GSTIN; the customer could not be identified")
        tax_id = None
    elif tax_id and kind == "OTHER":  # stored and shown, never used to find a customer
        tax_id = None
    elif tax_id and (kind is None or not has_shape(kind, tax_id)):
        issues.append(
            f"'{(value or '').strip()}' is not a valid {kind or 'tax ID'}, so it cannot be used to find the customer"
        )
        tax_id = None

    name = " ".join(name.split()) if name else None
    if name and our_company_name and _normalise_name(name) == _normalise_name(our_company_name):
        issues.append("The name read as the customer's is our own company name")
        name = None

    by_tax_id = await customers_repo.get_by_tax_id(db, tax_id) if tax_id else None
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

    if by_tax_id and by_name and by_tax_id.id != by_name.id:
        issues.append(
            f"Identity conflict: {kind or 'tax ID'} {tax_id} belongs to '{by_tax_id.customer_name}' "
            f"but the name '{name}' belongs to '{by_name.customer_name}'"
        )
        return CustomerMatch(None, None, issues)

    if by_tax_id:
        customer, method = by_tax_id, "tax_id"
    elif by_name and tax_id:
        issues.append(
            f"{kind or 'Tax ID'} {tax_id} is not in the customer master, but the name '{name}' matches "
            f"'{by_name.customer_name}' ({by_name.tax_id_type.value if by_name.tax_id_type else 'tax ID'} {by_name.tax_id}); "
            "please check which is right"
        )
        return CustomerMatch(by_name, None, issues)
    elif by_name:
        customer, method = by_name, name_method
    else:
        if not issues:
            what = f"{kind or 'tax ID'} {tax_id}" if tax_id else f"name '{name}'" if name else "nothing"
            issues.append(f"Unknown customer: no customer in the master matches ({what})")
        return CustomerMatch(None, None, issues)

    if not customer.is_active:
        issues.append(f"Customer '{customer.customer_name}' is deactivated")
    return CustomerMatch(customer, method, issues)
