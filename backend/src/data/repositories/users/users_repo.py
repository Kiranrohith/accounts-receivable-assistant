import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from data.models.postgres import Permission, Role, RolePermission, User
from data.repositories import unit_of_work


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(
        select(User).options(selectinload(User.role)).where(User.email == email)
    )


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.scalar(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )


async def is_active_user_email(db: AsyncSession, email: str) -> bool:
    """Is this address (case-insensitive) one of our active application users?"""
    found = await db.scalar(
        select(User.id).where(func.lower(User.email) == email.strip().lower(), User.is_active).limit(1)
    )
    return found is not None


async def email_exists(db: AsyncSession, email: str) -> bool:
    return await db.scalar(select(User.id).where(User.email == email)) is not None


async def get_active_role(db: AsyncSession, code: str) -> Role | None:
    return await db.scalar(select(Role).where(Role.code == code, Role.is_active))


async def insert_user(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    password_hash: str,
    role: Role,
    actor_id: uuid.UUID,
) -> User:
    user = User(
        username=name,
        email=email,
        password_hash=password_hash,
        role_id=role.id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(user)
    await unit_of_work.flush(db)
    await db.refresh(user)
    user.role = role
    return user


async def list_active_users(db: AsyncSession) -> list[User]:
    result = await db.scalars(
        select(User)
        .options(selectinload(User.role))
        .where(User.is_active)
        .order_by(User.created_at, User.email)
    )
    return list(result)


async def permission_codes_for_role(db: AsyncSession, role_id: uuid.UUID) -> list[str]:
    result = await db.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list(result)


async def lock_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """The user, locked for the rest of the transaction so two edits (or two demotions) cannot interleave."""
    return await db.scalar(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == user_id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )


async def email_taken_by_other(db: AsyncSession, email: str, user_id: uuid.UUID) -> bool:
    return await db.scalar(select(User.id).where(User.email == email, User.id != user_id)) is not None


async def active_users_with_role(db: AsyncSession, role_id: uuid.UUID) -> int:
    return (await db.scalar(select(func.count()).select_from(User).where(User.role_id == role_id, User.is_active))) or 0


async def change_user(
    db: AsyncSession,
    user: User,
    *,
    actor_id: uuid.UUID,
    name: str | None = None,
    email: str | None = None,
    role: Role | None = None,
    password_hash: str | None = None,
) -> User:
    if name is not None:
        user.username = name
    if email is not None:
        user.email = email
    if role is not None:
        user.role_id = role.id
        user.role = role
    if password_hash is not None:
        user.password_hash = password_hash
    user.updated_by = actor_id
    await unit_of_work.flush(db)
    return user
