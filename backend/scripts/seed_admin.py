"""Create the single seed admin from SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD in .env.

Idempotent: if the email already exists nothing is changed (the password is never
overwritten). Other users are created by the admin through the API.

Run from backend/:  uv run python scripts/seed_admin.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from config.settings import get_settings  # noqa: E402
from core.services.users.auth import hash_password  # noqa: E402
from data.clients.db import SessionLocal, engine  # noqa: E402
from data.models.postgres import Role, User  # noqa: E402


async def main() -> None:
    settings = get_settings()
    if not settings.seed_admin_email or not settings.seed_admin_password:
        raise SystemExit("Set SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD in backend/.env")

    email = settings.seed_admin_email.strip().lower()
    async with SessionLocal() as db:
        if await db.scalar(select(User).where(User.email == email)):
            print(f"Admin {email} already exists; nothing changed.")
        else:
            role = await db.scalar(select(Role).where(Role.code == "ADM"))
            if role is None:
                raise SystemExit("ADM role missing; run `alembic upgrade head` first")
            db.add(
                User(
                    username="Admin",
                    email=email,
                    password_hash=await hash_password(settings.seed_admin_password),
                    role_id=role.id,
                )
            )
            await db.commit()
            print(f"Created admin {email} with role ADM.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
