"""POST /auth/password: someone changes their own password. Real database, no model, no mail. Proves: the current password is
required and checked (422, never 401, which would sign the person out), the new one must differ and be 8 to 72 bytes, the old
password stops working and the new one works, the person is not signed out, and nobody can change anyone else's this way."""

import asyncio
import sys

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_selfpw" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email in (("Fa One", "fa1.pw@example.com"), ("Fa Two", "fa2.pw@example.com")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "oldpass123", "role": "FA"})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa1.pw@example.com', 'oldpass123')).json()['access_token']}"}
        change = lambda cur, new, H=FA: c.post("/auth/password", headers=H, json={"current_password": cur, "new_password": new})

        check("no token -> 401", (await c.post("/auth/password", json={"current_password": "a", "new_password": "bbbbbbbb"})).status_code == 401)
        r = await change("wrongpass1", "newpass1234")
        check("a wrong current password -> 422 (not 401: the person stays signed in)", r.status_code == 422 and "current password" in r.json()["detail"], r.text)
        check("... and nothing changed", (await login("fa1.pw@example.com", "oldpass123")).status_code == 200)
        r = await change("oldpass123", "oldpass123")
        check("the same password again -> 422", r.status_code == 422 and "different" in r.json()["detail"], r.text)
        check("a new password under 8 characters -> 422", (await change("oldpass123", "short")).status_code == 422)
        check("a new password over 72 bytes -> 422", (await change("oldpass123", "x" * 73)).status_code == 422)
        check("an empty current password -> 422", (await change("", "newpass1234")).status_code == 422)

        r = await change("oldpass123", "newpass1234")
        check("the right current password and a good new one -> 204, no body", r.status_code == 204 and r.content == b"", r.text)
        check("the old password stops working, the new one works", (await login("fa1.pw@example.com", "oldpass123")).status_code == 401 and (await login("fa1.pw@example.com", "newpass1234")).status_code == 200)
        check("the token the person holds still works (they are not signed out)", (await c.get("/auth/me", headers=FA)).status_code == 200)
        async with SessionLocal() as db:
            h = (await db.execute(text("select password_hash from users where email='fa1.pw@example.com'"))).scalar()
            other = (await db.execute(text("select password_hash from users where email='fa2.pw@example.com'"))).scalar()
        check("stored as a bcrypt hash, never the text", h.startswith("$2b$") and "newpass1234" not in h)
        check("someone else's password is untouched", (await login("fa2.pw@example.com", "oldpass123")).status_code == 200 and other.startswith("$2b$"))
        r = await change("newpass1234", "third-pass-9")
        check("it can be changed again, with the new current password", r.status_code == 204 and (await login("fa1.pw@example.com", "third-pass-9")).status_code == 200)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
