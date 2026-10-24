from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, CustomerAlias, Invoice, Payment, User
from data.models.postgres.enums import AliasSource, AliasType, InvoiceStatus, InvoiceType
from data.repositories import unit_of_work
from data.repositories.customers.tax_ids import normalize_tax_id


def _name_key(value: Any) -> ColumnElement:
    """Trimmed, spaces collapsed, lower-cased: the same expression as the unique index."""
    return func.lower(func.regexp_replace(func.btrim(value), r"\s+", " ", "g"))


async def tax_id_exists(db: AsyncSession, tax_id: str, *, except_id: Any = None) -> bool:
    """Does another customer hold this tax ID (compared in its normalized form)?"""
    query = select(Customer.id).where(Customer.tax_id == normalize_tax_id(tax_id))
    if except_id is not None:
        query = query.where(Customer.id != except_id)
    return await db.scalar(query.limit(1)) is not None


async def email_exists(db: AsyncSession, email: str, *, except_id: Any = None) -> bool:
    query = select(Customer.id).where(func.lower(Customer.email) == email.lower())
    if except_id is not None:
        query = query.where(Customer.id != except_id)
    return await db.scalar(query.limit(1)) is not None


async def name_exists(db: AsyncSession, name: str, *, except_id: Any = None) -> bool:
    query = select(Customer.id).where(_name_key(Customer.customer_name) == _name_key(literal(name)))
    if except_id is not None:
        query = query.where(Customer.id != except_id)
    return await db.scalar(query.limit(1)) is not None


async def get_for_update(db: AsyncSession, customer_id: Any) -> Customer | None:
    """Row-locks the customer so two edits (or an edit and a recompute) cannot interleave."""
    return await db.scalar(select(Customer).where(Customer.id == customer_id).with_for_update())


async def get_by_id(db: AsyncSession, customer_id: Any) -> Customer | None:
    return await db.get(Customer, customer_id)


async def get_by_tax_id(db: AsyncSession, tax_id: str) -> Customer | None:
    """The customer holding this tax ID: exact equality of the normalized forms, nothing looser."""
    normalized = normalize_tax_id(tax_id)
    if not normalized:
        return None
    return await db.scalar(select(Customer).where(Customer.tax_id == normalized))


async def get_by_name(db: AsyncSession, name: str) -> Customer | None:
    """Exact name after trimming, collapsing spaces and ignoring case (names are unique)."""
    return await db.scalar(
        select(Customer).where(_name_key(Customer.customer_name) == _name_key(literal(name)))
    )


async def get_by_alias_name(db: AsyncSession, name: str) -> list[Customer]:
    """Customers a person has confirmed this name as an alias for (several -> ambiguous)."""
    result = await db.scalars(
        select(Customer)
        .join(CustomerAlias, CustomerAlias.customer_id == Customer.id)
        .where(
            CustomerAlias.alias_type == AliasType.NAME,
            _name_key(CustomerAlias.alias_value) == _name_key(literal(name)),
        )
        .distinct()
    )
    return list(result)


async def get_by_email_or_alias(db: AsyncSession, email: str) -> list[Customer]:
    """Customers whose own e-mail, or a confirmed e-mail alias, is this address (case-insensitive).
    Several means a shared address: the caller must not guess between them."""
    address = email.strip().lower()
    result = await db.scalars(
        select(Customer)
        .outerjoin(
            CustomerAlias,
            (CustomerAlias.customer_id == Customer.id) & (CustomerAlias.alias_type == AliasType.EMAIL),
        )
        .where(or_(func.lower(Customer.email) == address, func.lower(CustomerAlias.alias_value) == address))
        .distinct()
    )
    return list(result)


async def get_alias(
    db: AsyncSession, customer_id: Any, alias_type: AliasType, value: str
) -> CustomerAlias | None:
    return await db.scalar(
        select(CustomerAlias).where(
            CustomerAlias.customer_id == customer_id,
            CustomerAlias.alias_type == alias_type,
            func.lower(CustomerAlias.alias_value) == value.lower(),
        )
    )


