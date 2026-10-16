from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, ValidationFailure
from core.services.users.auth import hash_password
from data.models.postgres import User
from data.repositories import unit_of_work
from data.repositories.unit_of_work import DuplicateRecordError
from data.repositories.users import users_repo
from schemas.users.users import UserCreate


async def create_user(db: AsyncSession, actor: User, data: UserCreate) -> User:
    email = data.email.lower()
    if await users_repo.email_exists(db, email):
        raise ConflictError(f"A user with email {email} already exists")

    role = await users_repo.get_active_role(db, data.role)
    if role is None:
        raise ValidationFailure(f"Unknown role '{data.role}'; use FA, FTM or ADM")

    password_hash = await hash_password(data.password)
    try:
        user = await users_repo.insert_user(
            db,
            name=data.name,
            email=email,
            password_hash=password_hash,
            role=role,
            actor_id=actor.id,
        )
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:  # lost a race with another create for this email
        raise ConflictError(f"A user with email {email} already exists") from exc
    return user


async def list_active_users(db: AsyncSession) -> list[User]:
    return await users_repo.list_active_users(db)


async def permission_codes_for(db: AsyncSession, user: User) -> list[str]:
    return await users_repo.permission_codes_for_role(db, user.role_id)
