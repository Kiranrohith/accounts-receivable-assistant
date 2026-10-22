import asyncio, sys, jwt
sys.path.insert(0, "src")
import httpx
from sqlalchemy import text
from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert not s.database_url.endswith("/ar_assistant"), "refusing to run against the real database"
results = []
def check(name, cond, extra=""):
    results.append(cond); print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}")

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        L = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        r = await L(s.seed_admin_email, "wrong");            check("login wrong password -> 401", r.status_code == 401)
        r = await L("nobody@example.com", "whatever1");     check("login unknown email -> 401", r.status_code == 401, r.json()["detail"])
        r = await L(s.seed_admin_email, s.seed_admin_password); check("admin login -> 200", r.status_code == 200)
        H = {"Authorization": f"Bearer {r.json()['access_token']}"}
        check("token has role ADM", jwt.decode(r.json()["access_token"], options={"verify_signature": False})["role"] == "ADM")

        check("/me without token -> 401", (await c.get("/auth/me")).status_code == 401)
        check("/me tampered token -> 401", (await c.get("/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})).status_code == 401)
        me = (await c.get("/auth/me", headers=H)).json()
        check("/me admin role + 27 permissions", me["role"] == "ADM" and len(me["permissions"]) == 27, f"{me['email']}")

        new = lambda n, e, p, role: c.post("/admin/users", headers=H, json={"name": n, "email": e, "password": p, "role": role})
        r = await new("Fa One", "fa.one@example.com", "fapass123", "fa");  check("create FA (role case-insensitive) -> 201", r.status_code == 201 and r.json()["role"] == "FA" and "password" not in str(r.json()))
        r = await new("Ftm One", "FTM.One@Example.com", "ftmpass123", "FTM"); check("create FTM, email lowercased -> 201", r.status_code == 201 and r.json()["email"] == "ftm.one@example.com")
        check("duplicate email -> 409", (await new("X", "fa.one@example.com", "fapass123", "FA")).status_code == 409)
        check("unknown role -> 422", (await new("X", "x1@example.com", "fapass123", "BOSS")).status_code == 422)
        check("short password -> 422", (await new("X", "x2@example.com", "short", "FA")).status_code == 422)
        check("bad email -> 422", (await new("X", "not-an-email", "fapass123", "FA")).status_code == 422)
        async with SessionLocal() as db:
            h = (await db.execute(text("select password_hash from users where email='fa.one@example.com'"))).scalar()
        check("password stored as bcrypt hash", h.startswith("$2b$") and "fapass123" not in h)
        users = (await c.get("/admin/users", headers=H)).json()
        check("list active users shows admin+FA+FTM", {"fa.one@example.com", "ftm.one@example.com", s.seed_admin_email.lower()} <= {u["email"] for u in users})

        FA = {"Authorization": f"Bearer {(await L('fa.one@example.com', 'fapass123')).json()['access_token']}"}
        FT = {"Authorization": f"Bearer {(await L('ftm.one@example.com', 'ftmpass123')).json()['access_token']}"}
        me = (await c.get("/auth/me", headers=FA)).json(); check("FA /me -> 15 permissions", me["role"] == "FA" and len(me["permissions"]) == 15)
        me = (await c.get("/auth/me", headers=FT)).json(); check("FTM /me -> 19 permissions", me["role"] == "FTM" and len(me["permissions"]) == 19)
        check("FA cannot create users -> 403", (await c.post("/admin/users", headers=FA, json={"name": "Z", "email": "z@example.com", "password": "zzzzzzzz1", "role": "FA"})).status_code == 403)
        check("FA cannot list users -> 403", (await c.get("/admin/users", headers=FA)).status_code == 403)

        cust = {"customer_name": "Zz Test Acme Ltd", "gstin": "29abcde1234f1z5", "email": "ZZ.AP@Acme-test.com", "billing_currency": "inr", "country": "India", "contact": "A. Rao", "deducts_tds": True}
        check("FA cannot add customer -> 403", (await c.post("/customers", headers=FA, json=cust)).status_code == 403)
        r = await c.post("/customers", headers=FT, json=cust)
        check("FTM adds customer -> 201, normalised", r.status_code == 201 and r.json()["gstin"] == "29ABCDE1234F1Z5" and r.json()["billing_currency"] == "INR" and r.json()["deducts_tds"] is True, str(r.status_code))
        check("duplicate GSTIN -> 409", (await c.post("/customers", headers=FT, json=cust)).status_code == 409)
        check("invalid GSTIN -> 422", (await c.post("/customers", headers=FT, json={**cust, "gstin": "BADGSTIN"})).status_code == 422)
        check("customer without GSTIN now refused -> 422", (await c.post("/customers", headers=FT, json={k: v for k, v in {**cust, "customer_name": "Zz Globex", "email": "zz.g@globex-test.com"}.items() if k != "gstin"})).status_code == 422)
        check("FA can view customers (sees the new one)", "Zz Test Acme Ltd" in [x["customer_name"] for x in (await c.get("/customers", headers=FA)).json()])

        check("logout -> 200", (await c.post("/auth/logout", headers=H)).status_code == 200)
        check("logout without token -> 401", (await c.post("/auth/logout")).status_code == 401)

        async with SessionLocal() as db:
            await db.execute(text("update users set is_active=false where email='fa.one@example.com'")); await db.commit()
        check("deactivated user cannot login -> 401", (await L("fa.one@example.com", "fapass123")).status_code == 401)
        check("deactivated user's old token -> 401", (await c.get("/auth/me", headers=FA)).status_code == 401)
        check("deactivated user not in active list", "fa.one@example.com" not in {u["email"] for u in (await c.get("/admin/users", headers=H)).json()})

    async with SessionLocal() as db:
        await db.execute(text("delete from users where email in ('fa.one@example.com','ftm.one@example.com')"))
        await db.execute(text("delete from customers where customer_name like 'Zz %'")); await db.commit()
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")
asyncio.run(main())
