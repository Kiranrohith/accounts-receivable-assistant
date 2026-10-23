import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions.errors import ConflictError, NotFoundError, ValidationFailure
from core.services.users.auth import hash_password
from data.models.postgres import User
from data.repositories import unit_of_work
from data.repositories.unit_of_work import DuplicateRecordError
from data.repositories.users import users_repo
from schemas.users.users import PasswordReset, UserCreate, UserUpdate


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


ADMIN_ROLE = "ADM"


async def update_user(db: AsyncSession, actor: User, user_id: uuid.UUID, data: UserUpdate) -> User:
    """Change a user's name, e-mail or role. The last active administrator cannot be moved to another role."""
    user = await users_repo.lock_user(db, user_id)
    if user is None:
        raise NotFoundError("User not found")

    email = data.email.lower() if data.email is not None else None
    if email is not None and email != user.email and await users_repo.email_taken_by_other(db, email, user.id):
        raise ConflictError(f"A user with email {email} already exists")

    role = None
    if data.role is not None:
        role = await users_repo.get_active_role(db, data.role)
        if role is None:
            raise ValidationFailure(f"Unknown role '{data.role}'; use FA, FTM or ADM")
        leaving_admin = user.role.code == ADMIN_ROLE and role.code != ADMIN_ROLE and user.is_active
        if leaving_admin and await users_repo.active_users_with_role(db, user.role_id) <= 1:
            raise ConflictError("This is the only active administrator; make someone else an administrator first")
        if role.id == user.role_id:
            role = None  # not a change

    try:
        await users_repo.change_user(db, user, actor_id=actor.id, name=data.name, email=email, role=role)
        await unit_of_work.commit(db)
    except DuplicateRecordError as exc:  # lost a race with another edit or create for this e-mail
        raise ConflictError(f"A user with email {email} already exists") from exc
    return user


async def reset_password(db: AsyncSession, actor: User, user_id: uuid.UUID, data: PasswordReset) -> None:
    """An administrator sets a new password for someone who forgot theirs."""
    user = await users_repo.lock_user(db, user_id)
    if user is None:
        raise NotFoundError("User not found")
    password_hash = await hash_password(data.password)
    await users_repo.change_user(db, user, actor_id=actor.id, password_hash=password_hash)
    await unit_of_work.commit(db)


async def list_active_users(db: AsyncSession) -> list[User]:
    return await users_repo.list_active_users(db)


async def permission_codes_for(db: AsyncSession, user: User) -> list[str]:
    return await users_repo.permission_codes_for_role(db, user.role_id)