async def insert_alias(
    db: AsyncSession, *, customer_id: Any, alias_type: AliasType, value: str, actor_id: Any
) -> CustomerAlias:
    alias = CustomerAlias(
        customer_id=customer_id,
        alias_type=alias_type,
        alias_value=value,
        source=AliasSource.HUMAN_REVIEW,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(alias)
    await unit_of_work.flush(db)
    return alias


async def insert_customer(db: AsyncSession, *, fields: dict[str, Any], actor_id: Any) -> Customer:
    customer = Customer(**fields, created_by=actor_id, updated_by=actor_id)
    db.add(customer)
    await unit_of_work.flush(db)
    await db.refresh(customer)
    return customer


async def list_active_customers(db: AsyncSession) -> list[Customer]:
    result = await db.scalars(
        select(Customer).where(Customer.is_active).order_by(Customer.customer_name)
    )
    return list(result)


async def list_name_aliases(db: AsyncSession) -> list[tuple[Any, str]]:
    """(customer id, alias) of every confirmed name alias of an active customer."""
    rows = await db.execute(
        select(CustomerAlias.customer_id, CustomerAlias.alias_value)
        .join(Customer, Customer.id == CustomerAlias.customer_id)
        .where(Customer.is_active, CustomerAlias.alias_type == AliasType.NAME)
    )
    return [(customer_id, value) for customer_id, value in rows]


async def customer_stats(db: AsyncSession, today: date, customer_id: Any = None) -> dict[Any, dict[str, Any]]:
    """Per customer: invoices still to collect (Open, PartlyPaid), how many are past due, what they add up to, and the
    INR received from the customer that is not yet against an invoice."""
    owing = (
        select(
            Invoice.customer_id,
            func.count().label("open"),
            func.count().filter(Invoice.due_date < today).label("overdue"),
            func.coalesce(func.sum(Invoice.collectible_balance), 0).label("outstanding"),
        )
        .where(Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTLY_PAID]))
        .group_by(Invoice.customer_id)
    )
    cash = (
        select(Payment.customer_id, func.coalesce(func.sum(Payment.unapplied_amount), 0).label("unapplied"))
        .where(Payment.customer_id.is_not(None), Payment.unapplied_amount > 0)
        .group_by(Payment.customer_id)
    )
    if customer_id is not None:
        owing = owing.where(Invoice.customer_id == customer_id)
        cash = cash.where(Payment.customer_id == customer_id)
    stats: dict[Any, dict[str, Any]] = {}
    for cid, count, overdue, outstanding in await db.execute(owing):
        stats.setdefault(cid, {}).update(open=count, overdue=overdue, outstanding=Decimal(outstanding))
    for cid, unapplied in await db.execute(cash):
        stats.setdefault(cid, {}).update(unapplied=Decimal(unapplied))
    return stats


async def aliases_with_confirmer(db: AsyncSession, customer_id: Any) -> list[tuple[CustomerAlias, str | None]]:
    """The customer's confirmed aliases, oldest first, with the username of whoever confirmed each."""
    rows = await db.execute(
        select(CustomerAlias, User.username)
        .outerjoin(User, User.id == CustomerAlias.created_by)
        .where(CustomerAlias.customer_id == customer_id)
        .order_by(CustomerAlias.created_at, CustomerAlias.alias_value)
    )
    return [(alias, username) for alias, username in rows]


async def alias_in_use_elsewhere(
    db: AsyncSession, customer_id: Any, alias_type: AliasType, value: str
) -> Customer | None:
    """Another customer whose own e-mail (or, for a name alias, whose own name) is this value: an alias must not
    take it over."""
    if alias_type is AliasType.EMAIL:
        return await db.scalar(
            select(Customer).where(func.lower(Customer.email) == value.lower(), Customer.id != customer_id).limit(1)
        )
    return await db.scalar(
        select(Customer)
        .where(_name_key(Customer.customer_name) == _name_key(literal(value)), Customer.id != customer_id)
        .limit(1)
    )


async def count_tds_out_of_date(db: AsyncSession, customer_id: Any, deducts_tds: bool) -> int:
    """Invoices still to collect whose frozen TDS does not match the customer's setting now (debit notes never carry TDS)."""
    carries = func.coalesce(Invoice.tds_amount, 0) > 0
    wrong = (~carries & (Invoice.amount_inr > 0)) if deducts_tds else carries
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTLY_PAID]),
                Invoice.type == InvoiceType.INVOICE,
                wrong,
            )
        )
        or 0
    )
