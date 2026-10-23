"""User administration: editing a user (name, e-mail, role) and setting a new password. Real database, no model, no mail.
Proves: who may do it, e-mail uniqueness (case-insensitive, and keeping one's own is fine), unknown roles, an empty edit,
that a role change takes effect on the person's very next request, that the only active administrator cannot be demoted
(while one of two can), that a password reset replaces the old password, and that nothing secret leaves the API."""

import asyncio
import sys
import uuid

sys.path.insert(0, "src")
import httpx
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_useradmin" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        bearer = lambda r: {"Authorization": f"Bearer {r.json()['access_token']}"}
        ADM = bearer(await login(s.seed_admin_email, s.seed_admin_password))
        new = lambda n, e, role: c.post("/admin/users", headers=ADM, json={"name": n, "email": e, "password": "pass12345", "role": role})
        fa = (await new("Fa One", "fa.ua@example.com", "FA")).json()
        ftm = (await new("Ftm One", "ftm.ua@example.com", "FTM")).json()
        other = (await new("Fa Two", "fa2.ua@example.com", "FA")).json()
        FA = bearer(await login("fa.ua@example.com", "pass12345"))
        FTM = bearer(await login("ftm.ua@example.com", "pass12345"))
        admin = next(u for u in (await c.get("/admin/users", headers=ADM)).json() if u["email"] == s.seed_admin_email.lower())

        # ======================================== A. editing: who, what, and what is refused
        check("no token -> 401", (await c.patch(f"/admin/users/{fa['id']}", json={"name": "X"})).status_code == 401)
        check("an FA cannot edit a user -> 403", (await c.patch(f"/admin/users/{fa['id']}", headers=FA, json={"name": "X"})).status_code == 403)
        check("an FTM cannot edit a user -> 403", (await c.patch(f"/admin/users/{fa['id']}", headers=FTM, json={"name": "X"})).status_code == 403)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"name": "  Fiona Associate  "})
        check("rename: trimmed, the rest untouched", r.status_code == 200 and r.json()["name"] == "Fiona Associate" and r.json()["email"] == "fa.ua@example.com" and r.json()["role"] == "FA", r.text)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"email": "Fiona.New@Example.com"})
        check("e-mail change is lower-cased", r.status_code == 200 and r.json()["email"] == "fiona.new@example.com", r.text)
        check("the new e-mail signs in, the old one does not", (await login("fiona.new@example.com", "pass12345")).status_code == 200 and (await login("fa.ua@example.com", "pass12345")).status_code == 401)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"email": "FIONA.NEW@example.com"})
        check("sending one's own e-mail again (any case) is not a clash", r.status_code == 200, r.text)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"email": "ftm.ua@example.com"})
        check("an e-mail another user has -> 409", r.status_code == 409 and "already exists" in r.json()["detail"], r.text)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"email": "FTM.UA@example.com"})
        check("... whatever its case -> 409", r.status_code == 409, r.text)
        check("nothing to change -> 422", (await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={})).status_code == 422)
        check("a bad e-mail -> 422", (await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"email": "not-an-email"})).status_code == 422)
        check("a blank name -> 422", (await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"name": "   "})).status_code == 422)
        check("an unknown role -> 422", (await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"role": "BOSS"})).status_code == 422)
        check("an unknown user -> 404", (await c.patch(f"/admin/users/{uuid.uuid4()}", headers=ADM, json={"name": "X"})).status_code == 404)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"role": "fa"})
        check("giving the role the person already has is fine (and not case-sensitive)", r.status_code == 200 and r.json()["role"] == "FA", r.text)
        by = await q("select updated_by from users where id = :i", i=fa["id"])
        check("the edit records who made it", str(by[0][0]) == admin["id"], str(by))
        check("the list shows the edit", any(u["name"] == "Fiona Associate" for u in (await c.get("/admin/users", headers=ADM)).json()))

        # ======================================== B. a role change applies at once
        FA2 = bearer(await login("fiona.new@example.com", "pass12345"))
        check("before: an FA cannot list users", (await c.get("/admin/users", headers=FA2)).status_code == 403)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"role": "ADM"})
        check("promote an FA to administrator", r.status_code == 200 and r.json()["role"] == "ADM", r.text)
        check("the person's next request, on the token they already hold, is checked against the new role", (await c.get("/admin/users", headers=FA2)).status_code == 200)
        r = await c.patch(f"/admin/users/{fa['id']}", headers=ADM, json={"role": "FTM"})
        check("with two administrators, one can be given another role", r.status_code == 200 and r.json()["role"] == "FTM", r.text)
        check("and loses the administrator's access at once", (await c.get("/admin/users", headers=FA2)).status_code == 403)
        me = (await c.get("/auth/me", headers=FA2)).json()
        check("/auth/me reports the new role and its permissions", me["role"] == "FTM" and "users.manage" not in me["permissions"], str(me)[:200])

        # ======================================== C. the last administrator
        r = await c.patch(f"/admin/users/{admin['id']}", headers=ADM, json={"role": "FA"})
        check("the only active administrator cannot be given another role -> 409", r.status_code == 409 and "only active administrator" in r.json()["detail"], r.text)
        check("... and is still an administrator", (await q("select r.code from users u join roles r on r.id=u.role_id where u.id=:i", i=admin["id"]))[0][0] == "ADM")
        r = await c.patch(f"/admin/users/{admin['id']}", headers=ADM, json={"name": "Chief Admin", "role": "ADM"})
        check("the administrator can still be renamed", r.status_code == 200 and r.json()["name"] == "Chief Admin", r.text)

        # ======================================== D. setting a new password
        check("no token -> 401", (await c.post(f"/admin/users/{ftm['id']}/password", json={"password": "brandnew123"})).status_code == 401)
        check("an FA cannot set a password -> 403", (await c.post(f"/admin/users/{ftm['id']}/password", headers=FA2, json={"password": "brandnew123"})).status_code == 403)
        r = await c.post(f"/admin/users/{ftm['id']}/password", headers=ADM, json={"password": "brandnew123"})
        check("an administrator sets a password -> 204 with no body", r.status_code == 204 and r.content == b"", r.text)
        check("the old password stops working, the new one works", (await login("ftm.ua@example.com", "pass12345")).status_code == 401 and (await login("ftm.ua@example.com", "brandnew123")).status_code == 200)
        h = (await q("select password_hash from users where id=:i", i=ftm["id"]))[0][0]
        check("it is stored as a bcrypt hash, never the text", h.startswith("$2b$") and "brandnew123" not in h)
        check("a password under 8 characters -> 422", (await c.post(f"/admin/users/{ftm['id']}/password", headers=ADM, json={"password": "short"})).status_code == 422)
        check("a password over 72 bytes -> 422", (await c.post(f"/admin/users/{ftm['id']}/password", headers=ADM, json={"password": "x" * 73})).status_code == 422)
        check("an unknown user -> 404", (await c.post(f"/admin/users/{uuid.uuid4()}/password", headers=ADM, json={"password": "brandnew123"})).status_code == 404)
        check("an administrator can set their own password", (await c.post(f"/admin/users/{admin['id']}/password", headers=ADM, json={"password": "adminchanged1"})).status_code == 204 and (await login(s.seed_admin_email, "adminchanged1")).status_code == 200)

        # ======================================== E. nothing secret in any answer
        everything = str((await c.get("/admin/users", headers=ADM)).json()) + (await c.patch(f"/admin/users/{other['id']}", headers=ADM, json={"name": "Fa Deux"})).text
        check("no answer carries a password or a hash", "password" not in everything.lower() and "$2b$" not in everything)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
