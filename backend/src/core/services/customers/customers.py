import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from constants.constants import BUSINESS_TIMEZONE
from core.exceptions.errors import ConflictError, NotFoundError, ValidationFailure
from core.services.customers.tax_id import SHAPE_HELP, has_shape
from data.models.postgres import Customer, User
from data.models.postgres.enums import AliasType, TaxIdType
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.customers.tax_ids import normalize_tax_id
from data.repositories.unit_of_work import DuplicateRecordError
from schemas.customers.customers import (
    AliasCreate,
    AliasOut,
    CustomerCreate,
    CustomerDetail,
    CustomerListItem,
    CustomerOut,
    CustomerUpdate,
)


def _checked_tax_id(value: str, kind: str) -> str:
    """The tax ID in its stored (normalized) form, after checking it has the shape of its type (OTHER has none)."""
    normalized = normalize_tax_id(value)
    if not normalized:
        raise ValidationFailure("The tax ID is empty")
    if not has_shape(kind, normalized):
        raise ValidationFailure(f"A {kind} must be {SHAPE_HELP[kind]}")
    return normalized


async def create_customer(db: AsyncSession, actor: User, data: CustomerCreate) -> Customer:
    email = data.email.lower()
    tax_id = _checked_tax_id(data.tax_id, data.tax_id_type.value)

    taken = []
    if await customers_repo.tax_id_exists(db, tax_id):
        taken.append("tax ID")
    if await customers_repo.name_exists(db, data.customer_name):
        taken.append("name")
    if await customers_repo.email_exists(db, email):
        taken.append("email")
    if taken:
        raise ConflictError(f"A customer with the same {', '.join(taken)} already exists")

    fields = {
        "customer_name": data.customer_name,
        "contact": data.contact,
        "email": email,
        "tax_id": tax_id,
        "tax_id_type": data.tax_id_type.value,
        "deducts_tds": data.deducts_tds,
        "billing_currency": data.billing_currency,
        "country": data.country,
    }
    try:
        customer = await customers_repo.insert_customer(db, fields=fields, actor_id=actor.id)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:  # lost a race; the unique indexes are the backstop
        raise ConflictError("A customer with the same tax ID, name or email already exists") from exc
    return customer


async def list_customers(db: AsyncSession) -> list[Customer]:
    return await customers_repo.list_active_customers(db)


def _today_ist() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()


def _with_stats(customer: Customer, stats: dict) -> dict:
    return {
        **CustomerOut.model_validate(customer).model_dump(),
        "open_invoices": stats.get("open", 0),
        "overdue_invoices": stats.get("overdue", 0),
        "outstanding": stats.get("outstanding", Decimal("0.00")),
        "unapplied_cash": stats.get("unapplied", Decimal("0.00")),
    }


async def list_customers_with_stats(db: AsyncSession) -> list[CustomerListItem]:
    customers = await customers_repo.list_active_customers(db)
    stats = await customers_repo.customer_stats(db, _today_ist())
    return [CustomerListItem(**_with_stats(c, stats.get(c.id, {}))) for c in customers]


async def get_customer_detail(db: AsyncSession, customer_id: uuid.UUID) -> CustomerDetail:
    customer = await customers_repo.get_by_id(db, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    stats = (await customers_repo.customer_stats(db, _today_ist(), customer_id)).get(customer_id, {})
    aliases = [
        AliasOut(
            alias_id=a.id, alias_type=a.alias_type.value, alias_value=a.alias_value,
            source=a.source.value if a.source else None, confirmed_by=who, created_at=a.created_at,
        )
        for a, who in await customers_repo.aliases_with_confirmer(db, customer_id)
    ]
    return CustomerDetail(
        **_with_stats(customer, stats),
        aliases=aliases,
        tds_out_of_date=await customers_repo.count_tds_out_of_date(db, customer_id, customer.deducts_tds),
    )


async def update_customer(db: AsyncSession, actor: User, customer_id: uuid.UUID, data: CustomerUpdate) -> CustomerDetail:
    customer = await customers_repo.get_for_update(db, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "email" in changes:
        changes["email"] = changes["email"].lower()
    if "tax_id" in changes or "tax_id_type" in changes:  # the id and its type are checked together, whichever one was sent
        kind = (changes.get("tax_id_type") or customer.tax_id_type or TaxIdType.GSTIN).value
        changes["tax_id"] = _checked_tax_id(changes.get("tax_id") or customer.tax_id, kind)
        changes["tax_id_type"] = kind

    taken = []
    if "tax_id" in changes and changes["tax_id"] != customer.tax_id and await customers_repo.tax_id_exists(db, changes["tax_id"], except_id=customer.id):
        taken.append("tax ID")
    if "customer_name" in changes and await customers_repo.name_exists(db, changes["customer_name"], except_id=customer.id):
        taken.append("name")
    if "email" in changes and await customers_repo.email_exists(db, changes["email"], except_id=customer.id):
        taken.append("email")
    if taken:
        raise ConflictError(f"Another customer already has the same {', '.join(taken)}")

    for field, value in changes.items():
        setattr(customer, field, value)
    customer.updated_by = actor.id
    try:
        await unit_of_work.flush(db)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:  # lost a race; the unique indexes are the backstop
        raise ConflictError("Another customer already has the same tax ID, name or email") from exc
    return await get_customer_detail(db, customer_id)


async def remember_name(db: AsyncSession, actor: User, customer: Customer, raw_name: str | None) -> str | None:
    """Save a name read from a document as an alias of the customer a person matched it to. Nothing is saved (None)
    when the name is empty, is the customer's own name, or is already one of its aliases; a name that is another
    customer's own name is refused. Flushes only: the caller commits with its own work."""
    value = " ".join((raw_name or "").split())
    if not value or value.lower() == " ".join(customer.customer_name.split()).lower():
        return None
    owner = await customers_repo.alias_in_use_elsewhere(db, customer.id, AliasType.NAME, value)
    if owner is not None:
        raise ConflictError(f"'{value}' is the name of {owner.customer_name}, so it cannot be saved as an alias here")
    if await customers_repo.get_alias(db, customer.id, AliasType.NAME, value) is not None:
        return None
    await customers_repo.insert_alias(db, customer_id=customer.id, alias_type=AliasType.NAME, value=value, actor_id=actor.id)
    return value


async def add_alias(db: AsyncSession, actor: User, customer_id: uuid.UUID, data: AliasCreate) -> CustomerDetail:
    customer = await customers_repo.get_for_update(db, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found")
    kind = AliasType(data.alias_type)
    value = " ".join(data.alias_value.split())
    if kind is AliasType.EMAIL:
        value = value.lower()
        if "@" not in value or " " in value:
            raise ConflictError("That is not an e-mail address")
        if value == customer.email.lower():
            raise ConflictError("That is already this customer's own e-mail address")
    elif value.lower() == " ".join(customer.customer_name.split()).lower():
        raise ConflictError("That is already this customer's own name")
    owner = await customers_repo.alias_in_use_elsewhere(db, customer.id, kind, value)
    if owner is not None:
        raise ConflictError(f"'{value}' already belongs to {owner.customer_name}")
    if await customers_repo.get_alias(db, customer.id, kind, value) is not None:
        raise ConflictError(f"'{value}' is already an alias of {customer.customer_name}")
    try:
        await customers_repo.insert_alias(db, customer_id=customer.id, alias_type=kind, value=value, actor_id=actor.id)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:
        raise ConflictError(f"'{value}' is already an alias of {customer.customer_name}") from exc
    return await get_customer_detail(db, customer_id)
