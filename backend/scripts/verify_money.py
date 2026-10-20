"""Walk every invoice, payment and credit note and report any broken money identity.

Read-only. Exit code 1 when something is wrong, so CI and the test suites can use it.

Run from backend/:  uv run python scripts/verify_money.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.services.payments.identities import verify_money_identities  # noqa: E402
from data.clients.db import SessionLocal, engine  # noqa: E402


async def main() -> int:
    async with SessionLocal() as db:
        violations = await verify_money_identities(db)
    await engine.dispose()
    for violation in violations:
        print(f"VIOLATION {violation}")
    print(f"{len(violations)} violation(s)" if violations else "All money identities hold.")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
