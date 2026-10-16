from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError
from data.models.postgres import Customer, User
from data.repositories import unit_of_work
from data.repositories.customers import customers_repo
from data.repositories.unit_of_work import DuplicateRecordError
from schemas.customers.customers import CustomerCreate


async def create_customer(db: AsyncSession, actor: User, data: CustomerCreate) -> Customer:
    email = data.email.lower()

    taken = []
    if await customers_repo.gstin_exists(db, data.gstin):
        taken.append("GSTIN")
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
        "gstin": data.gstin,
        "deducts_tds": data.deducts_tds,
        "billing_currency": data.billing_currency,
        "country": data.country,
    }
    try:
        customer = await customers_repo.insert_customer(db, fields=fields, actor_id=actor.id)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:  # lost a race; the unique indexes are the backstop
        raise ConflictError("A customer with the same GSTIN, name or email already exists") from exc
    return customer


async def list_customers(db: AsyncSession) -> list[Customer]:
    return await customers_repo.list_active_customers(db)
