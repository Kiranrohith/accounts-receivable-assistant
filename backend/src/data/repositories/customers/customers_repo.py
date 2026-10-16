from typing import Any

from sqlalchemy import ColumnElement, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.postgres import Customer, CustomerAlias
from data.models.postgres.enums import AliasType
from data.repositories import unit_of_work


def _name_key(value: Any) -> ColumnElement:
    """Trimmed, spaces collapsed, lower-cased: the same expression as the unique index."""
    return func.lower(func.regexp_replace(func.btrim(value), r"\s+", " ", "g"))


async def gstin_exists(db: AsyncSession, gstin: str) -> bool:
    return await db.scalar(select(Customer.id).where(Customer.gstin == gstin).limit(1)) is not None


async def email_exists(db: AsyncSession, email: str) -> bool:
    found = await db.scalar(
        select(Customer.id).where(func.lower(Customer.email) == email.lower()).limit(1)
    )
    return found is not None


async def name_exists(db: AsyncSession, name: str) -> bool:
    found = await db.scalar(
        select(Customer.id)
        .where(_name_key(Customer.customer_name) == _name_key(literal(name)))
        .limit(1)
    )
    return found is not None


async def get_by_id(db: AsyncSession, customer_id: Any) -> Customer | None:
    return await db.get(Customer, customer_id)


async def get_by_gstin(db: AsyncSession, gstin: str) -> Customer | None:
    return await db.scalar(select(Customer).where(Customer.gstin == gstin))


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
